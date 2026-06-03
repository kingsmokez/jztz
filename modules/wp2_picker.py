"""尾盘选股模块 - 输出格式与wp2_pick.html模板完全兼容

基于旧版 web_app.py 的 WP2 选股逻辑恢复：
- MA 多级对齐过滤
- 成交量突破过滤
- RSI/MACD 增强条件
- 自定义评分 _wp2_calc_score
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import akshare as ak

from modules.http_client import EM_HEADERS, session
from modules.logger import log
from modules.data_fetcher import get_stock_industry

# ---------------------------------------------------------------------------
# 模块级缓存
# ---------------------------------------------------------------------------
_WP2_PICK_DATA: dict = {
    "stocks": [],
    "pick_time": None,
    "last_update": None,
    "filter_stats": [],
    "market_info": {},
    "running": False,
}
_WP2_PICK_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def run_wp2_picker(
    top_n: int = 20,
    min_cap: int = 30,
    max_cap: int = 300,
    min_amt: int = 3,
    vol_mul: float = 1.5,
    break_n: int = 20,
    body_r: float = 0.6,
    rsi_lo: float = 40,
    rsi_hi: float = 75,
    min_score: int = 25,
) -> list[dict]:
    """执行尾盘选股，返回与模板兼容的股票列表

    模板期望字段:
    - code, name, price, ch(涨跌幅), cap(市值), amt(成交额)
    - vr(量比), ma(MA信号), rsi, macd(MACD值), score
    """
    with _WP2_PICK_LOCK:
        _WP2_PICK_DATA["running"] = True

    filter_log: list[dict] = []

    try:
        now = datetime.now()
        log.info(f"执行尾盘强势股选股... {now.strftime('%H:%M:%S')}")

        # 1. 获取大盘信息
        market_info = _get_market_info()

        # 2. 获取股票代码列表
        try:
            code_df = ak.stock_info_a_code_name()
        except Exception as exc:
            log.warning(f"akshare 获取失败: {exc}")
            _save_empty_result(now, market_info, filter_log)
            return []

        log.info(f"共 {len(code_df)} 只股票")

        # 3. 构建新浪代码
        sina_codes: list[str] = []
        for _, row in code_df.iterrows():
            code = str(row["code"])
            if code.startswith("688") or code.startswith("8") or code.startswith("4"):
                continue
            sina_codes.append(f"sh{code}" if code.startswith("6") else f"sz{code}")

        # 4. 批量获取行情
        all_data: dict[str, dict] = {}
        batch_size = 500
        for i in range(0, len(sina_codes), batch_size):
            batch = sina_codes[i : i + batch_size]
            batch_data = _wp2_get_sina_quote(batch)
            all_data.update(batch_data)
            time.sleep(0.3)

        # 5. 批量获取市值
        cap_data: dict[str, float] = {}
        for i in range(0, len(sina_codes), batch_size):
            batch = sina_codes[i : i + batch_size]
            batch_caps = _wp2_get_tencent_market_cap(batch)
            cap_data.update(batch_caps)
            time.sleep(0.3)

        for code, info in all_data.items():
            cap_yi = cap_data.get(code, 0)
            info["f21"] = cap_yi * 1e8

        all_stocks = list(all_data.values())
        log.info(f"获取行情数据 {len(all_stocks)} 只")

        # 6. 第一层：基础过滤
        s1 = _base_filter(all_stocks, min_cap, max_cap, min_amt, filter_log)
        if not s1:
            _save_empty_result(now, market_info, filter_log)
            return []

        s1.sort(key=lambda x: float(x.get("f6", 0)), reverse=True)

        # 7. 获取 K 线数据
        log.info(f"获取 {len(s1)} 只K线...")
        kline_data = _fetch_klines(s1)

        # 8. 多层技术过滤 + 评分
        results = _technical_filter_and_score(
            s1, kline_data, vol_mul, break_n, body_r, rsi_lo, rsi_hi, min_score, filter_log
        )

        results.sort(key=lambda x: x["score"], reverse=True)
        final = results[:top_n]

        # 添加行业信息
        if final:
            from concurrent.futures import ThreadPoolExecutor
            def _fetch_industry(stock):
                try:
                    info = get_stock_industry(stock["code"])
                    stock["industry"] = info.get("industry", "未知")
                    stock["sector"] = info.get("sector_type", "default")
                except Exception:
                    stock["industry"] = "未知"
                    stock["sector"] = "default"
            with ThreadPoolExecutor(max_workers=10) as executor:
                list(executor.map(_fetch_industry, final))

        log.info(f"尾盘选股完成: {len(final)} 只")

        with _WP2_PICK_LOCK:
            _WP2_PICK_DATA["date"] = now.strftime("%Y-%m-%d")
            _WP2_PICK_DATA["stocks"] = final
            _WP2_PICK_DATA["pick_time"] = now.strftime("%H:%M:%S")
            _WP2_PICK_DATA["last_update"] = now.strftime("%Y-%m-%d %H:%M:%S")
            _WP2_PICK_DATA["filter_stats"] = filter_log
            _WP2_PICK_DATA["market_info"] = market_info
            _WP2_PICK_DATA["running"] = False

        return final

    except Exception as exc:
        log.error(f"尾盘选股失败: {exc}")
        with _WP2_PICK_LOCK:
            _WP2_PICK_DATA["running"] = False
        return []


def get_wp2_pick_data() -> dict:
    """获取当前 WP2 选股缓存数据（供路由使用）"""
    with _WP2_PICK_LOCK:
        return {
            "stocks": _WP2_PICK_DATA.get("stocks", []),
            "pick_time": _WP2_PICK_DATA.get("pick_time"),
            "last_update": _WP2_PICK_DATA.get("last_update"),
            "filter_stats": _WP2_PICK_DATA.get("filter_stats", []),
            "market_info": _WP2_PICK_DATA.get("market_info", {}),
            "running": _WP2_PICK_DATA.get("running", False),
        }


def set_wp2_running(running: bool = True) -> None:
    """设置运行状态"""
    with _WP2_PICK_LOCK:
        _WP2_PICK_DATA["running"] = running


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _get_market_info() -> dict:
    """获取大盘信息"""
    try:
        import requests
        ir = session.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": "1.000300", "fields": "f43,f60,f170"},
            headers=EM_HEADERS,
            timeout=10,
        )
        if ir.status_code == 200:
            idata = ir.json().get("data", {})
            if idata:
                return {
                    "idx_price": idata.get("f43", 0),
                    "idx_open": idata.get("f60", 0),
                    "idx_change": idata.get("f170", 0),
                }
    except Exception:
        pass
    return {}


def _save_empty_result(
    now: datetime, market_info: dict, filter_log: list[dict]
) -> None:
    """保存空结果"""
    with _WP2_PICK_LOCK:
        _WP2_PICK_DATA["stocks"] = []
        _WP2_PICK_DATA["pick_time"] = now.strftime("%H:%M:%S")
        _WP2_PICK_DATA["last_update"] = now.strftime("%Y-%m-%d %H:%M:%S")
        _WP2_PICK_DATA["filter_stats"] = filter_log or [{"n": "akshare", "b": 0, "a": 0}]
        _WP2_PICK_DATA["market_info"] = market_info
        _WP2_PICK_DATA["running"] = False


def _base_filter(
    all_stocks: list[dict],
    min_cap: int,
    max_cap: int,
    min_amt: int,
    filter_log: list[dict],
) -> list[dict]:
    """第一层：基础过滤"""
    s1: list[dict] = []
    for s in all_stocks:
        c = str(s.get("f12", ""))
        n = str(s.get("f14", ""))
        if c.startswith("688") or c.startswith("8") or c.startswith("4"):
            continue
        if "ST" in n or "退" in n or "*" in n:
            continue
        cap = float(s.get("f21", 0))
        if not cap or cap < min_cap * 1e8 or cap > max_cap * 1e8:
            continue
        amt = float(s.get("f6", 0))
        # 非交易时间成交额可能为0，用换手率替代判断活跃度
        turnover = float(s.get("f8", 0))
        if amt and amt < min_amt * 1e8 and turnover < 0.3:
            continue
        if not s.get("f2") or s.get("f2") == "-":
            continue
        s1.append(s)

    filter_log.append({"n": "基础过滤", "b": len(all_stocks), "a": len(s1)})
    log.info(f"第1层 基础过滤: {len(all_stocks)}->{len(s1)}")
    return s1


def _fetch_klines(s1: list[dict]) -> dict[str, dict]:
    """获取K线数据"""
    kline_data: dict[str, dict] = {}
    for i in range(0, len(s1), 50):
        batch = s1[i : i + 50]
        for s in batch:
            code = str(s.get("f12", ""))
            market = str(s.get("f13", "1"))
            symbol = f"sh{code}" if market == "1" else f"sz{code}"
            kd = _wp2_get_tencent_kline(symbol, 60)
            if kd:
                kline_data[code] = kd
            time.sleep(0.05)
    return kline_data


def _technical_filter_and_score(
    s1: list[dict],
    kline_data: dict[str, dict],
    vol_mul: float,
    break_n: int,
    body_r: float,
    rsi_lo: float,
    rsi_hi: float,
    min_score: int,
    filter_log: list[dict],
) -> list[dict]:
    """多层技术评分（替代全硬过滤）

    每个条件贡献分数而非一刀切，最终按总分排序：
    - MA 对齐: 最多 20 分
    - 成交量突破: 最多 15 分
    - 价格突破: 最多 10 分
    - K 线实体比: 最多 5 分
    - RSI 安全区: 最多 15 分
    - MACD 正向: 最多 10 分
    合计满分 75 分，最低门槛 min_score（默认 25 分）
    """
    results: list[dict] = []
    # 统计各维度得分分布
    stat_ma = stat_vol = stat_brk = stat_rsi = stat_mc = 0

    for s in s1:
        code = str(s.get("f12", ""))
        kd = kline_data.get(code)
        if not kd or not kd.get("data") or not kd.get("data", {}).get("klines"):
            continue
        klines = kd["data"]["klines"]
        if len(klines) < 25:
            continue

        # 解析K线
        kl: list[dict] = []
        for line in klines:
            parts = line.split(",")
            if len(parts) >= 6:
                kl.append(
                    {
                        "o": float(parts[1]),
                        "c": float(parts[2]),
                        "h": float(parts[3]),
                        "lo": float(parts[4]),
                        "v": float(parts[5]),
                    }
                )
        kl = [k for k in kl if k["c"] > 0]
        if len(kl) < 25:
            continue

        cl = [k["c"] for k in kl]
        hi = [k["h"] for k in kl]
        vl = [k["v"] for k in kl]
        t = len(kl) - 1

        # MA 计算
        m5 = _wp2_calc_ma(cl, 5)
        m10 = _wp2_calc_ma(cl, 10)
        m20 = _wp2_calc_ma(cl, 20)
        m60 = _wp2_calc_ma(cl, 60)

        if not m5 or not m10 or not m20 or not m60:
            continue

        tech_score = 0.0

        # --- MA 对齐评分 (最多 20 分) ---
        ma_pts = _ma_alignment_score(m5, m10, m20, m60, cl[t])
        tech_score += ma_pts
        if ma_pts >= 12:
            stat_ma += 1

        # --- 成交量突破评分 (最多 15 分) ---
        vol_pts = _volume_breakout_score(vl, t, vol_mul)
        tech_score += vol_pts
        if vol_pts >= 8:
            stat_vol += 1

        # --- 价格突破评分 (最多 10 分) ---
        hn = 0
        for j in range(t - 1, max(0, t - break_n) - 1, -1):
            hn = max(hn, hi[j])
        brk_pts = 10 if cl[t] > hn else 0
        tech_score += brk_pts
        if brk_pts > 0:
            stat_brk += 1

        # --- K 线实体比评分 (最多 5 分) ---
        body = cl[t] - kl[t]["o"]
        rng = hi[t] - kl[t]["lo"]
        body_pts = 5 if (rng > 0 and body / rng > body_r) else 0
        tech_score += body_pts

        # --- RSI 安全区评分 (最多 15 分) ---
        rsi = _wp2_calc_rsi(cl)
        rsi_pts = 0
        if rsi is not None:
            rsi_pts = _rsi_score(rsi, rsi_lo, rsi_hi)
        tech_score += rsi_pts
        if rsi_pts >= 8:
            stat_rsi += 1

        # --- MACD 正向评分 (最多 10 分) ---
        mc = _wp2_calc_macd(cl)
        macd_pts = 0
        if mc and mc["dif"] is not None and mc["dea"] is not None and mc["macd"] is not None:
            macd_pts = _macd_score(mc)
        tech_score += macd_pts
        if macd_pts >= 5:
            stat_mc += 1

        # 最低分数门槛
        if tech_score < min_score:
            continue

        # 保留 _ma_alignment_filter 的布尔结果用于 ma 显示字段
        ma_aligned = _ma_alignment_filter(m5, m10, m20, m60, cl[t])

        # 构建结果
        stock_data = {
            "vr": float(s.get("f10", 0)),
            "ch": float(s.get("f3", 0)),
            "rsi": rsi if rsi is not None else 0,
            "cap": float(s.get("f21", 0)),
        }
        base_score = _wp2_calc_score(stock_data)

        # 技术评分 + 基础评分加权合并: 技术分占 60%，基础分占 40%
        combined_score = round(tech_score * 0.6 + base_score * 0.4, 1)

        results.append(
            {
                "code": code,
                "name": s.get("f14", ""),
                "price": round(cl[t], 2),
                "ch": round(float(s.get("f3", 0)), 2),
                "amt": round(float(s.get("f6", 0)), 2),
                "cap": round(float(s.get("f21", 0)), 2),
                "vr": round(float(s.get("f10", 0)), 2),
                "to": round(float(s.get("f8", 0)), 2),
                "ma": f"{m5:.1f}>{m10:.1f}" if ma_aligned else f"{m5:.1f}/{m10:.1f}",
                "rsi": round(rsi, 1) if rsi is not None else 0,
                "macd": round(mc["macd"], 4) if mc and mc["macd"] is not None else 0,
                "score": combined_score,
                "tech_score": tech_score,
            }
        )

    filter_log.append({"n": "MA对齐(>=12)", "b": len(s1), "a": stat_ma})
    filter_log.append({"n": "量能(>=8)", "b": len(s1), "a": stat_vol})
    filter_log.append({"n": "价格突破", "b": len(s1), "a": stat_brk})
    filter_log.append({"n": "RSI安全(>=8)", "b": len(s1), "a": stat_rsi})
    filter_log.append({"n": "MACD正向(>=5)", "b": len(s1), "a": stat_mc})
    filter_log.append({"n": f"达标(>={min_score})", "b": len(s1), "a": len(results)})

    return results


# ---------------------------------------------------------------------------
# 过滤条件
# ---------------------------------------------------------------------------

def _ma_alignment_filter(m5: float, m10: float, m20: float, m60: float, price: float) -> bool:
    """MA 多级对齐过滤

    - MA5 > MA10 > MA20 > MA60 = 强多头对齐（优先）
    - MA5 > MA10 > MA20 = 中等对齐
    - 价格应在 MA5 之上
    - MA5 < MA20 则跳过（空头）
    """
    # 空头排除
    if m5 < m20:
        return False
    # 价格应在 MA5 之上
    if price <= m5:
        return False
    # 强多头：MA5 > MA10 > MA20 > MA60
    if m5 > m10 > m20 > m60:
        return True
    # 中等对齐：MA5 > MA10 > MA20
    if m5 > m10 > m20:
        return True
    return False


def _ma_alignment_score(m5: float, m10: float, m20: float, m60: float, price: float) -> float:
    """MA 对齐评分 (最多 20 分)

    - 强多头 MA5>MA10>MA20>MA60 + 价格>MA5: 20 分
    - 中等对齐 MA5>MA10>MA20 + 价格>MA5: 12 分
    - 弱多头 MA5>MA20 但非完整对齐 + 价格>MA5: 5 分
    - 空头 (MA5<MA20) 或价格<MA5: 0 分
    """
    # 空头排除：不得分
    if m5 < m20:
        return 0
    # 价格应在 MA5 之上
    if price <= m5:
        return 0
    # 强多头：MA5 > MA10 > MA20 > MA60
    if m5 > m10 > m20 > m60:
        return 20
    # 中等对齐：MA5 > MA10 > MA20
    if m5 > m10 > m20:
        return 12
    # 弱多头：MA5 > MA20 但排列不完整
    return 5


def _volume_breakout_filter(vl: list[float], t: int, vol_mul: float) -> bool:
    """成交量突破过滤

    - 量比 > 1.5（显著放量）
    - 或成交量 > 1.5 * 5日平均成交量
    - 或量比 > 2.0（非常显著，即使价格走平）
    """
    if t < 5:
        return False

    current_vol = vl[t]
    prev_vol = vl[t - 1] if t > 0 else 1
    avg_5d = sum(vl[t - 5 : t]) / 5 if t >= 5 else 1

    # 量比近似（当日 / 前一日）
    vol_ratio = current_vol / max(prev_vol, 1)

    # 条件1：量比 > 1.5
    if vol_ratio > 1.5:
        return True

    # 条件2：成交量 > 1.5 * 5日平均
    if current_vol > vol_mul * avg_5d:
        return True

    # 条件3：量比 > 2.0（非常显著）
    if vol_ratio > 2.0:
        return True

    return False


def _volume_breakout_score(vl: list[float], t: int, vol_mul: float) -> float:
    """成交量突破评分 (最多 15 分)

    - 强放量：量比 > 2.0 或 成交量 > 2x 5日均量: 15 分
    - 中等放量：量比 > 1.5 或 成交量 > 1.5x 5日均量: 8 分
    - 无明显放量: 0 分
    """
    if t < 5:
        return 0

    current_vol = vl[t]
    prev_vol = vl[t - 1] if t > 0 else 1
    avg_5d = sum(vl[t - 5 : t]) / 5 if t >= 5 else 1

    vol_ratio = current_vol / max(prev_vol, 1)

    # 强放量
    if vol_ratio > 2.0 or current_vol > 2.0 * avg_5d:
        return 15
    # 中等放量
    if vol_ratio > 1.5 or current_vol > vol_mul * avg_5d:
        return 8

    return 0


def _rsi_score(rsi: float, rsi_lo: float = 40, rsi_hi: float = 75) -> float:
    """RSI 安全区评分 (最多 15 分)

    - 最优区 (50-65): 15 分
    - 扩展区 (40-50 或 65-75): 8 分
    - 安全区外: 0 分
    """
    if 50 <= rsi <= 65:
        return 15
    if rsi_lo <= rsi < 50 or 65 < rsi <= rsi_hi:
        return 8
    return 0


def _macd_score(mc: dict) -> float:
    """MACD 正向评分 (最多 10 分)

    - DIF > DEA: 5 分
    - MACD > 0: 5 分
    """
    pts = 0.0
    if mc["dif"] > mc["dea"]:
        pts += 5
    if mc["macd"] > 0:
        pts += 5
    return pts


# ---------------------------------------------------------------------------
# 评分函数
# ---------------------------------------------------------------------------

def _wp2_calc_score(stock: dict) -> float:
    """WP2 自定义评分 (0-100)

    Score components:
    - vr (量比): weight 30%
    - ch (涨跌幅): weight 25%
    - rsi (RSI): weight 25%
    - cap (市值): weight 20%
    """
    sc = 0
    vr = stock.get("vr", 0)
    ch = stock.get("ch", 0)
    rsi = stock.get("rsi", 0)
    cap = stock.get("cap", 0) / 1e8

    # 量比评分 (0-30)
    if vr >= 2.5:
        sc += 30
    elif vr >= 2:
        sc += 25
    elif vr >= 1.5:
        sc += 18
    else:
        sc += 10

    # 涨跌幅评分 (0-25)
    if 3 <= ch <= 6:
        sc += 25
    elif ch >= 2:
        sc += 20
    elif ch >= 1:
        sc += 15
    else:
        sc += 5

    # RSI 评分 (0-25)
    if 55 <= rsi <= 65:
        sc += 25
    elif 50 <= rsi <= 70:
        sc += 20
    else:
        sc += 10

    # 市值评分 (0-20)
    if 50 <= cap <= 200:
        sc += 20
    elif 30 <= cap <= 300:
        sc += 15
    else:
        sc += 8

    return min(sc, 100)


# ---------------------------------------------------------------------------
# 技术指标计算（旧版兼容）— 代理到 modules.technical.calc_*
# ---------------------------------------------------------------------------
from modules.technical import calc_ma, calc_ema, calc_rsi, calc_macd


def _wp2_calc_ma(prices: list[float], period: int) -> Optional[float]:
    """计算简单移动平均 (代理到 modules.technical.calc_ma)"""
    series = calc_ma(prices, period)
    return series[-1] if series else None


def _wp2_calc_ema(prices: list[float], period: int) -> Optional[float]:
    """计算指数移动平均 (代理到 modules.technical.calc_ema)"""
    series = calc_ema(prices, period)
    return series[-1] if series else None


def _wp2_calc_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """计算 RSI (代理到 modules.technical.calc_rsi)"""
    series = calc_rsi(prices, period)
    return series[-1] if series else None


def _wp2_calc_macd(prices: list[float]) -> Optional[dict]:
    """计算 MACD (代理到 modules.technical.calc_macd)"""
    result = calc_macd(prices)
    if not result or not result.get("dif"):
        return None
    return {
        "dif": result["dif"][-1] if result.get("dif") else None,
        "dea": result["dea"][-1] if result.get("dea") else None,
        "macd": result["macd"][-1] if result.get("macd") else None,
    }


# ---------------------------------------------------------------------------
# 数据获取函数
# ---------------------------------------------------------------------------

def _wp2_get_sina_quote(codes: list[str]) -> dict[str, dict]:
    """从新浪获取行情数据"""
    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
    try:
        r = session.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/",
            },
            timeout=15,
        )
        stocks: dict[str, dict] = {}
        pattern = r'var hq_(\w+)="(.*?)";'
        for match in re.finditer(pattern, r.text):
            raw_code = match.group(1)
            content = match.group(2)
            if not content:
                continue
            fields = content.split(",")
            if len(fields) < 32:
                continue
            try:
                price = float(fields[3])
                pre_close = float(fields[2])
                open_price = float(fields[1])
                high = float(fields[4])
                low = float(fields[5])
                volume = int(fields[8])
                amount = float(fields[9])
                pct_change = (
                    round((price - pre_close) / pre_close * 100, 2)
                    if pre_close > 0
                    else 0
                )
                # 从新浪变量名提取纯代码（去掉所有前缀如hq_/r_/str_/sh/sz）
                # 可能格式: hq_sh600519, r_sz000001, str_sh600519 等
                clean = raw_code
                for prefix in ("hq_", "r_", "str_", "s_"):
                    if clean.startswith(prefix):
                        clean = clean[len(prefix):]
                        break
                if clean.startswith("sh") or clean.startswith("sz"):
                    pure_code = clean[2:]
                else:
                    pure_code = clean
                market = "1" if "sh" in raw_code else "0"
                try:
                    turnover_rate = (
                        float(fields[30]) if len(fields) > 30 and fields[30] else 0
                    )
                except ValueError:
                    turnover_rate = 0
                try:
                    pe_val = float(fields[31]) if len(fields) > 31 and fields[31] else 0
                except ValueError:
                    pe_val = 0
                avg_vol_5 = 0
                if len(fields) > 8 and volume > 0:
                    avg_vol_5 = (
                        volume / max(turnover_rate / 5, 0.1)
                        if turnover_rate > 0
                        else 0
                    )
                vol_ratio = round(volume / avg_vol_5, 2) if avg_vol_5 > 0 else 0
                stocks[pure_code] = {
                    "f2": price,
                    "f3": pct_change,
                    "f4": round(price - pre_close, 2) if pre_close > 0 else 0,
                    "f5": volume,
                    "f6": amount,
                    "f7": (
                        round((high - low) / pre_close * 100, 2)
                        if pre_close > 0
                        else 0
                    ),
                    "f8": turnover_rate,
                    "f9": pe_val,
                    "f10": vol_ratio,
                    "f12": pure_code,
                    "f13": market,
                    "f14": fields[0],
                    "f15": high,
                    "f16": low,
                    "f17": open_price,
                    "f18": pre_close,
                    "f20": 0,
                    "f21": 0,
                    "f23": pe_val,
                }
            except (ValueError, IndexError):
                continue
        return stocks
    except Exception as e:
        log.warning(f"新浪行情获取失败: {e}")
        return {}


def _wp2_get_tencent_market_cap(codes: list[str]) -> dict[str, float]:
    """从腾讯获取市值数据"""
    url = f"https://qt.gtimg.cn/q={','.join(codes)}"
    try:
        r = session.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/",
            },
            timeout=15,
        )
        caps: dict[str, float] = {}
        pattern = r'v_(\w+)="(.*?)";'
        for match in re.finditer(pattern, r.text):
            full_code = match.group(1)
            content = match.group(2)
            if not content:
                continue
            fields = content.split("~")
            pure_code = full_code[2:]
            try:
                circ_mv = float(fields[45]) if len(fields) > 45 and fields[45] else 0
                caps[pure_code] = circ_mv
            except (ValueError, IndexError):
                caps[pure_code] = 0
        return caps
    except Exception as e:
        log.warning(f"腾讯市值获取失败: {e}")
        return {}


def _wp2_get_tencent_kline(symbol: str, count: int = 60) -> Optional[dict]:
    """从腾讯获取K线数据"""
    try:
        r = session.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": f"{symbol},day,,,{count},qfq"},
            timeout=10,
        )
        d = r.json()
        if d.get("code") != 0:
            return None
        data = d.get("data", {})
        stock_key = list(data.keys())[0] if data else None
        if not stock_key:
            return None
        qfqday = data[stock_key].get("qfqday") or data[stock_key].get("day") or []
        klines: list[str] = []
        for row in qfqday:
            if len(row) >= 6:
                klines.append(f"{row[0]},{row[1]},{row[2]},{row[3]},{row[4]},{row[5]}")
        return {"data": {"klines": klines}}
    except Exception as e:
        log.debug(f"腾讯K线获取失败: {symbol}, {e}")
        return None
