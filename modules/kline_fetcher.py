"""统一K线数据获取模块 — 支持多数据源自动降级

数据源优先级:
1. 腾讯 (web.ifzq.gtimg.cn) — 速度快，但容易被WAF封
2. 新浪 (money.finance.sina.com.cn) — 稳定，GBK编码
3. 东方财富 (push2his.eastmoney.com) — 官方API

健康检测机制:
- 每个源维护连续失败计数
- 连续失败超过阈值 → 标记不健康，不再尝试
- 不健康源 5 分钟后自动恢复重试资格
- 任何一次成功 → 立即重置失败计数
"""

from __future__ import annotations

import json
import threading
import time
from typing import Optional

from modules.http_client import EM_HEADERS, session
from modules.logger import log


class KlineFetcher:
    """统一K线获取，支持多数据源自动降级"""

    # 数据源优先级
    SOURCES = ("tencent", "sina", "eastmoney")
    # 连续失败阈值：超过此数标记不健康
    FAIL_THRESHOLD = 10
    # 不健康源恢复时间（秒）
    RECOVER_TIME = 300
    # 最少K线条数（少于此数视为失败）
    MIN_KLINES = 15

    def __init__(self) -> None:
        self._fail_counts: dict[str, int] = {s: 0 for s in self.SOURCES}
        self._mark_time: dict[str, float] = {s: 0.0 for s in self.SOURCES}
        self._lock = threading.Lock()
        # 统计
        self._stats: dict[str, dict[str, int]] = {s: {"ok": 0, "fail": 0} for s in self.SOURCES}

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def get_kline(self, code: str, count: int = 120) -> Optional[list[dict]]:
        """获取日K线数据，自动降级: 腾讯 → 新浪 → 东方财富

        Args:
            code: 纯数字代码，如 "600519"
            count: 请求K线条数

        Returns:
            统一格式 [{"date","open","close","high","low","volume"}, ...] 按时间正序，
            或 None（所有源均失败）
        """
        for source_name in self.SOURCES:
            if not self._is_healthy(source_name):
                continue
            try:
                result = self._dispatch(source_name, code, count)
                if result and len(result) >= self.MIN_KLINES:
                    self._mark_success(source_name)
                    return result
            except Exception as e:
                log.debug(f"K线获取异常({source_name}): {code}, {e}")
            self._mark_fail(source_name, code)

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
                elif now - self._mark_time[s] >= self.RECOVER_TIME:
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

    def _is_healthy(self, source_name: str) -> bool:
        with self._lock:
            if self._fail_counts[source_name] < self.FAIL_THRESHOLD:
                return True
            # 超过恢复时间，给予一次重试机会
            if time.time() - self._mark_time[source_name] >= self.RECOVER_TIME:
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
            log.warning(f"K线源[{source_name}]连续失败{fc}次，标记为不健康 (5分钟后恢复)")
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
        if source_name == "tencent":
            return self._fetch_tencent(code, count)
        elif source_name == "sina":
            return self._fetch_sina(code, count)
        elif source_name == "eastmoney":
            return self._fetch_eastmoney(code, count)
        return None

    # ------------------------------------------------------------------
    # 腾讯 K线
    # ------------------------------------------------------------------

    def _fetch_tencent(self, code: str, count: int) -> Optional[list[dict]]:
        """腾讯日K线 (前复权)"""
        prefix = "sh" if code.startswith("6") else "sz"
        symbol = f"{prefix}{code}"
        try:
            r = session.get(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                params={"param": f"{symbol},day,,,{count},qfq"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
                timeout=10,
            )
            if r.status_code != 200:
                log.debug(f"腾讯K线HTTP错误: {code}, status={r.status_code}")
                return None
            d = r.json()
            if d.get("code") != 0:
                return None
            data = d.get("data", {})
            stock_key = list(data.keys())[0] if data else None
            if not stock_key:
                return None
            qfqday = data[stock_key].get("qfqday") or data[stock_key].get("day") or []
            return self._parse_tencent_klines(qfqday)
        except Exception as e:
            log.debug(f"腾讯K线获取失败: {code}, {e}")
            return None

    @staticmethod
    def _parse_tencent_klines(rows: list) -> list[dict]:
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

    # ------------------------------------------------------------------
    # 新浪 K线
    # ------------------------------------------------------------------

    def _fetch_sina(self, code: str, count: int) -> Optional[list[dict]]:
        """新浪日K线"""
        prefix = "sh" if code.startswith("6") else "sz"
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
                log.debug(f"新浪K线HTTP错误: {code}, status={r.status_code}")
                return None
            text = r.content.decode("gbk", errors="replace")
            data = json.loads(text)
            if not data or not isinstance(data, list):
                return None
            return self._parse_sina_klines(data)
        except Exception as e:
            log.debug(f"新浪K线获取失败: {code}, {e}")
            return None

    @staticmethod
    def _parse_sina_klines(rows: list) -> list[dict]:
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

    # ------------------------------------------------------------------
    # 东方财富 K线
    # ------------------------------------------------------------------

    def _fetch_eastmoney(self, code: str, count: int) -> Optional[list[dict]]:
        """东方财富日K线 (前复权)"""
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
                log.debug(f"东方财富K线HTTP错误: {code}, status={r.status_code}")
                return None
            d = r.json()
            klines = d.get("data", {}).get("klines", [])
            if not klines:
                return None
            return self._parse_eastmoney_klines(klines)
        except Exception as e:
            log.debug(f"东方财富K线获取失败: {code}, {e}")
            return None

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


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------
kline_fetcher = KlineFetcher()
