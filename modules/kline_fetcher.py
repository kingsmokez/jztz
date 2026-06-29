"""统一K线数据获取模块 — 6源互备 + 快速自动切换

数据源优先级 (按速度和稳定性排序):
1. 腾讯 kline   (web.ifzq.gtimg.cn/appstock/app/kline/kline) — 不复权，最快
2. 新浪 K线     (money.finance.sina.com.cn) — 不复权，稳定
3. 同花顺 K线   (d.10jqka.com.cn) — 不复权，数据丰富
4. 腾讯 fqkline (web.ifzq.gtimg.cn/appstock/app/fqkline/get) — 前复权，WAF可能拦截
5. 东方财富     (push2his.eastmoney.com) — 前复权，连接不稳定
6. 雪球 K线     (stock.xueqiu.com) — 前复权，需cookie

健康检测机制:
- 每个源维护连续失败计数
- 连续失败超过阈值(10次) → 标记不健康
- 不健康源渐进式恢复 (60s → 120s → 240s → 480s → 600s)
- 任何一次成功 → 立即重置失败计数
- 每个源独立健康状态，互不影响
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Optional

from modules.http_client import EM_HEADERS, session
from modules.logger import log


class KlineFetcher:
    """统一K线获取，6源互备 + 快速自动切换"""

    # 数据源优先级列表
    SOURCES = ("tencent_kline", "sina", "10jqka", "tencent_fqkline", "eastmoney", "xueqiu")

    # 连续失败阈值：超过此数标记不健康
    FAIL_THRESHOLD = 10
    # 基础恢复时间（秒），渐进式增长
    RECOVER_BASE = 60
    # 最少K线条数（少于此数视为失败）
    MIN_KLINES = 15

    def __init__(self) -> None:
        self._fail_counts: dict[str, int] = {s: 0 for s in self.SOURCES}
        self._mark_time: dict[str, float] = {s: 0.0 for s in self.SOURCES}
        self._lock = threading.Lock()
        # 统计
        self._stats: dict[str, dict[str, int]] = {s: {"ok": 0, "fail": 0} for s in self.SOURCES}
        # 雪球专用 session（需要独立 cookie）
        self._xueqiu_session = None
        self._xueqiu_cookie_time = 0.0

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def get_kline(self, code: str, count: int = 120) -> Optional[list[dict]]:
        """获取日K线数据，6源自动切换

        Args:
            code: 纯数字代码，如 "600519"
            count: 请求K线条数

        Returns:
            统一格式 [{"date","open","close","high","low","volume"}, ...] 按时间正序，
            或 None（所有源均失败）
        """
        empty_count = 0  # 连续返回空列表的源计数（区分"源故障"和"股票无数据"）

        for source_name in self.SOURCES:
            if not self._is_healthy(source_name):
                continue
            try:
                result = self._dispatch(source_name, code, count)
                if result and len(result) >= self.MIN_KLINES:
                    self._mark_success(source_name)
                    return result
                # 有数据但不够 MIN_KLINES — 次新股，宽容返回
                if result and len(result) > 0:
                    self._mark_success(source_name)
                    return result
                # 源正常响应但返回空列表 → 这只股票本身无K线数据
                if result is not None and len(result) == 0:
                    empty_count += 1
                # 源故障（None）→ 标记失败
            except Exception as e:
                log.debug(f"K线获取异常({source_name}): {code}, {e}")
                self._mark_fail(source_name, code)

            # 快速退出：如果前2个主源（腾讯kline + 新浪）都返回空列表，
            # 说明这只股票确实没有K线数据（次新股/待上市/已退市），
            # 继续尝试剩余源只是浪费时间
            if empty_count >= 2:
                log.debug(f"K线数据不存在(次新/待上市/退市): {code}, {empty_count}个主源无数据")
                return None

        # 所有源均失败
        log.warning(f"K线获取全部失败: {code}, 源状态={self._health_summary()}")
        return None

    def get_kline_raw(self, symbol: str, count: int = 120) -> Optional[dict]:
        """获取K线，返回 wp2 兼容格式 {"data": {"klines": ["date,o,c,h,l,v", ...]}}

        Args:
            symbol: 带市场前缀代码，如 "sh600519"
            count: 请求K线条数
        """
        code = symbol[2:] if len(symbol) > 2 else symbol
        result = self.get_kline(code, count)
        if not result:
            return None
        klines = [
            f"{d['date']},{d['open']},{d['close']},{d['high']},{d['low']},{d['volume']}"
            for d in result
        ]
        return {"data": {"klines": klines}}

    def health_status(self) -> dict[str, str]:
        """返回各源健康状态，供调试/展示"""
        with self._lock:
            now = time.time()
            status = {}
            for s in self.SOURCES:
                if self._fail_counts[s] < self.FAIL_THRESHOLD:
                    status[s] = "healthy"
                elif now - self._mark_time[s] >= self._recover_interval(s):
                    status[s] = "recovering"
                else:
                    status[s] = "unhealthy"
            return status

    def stats(self) -> dict[str, dict[str, int]]:
        """返回各源成功/失败统计"""
        with self._lock:
            return {s: dict(v) for s, v in self._stats.items()}

    def reset(self) -> None:
        """重置所有源健康状态（用于强制重试）"""
        with self._lock:
            for s in self.SOURCES:
                self._fail_counts[s] = 0
                self._mark_time[s] = 0.0

    # ------------------------------------------------------------------
    # 健康检测
    # ------------------------------------------------------------------

    def _recover_interval(self, source_name: str) -> float:
        """计算恢复间隔：渐进式增长，避免雪崩

        基础60秒，每多一轮翻倍: 60 → 120 → 240 → 480 → 600(上限)
        """
        fc = self._fail_counts.get(source_name, 0)
        if fc < self.FAIL_THRESHOLD:
            return self.RECOVER_BASE
        rounds = (fc - self.FAIL_THRESHOLD) // self.FAIL_THRESHOLD + 1
        return min(self.RECOVER_BASE * (2 ** (rounds - 1)), 600)

    def _is_healthy(self, source_name: str) -> bool:
        with self._lock:
            if self._fail_counts[source_name] < self.FAIL_THRESHOLD:
                return True
            if time.time() - self._mark_time[source_name] >= self._recover_interval(source_name):
                return True
            return False

    def _mark_success(self, source_name: str) -> None:
        with self._lock:
            self._fail_counts[source_name] = 0
            self._stats[source_name]["ok"] += 1

    def _mark_fail(self, source_name: str, code: str = "") -> None:
        with self._lock:
            self._fail_counts[source_name] += 1
            self._mark_time[source_name] = time.time()
            self._stats[source_name]["fail"] += 1
            fc = self._fail_counts[source_name]
        if fc == self.FAIL_THRESHOLD:
            log.warning(f"K线源[{source_name}]连续失败{fc}次，标记为不健康 (将渐进恢复)")
        elif fc % 50 == 0:
            log.debug(f"K线源[{source_name}]已累计失败{fc}次")

    def _health_summary(self) -> str:
        with self._lock:
            parts = []
            for s in self.SOURCES:
                fc = self._fail_counts[s]
                tag = "✓" if fc < self.FAIL_THRESHOLD else f"✗({fc})"
                parts.append(f"{s}={tag}")
            return " ".join(parts)

    # ------------------------------------------------------------------
    # 数据源分发
    # ------------------------------------------------------------------

    def _dispatch(self, source_name: str, code: str, count: int) -> Optional[list[dict]]:
        if source_name == "tencent_kline":
            return self._fetch_tencent_kline(code, count)
        elif source_name == "sina":
            return self._fetch_sina(code, count)
        elif source_name == "10jqka":
            return self._fetch_10jqka(code, count)
        elif source_name == "tencent_fqkline":
            return self._fetch_tencent_fqkline(code, count)
        elif source_name == "eastmoney":
            return self._fetch_eastmoney(code, count)
        elif source_name == "xueqiu":
            return self._fetch_xueqiu(code, count)
        return None

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _market_prefix(code: str) -> str:
        """返回沪深市场前缀: sh / sz"""
        return "sh" if code.startswith("6") else "sz"

    # ------------------------------------------------------------------
    # 源1: 腾讯 kline (不复权，最快最稳)
    # ------------------------------------------------------------------

    def _fetch_tencent_kline(self, code: str, count: int) -> Optional[list[dict]]:
        """腾讯日K线 (kline端点，不复权) — 主力源，速度快稳定性好"""
        prefix = self._market_prefix(code)
        symbol = f"{prefix}{code}"
        try:
            r = session.get(
                "https://web.ifzq.gtimg.cn/appstock/app/kline/kline",
                params={"param": f"{symbol},day,,,{count}"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            d = r.json()
            if d.get("code") != 0:
                return None
            data = d.get("data", {})
            stock_key = list(data.keys())[0] if data else None
            if not stock_key:
                return None
            day = data[stock_key].get("day") or []
            return self._parse_tencent_klines(day)
        except Exception as e:
            log.debug(f"腾讯K线获取失败: {code}, {e}")
            return None

    # ------------------------------------------------------------------
    # 源2: 新浪 K线 (不复权，稳定)
    # ------------------------------------------------------------------

    def _fetch_sina(self, code: str, count: int) -> Optional[list[dict]]:
        """新浪日K线 — 稳定备用源"""
        prefix = self._market_prefix(code)
        symbol = f"{prefix}{code}"
        try:
            url = (
                f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={count}"
            )
            r = session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            text = r.content.decode("gbk", errors="replace")
            data = json.loads(text)
            # 新浪返回 null 表示"有这只股票但无K线数据"(次新股/待上市)
            if data is None:
                return []
            if not isinstance(data, list):
                return None
            return self._parse_sina_klines(data)
        except Exception as e:
            log.debug(f"新浪K线获取失败: {code}, {e}")
            return None

    # ------------------------------------------------------------------
    # 源3: 同花顺 K线 (不复权，数据丰富)
    # ------------------------------------------------------------------

    def _fetch_10jqka(self, code: str, count: int) -> Optional[list[dict]]:
        """同花顺日K线 — 数据丰富，覆盖范围广

        返回JSONP格式，data字段为分号分隔的K线字符串:
        date,open,high,low,close,volume,amount,chg_pct,...,...;
        """
        try:
            r = session.get(
                f"http://d.10jqka.com.cn/v6/line/hs_{code}/01/last.js",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://stockpage.10jqka.com.cn/"},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            text = r.text
            # 解析 JSONP: callback_name({...})
            if '(' not in text or not text.endswith(')'):
                return None
            json_str = text[text.index('(') + 1:text.rindex(')')]
            d = json.loads(json_str)
            data = d.get("data")
            # total=0 且 data为空 → 有这只股票但无K线数据(次新股)
            total = d.get("total", 0)
            if data == "" or (total == 0 and data == ""):
                return []
            if not data or not isinstance(data, str):
                return None
            return self._parse_10jqka_klines(data, count)
        except Exception as e:
            log.debug(f"同花顺K线获取失败: {code}, {e}")
            return None

    # ------------------------------------------------------------------
    # 源4: 腾讯 fqkline (前复权，WAF可能拦截)
    # ------------------------------------------------------------------

    def _fetch_tencent_fqkline(self, code: str, count: int) -> Optional[list[dict]]:
        """腾讯前复权K线 (fqkline端点) — 可能被WAF拦截返回501"""
        prefix = self._market_prefix(code)
        symbol = f"{prefix}{code}"
        try:
            r = session.get(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                params={"param": f"{symbol},day,,,{count},qfq"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            d = r.json()
            if d.get("code") != 0:
                return None
            data = d.get("data", {})
            stock_key = list(data.keys())[0] if data else None
            if not stock_key:
                return None
            qfqday = data[stock_key].get("qfqday") or data[stock_key].get("day") or []
            # 返回空列表表示"有这只股票但无K线数据"
            if not qfqday:
                return []
            return self._parse_tencent_klines(qfqday)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 源5: 东方财富 (前复权，连接不稳定)
    # ------------------------------------------------------------------

    def _fetch_eastmoney(self, code: str, count: int) -> Optional[list[dict]]:
        """东方财富日K线 (前复权) — 连接不稳定，作为兜底"""
        market = "1" if code.startswith("6") else "0"
        secid = f"{market}.{code}"
        try:
            r = session.get(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                params={
                    "secid": secid,
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                    "klt": "101",   # 日K
                    "fqt": "1",     # 前复权
                    "lmt": str(count),
                },
                headers=EM_HEADERS,
                timeout=10,
            )
            if r.status_code != 200:
                return None
            d = r.json()
            klines = d.get("data", {}).get("klines", [])
            # 返回空列表表示"有这只股票但无K线数据"
            if klines is not None and len(klines) == 0:
                return []
            if not klines:
                return None
            return self._parse_eastmoney_klines(klines)
        except Exception as e:
            log.debug(f"东方财富K线获取失败: {code}, {e}")
            return None

    def get_minute_kline(self, code: str, minute: int = 30, count: int = 60):
        klt_map = {1:"1",5:"5",15:"15",30:"30",60:"60"}
        klt = klt_map.get(minute, "30")
        market = "1" if code.startswith("6") else "0"
        try:
            from modules.http_client import session, EM_HEADERS
            r = session.get("https://push2his.eastmoney.com/api/qt/stock/kline/get", params={
                "secid": f"{market}.{code}", "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
                "klt": klt, "fqt": "1", "lmt": str(count),
            }, headers=EM_HEADERS, timeout=10)
            if r.status_code != 200: return None
            d = r.json()
            klines = d.get("data", {}).get("klines", [])
            if not klines: return None
            return self._parse_eastmoney_klines(klines)
        except Exception as e:
            log.debug(f"分钟K线获取失败: {code}, {e}")
            return None

    # ------------------------------------------------------------------
    # 源6: 雪球 K线 (前复权，需cookie)
    # ------------------------------------------------------------------

    def _fetch_xueqiu(self, code: str, count: int) -> Optional[list[dict]]:
        """雪球日K线 (前复权) — 需要先获取cookie"""
        market = "SH" if code.startswith("6") else "SZ"
        symbol = f"{market}{code}"
        try:
            s = self._get_xueqiu_session()
            if s is None:
                return None
            r = s.get(
                f"https://stock.xueqiu.com/v5/stock/chart/kline.json",
                params={
                    "symbol": symbol,
                    "begin": 0,
                    "period": "day",
                    "type": "before",
                    "count": -count,
                },
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://xueqiu.com/"},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            d = r.json()
            data = d.get("data", {})
            items = data.get("item", [])
            columns = data.get("column", [])
            # 返回空列表表示"有这只股票但无K线数据"
            if items is not None and len(items) == 0:
                return []
            if not items or not columns:
                return None
            return self._parse_xueqiu_klines(items, columns)
        except Exception as e:
            log.debug(f"雪球K线获取失败: {code}, {e}")
            return None

    def _get_xueqiu_session(self):
        """获取雪球session (带有效cookie)"""
        now = time.time()
        if self._xueqiu_session and now - self._xueqiu_cookie_time < 1800:
            return self._xueqiu_session
        try:
            import requests as req
            s = req.Session()
            s.get(
                "https://xueqiu.com/",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=10,
            )
            self._xueqiu_session = s
            self._xueqiu_cookie_time = now
            return s
        except Exception:
            return None

    # ------------------------------------------------------------------
    # K线解析器
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_tencent_klines(rows: list) -> list[dict]:
        """腾讯K线格式: [date, open, close, high, low, volume]"""
        result = []
        for row in rows:
            if len(row) >= 6:
                try:
                    result.append({
                        "date": str(row[0]),
                        "open": float(row[1]),
                        "close": float(row[2]),
                        "high": float(row[3]),
                        "low": float(row[4]),
                        "volume": float(row[5]),
                    })
                except (ValueError, IndexError):
                    continue
        return result

    @staticmethod
    def _parse_sina_klines(rows: list) -> list[dict]:
        """新浪K线格式: {"day":"...", "open":"...", "close":"...", ...}"""
        result = []
        for d in rows:
            try:
                result.append({
                    "date": str(d.get("day", "")),
                    "open": float(d.get("open", 0)),
                    "close": float(d.get("close", 0)),
                    "high": float(d.get("high", 0)),
                    "low": float(d.get("low", 0)),
                    "volume": float(d.get("volume", 0)),
                })
            except (ValueError, KeyError):
                continue
        return result

    @staticmethod
    def _parse_10jqka_klines(data_str: str, count: int) -> list[dict]:
        """同花顺K线格式: date,open,high,low,close,volume,amount,...;...;...

        date格式: YYYYMMDD (需要转为 YYYY-MM-DD)
        """
        result = []
        days = data_str.split(";")
        # 取最后 count 条
        days = days[-count:] if len(days) > count else days
        for day_str in days:
            parts = day_str.split(",")
            if len(parts) >= 6:
                try:
                    raw_date = str(parts[0])
                    # 格式化日期: 20260618 → 2026-06-18
                    if len(raw_date) == 8 and raw_date.isdigit():
                        formatted = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                    else:
                        formatted = raw_date
                    result.append({
                        "date": formatted,
                        "open": float(parts[1]),
                        "close": float(parts[4]),
                        "high": float(parts[2]),
                        "low": float(parts[3]),
                        "volume": float(parts[5]),
                    })
                except (ValueError, IndexError):
                    continue
        return result

    @staticmethod
    def _parse_eastmoney_klines(rows: list) -> list[dict]:
        """东方财富K线格式: "date,open,close,high,low,volume,amount" 逗号分隔"""
        result = []
        for line in rows:
            parts = line.split(",")
            if len(parts) >= 6:
                try:
                    result.append({
                        "date": str(parts[0]),
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]),
                    })
                except (ValueError, IndexError):
                    continue
        return result

    @staticmethod
    def _parse_xueqiu_klines(items: list, columns: list) -> list[dict]:
        """雪球K线格式: items为二维数组，columns为字段名列表

        典型columns: [timestamp, volume, open, high, low, close,chg, percent, ...]
        """
        # 建立字段索引
        col_map = {}
        for i, col in enumerate(columns):
            col_map[col] = i

        result = []
        for item in items:
            try:
                # 雪球时间戳是毫秒
                ts = item[col_map.get("timestamp", 0)]
                if isinstance(ts, (int, float)):
                    from datetime import datetime
                    dt = datetime.fromtimestamp(ts / 1000)
                    date_str = dt.strftime("%Y-%m-%d")
                else:
                    date_str = str(ts)

                result.append({
                    "date": date_str,
                    "open": float(item[col_map.get("open", 2)]) if "open" in col_map else 0,
                    "close": float(item[col_map.get("close", 5)]) if "close" in col_map else 0,
                    "high": float(item[col_map.get("high", 3)]) if "high" in col_map else 0,
                    "low": float(item[col_map.get("low", 4)]) if "low" in col_map else 0,
                    "volume": float(item[col_map.get("volume", 1)]) if "volume" in col_map else 0,
                })
            except (ValueError, IndexError, KeyError):
                continue
        return result


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------
kline_fetcher = KlineFetcher()
