"""技术指标计算模块

提供以下指标计算:
    calc_ma / calc_ema / calc_macd / calc_rsi / calc_kdj / calc_boll
    evaluate_technical / calculate_technical_indicators
新增指标 (v2):
    calc_obv   — 能量潮 (On-Balance Volume)
    calc_vwap  — 成交量加权平均价 (Volume Weighted Average Price)
    calc_boll_width — 布林带宽度百分比 (Bollinger Band Width)
    calc_multi_rsi  — 多周期RSI (Multi-period RSI)
    evaluate_technical_score — 独立技术评分函数 (0-15)
"""

from __future__ import annotations

import math
from typing import Optional

from modules.logger import log


def calc_ma(prices: list[float], period: int) -> list[Optional[float]]:
    """计算移动平均线"""
    if len(prices) < period:
        return [None] * len(prices)
    result: list[Optional[float]] = [None] * (period - 1)
    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1 : i + 1]
        result.append(round(sum(window) / period, 2))
    return result


def calc_ema(prices: list[float], period: int) -> list[Optional[float]]:
    """计算指数移动平均线"""
    if len(prices) < period:
        return [None] * len(prices)
    k = 2 / (period + 1)
    result: list[Optional[float]] = [None] * (period - 1)
    ema = sum(prices[:period]) / period
    result.append(round(ema, 2))
    for i in range(period, len(prices)):
        ema = prices[i] * k + ema * (1 - k)
        result.append(round(ema, 2))
    return result


def calc_macd(
    prices: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, list[Optional[float]]]:
    """计算MACD"""
    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)

    dif: list[Optional[float]] = []
    for f, s in zip(ema_fast, ema_slow):
        if f is not None and s is not None:
            dif.append(round(f - s, 4))
        else:
            dif.append(None)

    dif_values = [d for d in dif if d is not None]
    dea_full = calc_ema(dif_values, signal) if len(dif_values) >= signal else [None] * len(dif_values)

    # 对齐dea长度
    dea: list[Optional[float]] = [None] * (len(dif) - len(dea_full)) + dea_full

    macd_hist: list[Optional[float]] = []
    for d, e in zip(dif, dea):
        if d is not None and e is not None:
            macd_hist.append(round(2 * (d - e), 4))
        else:
            macd_hist.append(None)

    return {"dif": dif, "dea": dea, "macd": macd_hist}


def calc_rsi(prices: list[float], period: int = 14) -> list[Optional[float]]:
    """计算RSI"""
    if len(prices) < period + 1:
        return [None] * len(prices)

    result: list[Optional[float]] = [None] * period
    gains: list[float] = []
    losses: list[float] = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gains.append(max(0, change))
        losses.append(max(0, -change))

    # 第一个RSI用简单平均
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(round(100 - 100 / (1 + rs), 2))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(round(100 - 100 / (1 + rs), 2))

    return result


def calc_kdj(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> dict[str, list[Optional[float]]]:
    """计算KDJ

    改进: 不再以 k=50, d=50 作为初始值, 而是先用前 m1 个 RSV 的简单均值
    计算第一个有效 K, 再用前 m2 个 K 的简单均值计算第一个有效 D,
    使早期数值更准确.
    """
    length = len(closes)
    if length < n:
        return {"k": [None] * length, "d": [None] * length, "j": [None] * length}

    k_values: list[Optional[float]] = [None] * (n - 1)
    d_values: list[Optional[float]] = [None] * (n - 1)
    j_values: list[Optional[float]] = [None] * (n - 1)

    # 先收集所有 RSV
    rsv_list: list[float] = []
    for i in range(n - 1, length):
        low_n = min(lows[i - n + 1 : i + 1])
        high_n = max(highs[i - n + 1 : i + 1])
        rsv = (closes[i] - low_n) / (high_n - low_n) * 100 if high_n != low_n else 50
        rsv_list.append(rsv)

    # 用前 m1 个 RSV 的 SMA 递推初始化 K
    warmup_k = min(m1, len(rsv_list))
    warmup_d = min(m2, warmup_k)

    # 逐步计算 warmup 阶段的 K
    k_warmup: list[float] = []
    for rsv in rsv_list[:warmup_k]:
        if not k_warmup:
            k_warmup.append(rsv)
        else:
            k_warmup.append((m1 - 1) / m1 * k_warmup[-1] + rsv / m1)

    # 逐步计算 warmup 阶段的 D
    d_warmup: list[float] = []
    for kv in k_warmup[:warmup_d]:
        if not d_warmup:
            d_warmup.append(kv)
        else:
            d_warmup.append((m2 - 1) / m2 * d_warmup[-1] + kv / m2)

    k = k_warmup[-1] if k_warmup else 50.0
    d = d_warmup[-1] if d_warmup else 50.0

    # 填充 warmup 阶段的输出 (前 warmup_k 个有效位置)
    d_running = d_warmup[0] if d_warmup else 50.0
    for wi in range(warmup_k):
        kv = k_warmup[wi]
        if wi < warmup_d:
            dv = d_warmup[wi]
        elif wi == warmup_d:
            dv = (m2 - 1) / m2 * d_warmup[-1] + k_warmup[wi] / m2
        else:
            dv = (m2 - 1) / m2 * d_running + k_warmup[wi] / m2
        jv = 3 * kv - 2 * dv
        k_values.append(round(kv, 2))
        d_values.append(round(dv, 2))
        j_values.append(round(jv, 2))
        d_running = dv

    # warmup 之后的正常递推
    for idx in range(warmup_k, len(rsv_list)):
        rsv = rsv_list[idx]
        k = (m1 - 1) / m1 * k + rsv / m1
        d = (m2 - 1) / m2 * d + k / m2
        j = 3 * k - 2 * d
        k_values.append(round(k, 2))
        d_values.append(round(d, 2))
        j_values.append(round(j, 2))

    return {"k": k_values, "d": d_values, "j": j_values}


def calc_boll(prices: list[float], period: int = 20, std_dev: int = 2) -> dict[str, list[Optional[float]]]:
    """计算布林带"""
    if len(prices) < period:
        empty = [None] * len(prices)
        return {"upper": empty, "mid": empty, "lower": empty}

    upper: list[Optional[float]] = [None] * (period - 1)
    mid: list[Optional[float]] = [None] * (period - 1)
    lower: list[Optional[float]] = [None] * (period - 1)

    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1 : i + 1]
        avg = sum(window) / period
        variance = sum((p - avg) ** 2 for p in window) / period
        std = math.sqrt(variance)
        mid.append(round(avg, 2))
        upper.append(round(avg + std_dev * std, 2))
        lower.append(round(avg - std_dev * std, 2))

    return {"upper": upper, "mid": mid, "lower": lower}


def evaluate_technical(
    closes: list[float],
    highs: Optional[list[float]] = None,
    lows: Optional[list[float]] = None,
) -> float:
    """综合技术评分 (0-100)"""
    if len(closes) < 30:
        log.debug(f"数据不足，跳过技术评分: {len(closes)} < 30")
        return 0

    score = 50.0  # 基准分

    # MA趋势
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)

    if ma5[-1] and ma10[-1] and ma20[-1]:
        if ma5[-1] > ma10[-1] > ma20[-1]:
            score += 15  # 多头排列
        elif ma5[-1] < ma10[-1] < ma20[-1]:
            score -= 15  # 空头排列

    # MACD
    macd_data = calc_macd(closes)
    dif = macd_data["dif"]
    macd_hist = macd_data["macd"]
    if dif and dif[-1] is not None:
        if dif[-1] > 0:
            score += 10
        else:
            score -= 10
    if macd_hist and len(macd_hist) >= 2 and macd_hist[-1] is not None and macd_hist[-2] is not None:
        if macd_hist[-1] > macd_hist[-2]:
            score += 5  # MACD柱转强

    # RSI
    rsi = calc_rsi(closes)
    if rsi and rsi[-1] is not None:
        if 40 <= rsi[-1] <= 60:
            score += 10  # 中性区间
        elif 30 <= rsi[-1] < 40:
            score += 5  # 接近超卖
        elif rsi[-1] > 70:
            score -= 10  # 超买

    # KDJ
    if highs and lows:
        kdj = calc_kdj(highs, lows, closes)
        j = kdj["j"]
        if j and j[-1] is not None:
            if j[-1] < 20:
                score += 8  # 超卖区
            elif j[-1] > 80:
                score -= 8  # 超买区

    # 布林带
    boll = calc_boll(closes)
    if boll["upper"][-1] and boll["lower"][-1] and closes:
        current = closes[-1]
        boll_width = boll["upper"][-1] - boll["lower"][-1]
        if boll_width > 0:
            pos = (current - boll["lower"][-1]) / boll_width
            if 0.2 <= pos <= 0.8:
                score += 5
            elif pos < 0.2:
                score += 8  # 接近下轨
            elif pos > 0.8:
                score -= 5  # 接近上轨

    return min(100, max(0, round(score, 1)))


def _fetch_kline(code: str, count: int = 120) -> Optional[list[dict]]:
    """获取K线数据，多源自动降级: 腾讯→新浪→东方财富"""
    from modules.kline_fetcher import kline_fetcher
    raw = kline_fetcher.get_kline(code, count)
    if not raw:
        return None
    # kline_fetcher 返回格式含 date 字段，technical 模块只需 OHLCV
    return [{"open": d["open"], "close": d["close"], "high": d["high"],
             "low": d["low"], "volume": d["volume"]} for d in raw]


def calculate_technical_indicators(code: str, days: int = 30) -> Optional[dict]:
    """获取技术指标（兼容旧版API）

    返回dict格式：rsi, macd_signal, ma_signal, boll_position, change_5d等
    K线数据源: 腾讯(主) → 新浪(备)
    """
    try:
        kline_data = _fetch_kline(code, count=120)
        if not kline_data or len(kline_data) < days:
            return None

        closes = [d["close"] for d in kline_data if d["close"] > 0]
        if len(closes) < days:
            return None

        closes = closes[-days:]
        highs_all = [d["high"] for d in kline_data if d["high"] > 0]
        highs = highs_all[-days:] if len(highs_all) >= days else highs_all
        lows_all = [d["low"] for d in kline_data if d["low"] > 0]
        lows = lows_all[-days:] if len(lows_all) >= days else lows_all
        highs_20d = highs_all[-20:] if len(highs_all) >= 20 else highs_all

        # 提取成交量数据
        volumes_all = [d.get("volume", 0) for d in kline_data]
        volumes = volumes_all[-days:] if len(volumes_all) >= days else volumes_all

        # 计算MA
        ma5 = calc_ma(closes, 5)
        ma10 = calc_ma(closes, 10)
        ma20 = calc_ma(closes, 20)
        ma60 = calc_ma(closes, 60) if len(closes) >= 60 else None

        # MA信号
        ma_signal = "unknown"
        if ma5[-1] and ma10[-1] and ma20[-1]:
            if ma5[-1] > ma10[-1] > ma20[-1]:
                ma_signal = "bull"
            elif ma5[-1] < ma10[-1] < ma20[-1]:
                ma_signal = "bear"

        # RSI
        rsi_data = calc_rsi(closes)
        rsi_value = rsi_data[-1] if rsi_data and rsi_data[-1] is not None else 50

        # MACD
        macd_data = calc_macd(closes)
        dif = macd_data["dif"]
        macd_hist = macd_data["macd"]
        macd_signal = "neutral"
        if dif and dif[-1] is not None:
            if dif[-1] > 0:
                macd_signal = "golden_cross" if (macd_hist and len(macd_hist) >= 2 and
                                                   macd_hist[-1] is not None and macd_hist[-2] is not None and
                                                   macd_hist[-1] > macd_hist[-2]) else "bullish"
            else:
                macd_signal = "death_cross" if (macd_hist and len(macd_hist) >= 2 and
                                                 macd_hist[-1] is not None and macd_hist[-2] is not None and
                                                 macd_hist[-1] < macd_hist[-2]) else "bearish"

        # 布林带位置
        boll = calc_boll(closes)
        boll_position = 0.5
        if boll["upper"][-1] and boll["lower"][-1] and closes:
            boll_width = boll["upper"][-1] - boll["lower"][-1]
            if boll_width > 0:
                boll_position = (closes[-1] - boll["lower"][-1]) / boll_width

        # 5日涨幅
        change_5d = 0.0
        if len(closes) >= 6:
            price_now = closes[-1]
            price_5d_ago = closes[-6]
            if price_5d_ago > 0:
                change_5d = round((price_now / price_5d_ago - 1) * 100, 2)

        # === 新增技术指标 (v2) ===

        # OBV 能量潮 — 量价确认
        obv_trend = "neutral"
        if volumes and len(volumes) >= 10 and len(closes) >= 10:
            obv_data = calc_obv(closes, volumes)
            if obv_data and len(obv_data) >= 5:
                obv_recent = obv_data[-5:]
                obv_ma5 = sum(obv_recent) / len(obv_recent)
                obv_older = obv_data[-10:-5] if len(obv_data) >= 10 else obv_data[:5]
                obv_ma5_old = sum(obv_older) / len(obv_older) if obv_older else 0
                if obv_ma5 > obv_ma5_old and closes[-1] > closes[-5]:
                    obv_trend = "bullish"
                elif obv_ma5 < obv_ma5_old and closes[-1] < closes[-5]:
                    obv_trend = "bearish"

        # 布林带宽度 — 收敛/发散判断
        boll_width_pct = None
        if len(closes) >= 20:
            boll_w = calc_boll_width(closes)
            if boll_w["width"] and boll_w["width"][-1] is not None:
                boll_width_pct = boll_w["width"][-1]

        # 多周期 RSI — 共振判断
        rsi_6 = None
        rsi_12 = None
        rsi_24 = None
        if len(closes) >= 25:
            multi_rsi = calc_multi_rsi(closes, (6, 12, 24))
            rsi_6 = multi_rsi["rsi_6"][-1] if multi_rsi["rsi_6"] and multi_rsi["rsi_6"][-1] is not None else None
            rsi_12 = multi_rsi["rsi_12"][-1] if multi_rsi["rsi_12"] and multi_rsi["rsi_12"][-1] is not None else None
            rsi_24 = multi_rsi["rsi_24"][-1] if multi_rsi["rsi_24"] and multi_rsi["rsi_24"][-1] is not None else None

        # VWAP — 成交量加权平均价
        vwap_value = None
        if volumes and len(volumes) >= 5 and sum(volumes[-5:]) > 0:
            vwap_data = calc_vwap(closes, volumes)
            if vwap_data and vwap_data[-1] is not None:
                vwap_value = vwap_data[-1]

        # ATR — 平均真实波幅 (用于波动率/仓位计算)
        atr_value = None
        if len(closes) >= 14 and len(highs) >= 14 and len(lows) >= 14:
            atr_value = calc_atr(highs, lows, closes, period=14)

        return {
            "rsi": round(rsi_value, 1),
            "macd_signal": macd_signal,
            "ma_signal": ma_signal,
            "boll_position": round(boll_position, 2),
            "ma5": ma5[-1],
            "ma10": ma10[-1],
            "ma20": ma20[-1],
            "ma60": ma60[-1] if ma60 and ma60[-1] is not None else None,
            "price": closes[-1],
            "high_20d": max(highs_20d) if highs_20d else closes[-1],
            "recent_high": max(highs) if highs else closes[-1],
            "boll_upper": boll["upper"][-1] if boll["upper"] and boll["upper"][-1] is not None else None,
            "change_5d": change_5d,
            # v2 新增指标
            "boll_width_pct": boll_width_pct,
            "obv_trend": obv_trend,
            "rsi_6": round(rsi_6, 1) if rsi_6 is not None else None,
            "rsi_12": round(rsi_12, 1) if rsi_12 is not None else None,
            "rsi_24": round(rsi_24, 1) if rsi_24 is not None else None,
            "vwap": vwap_value,
            "atr": atr_value,
            # 动量指标 (供 V5 评分使用)
            "momentum_20": round((closes[-1] / closes[-20] - 1) * 100, 2) if len(closes) >= 20 else 0,
            "momentum_60": round((closes[-1] / closes[0] - 1) * 100, 2) if len(closes) >= 2 else 0,
        }
    except Exception as e:
        log.debug(f"技术指标获取失败: {code}, {e}")
        return None


# ---------------------------------------------------------------------------
# 新增技术指标 (v2)
# ---------------------------------------------------------------------------
def calc_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> Optional[float]:
    """计算平均真实波幅 (Average True Range)

    ATR = MA(True Range, period)
    True Range = max(high-low, abs(high-prev_close), abs(low-prev_close))

    Returns:
        最新的 ATR 值 (float), 数据不足时返回 None
    """
    if len(closes) < period + 1 or len(highs) < period + 1 or len(lows) < period + 1:
        return None

    tr_list: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)

    if len(tr_list) < period:
        return None

    # 简单移动平均
    atr = sum(tr_list[-period:]) / period
    return round(atr, 4)




def calc_obv(closes: list[float], volumes: list[float]) -> list[float]:
    """计算能量潮 (On-Balance Volume)

    规则:
        close > prev_close  → obv += volume
        close < prev_close  → obv -= volume
        close == prev_close → obv 不变

    Returns:
        list[float], 长度与 closes 相同, 第一个元素为 0
    """
    if not closes:
        return []
    obv: list[float] = [0.0]
    for i in range(1, len(closes)):
        prev = obv[-1]
        if closes[i] > closes[i - 1]:
            obv.append(prev + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(prev - volumes[i])
        else:
            obv.append(prev)
    return obv


def calc_vwap(closes: list[float], volumes: list[float]) -> list[Optional[float]]:
    """计算成交量加权平均价 (Volume Weighted Average Price)

    累积计算: vwap[i] = cumsum(close*volume)[i] / cumsum(volume)[i]
    若累积成交量为 0 则对应位置返回 None.

    Returns:
        list[Optional[float]], 长度与 closes 相同
    """
    if not closes:
        return []
    result: list[Optional[float]] = []
    cum_pv = 0.0
    cum_vol = 0.0
    for i in range(len(closes)):
        cum_pv += closes[i] * volumes[i]
        cum_vol += volumes[i]
        if cum_vol == 0:
            result.append(None)
        else:
            result.append(round(cum_pv / cum_vol, 2))
    return result


def calc_boll_width(
    prices: list[float],
    period: int = 20,
    std_dev: int = 2,
) -> dict[str, list[Optional[float]]]:
    """计算布林带宽度百分比 (Bollinger Band Width)

    width = (upper - lower) / mid * 100
    宽度低 → 波动收敛 (squeeze), 宽度高 → 波动扩张 (expansion).

    Returns:
        {"width": list, "mid": list}  — 前置 None 与 calc_boll 对齐
    """
    if len(prices) < period:
        empty: list[Optional[float]] = [None] * len(prices)
        return {"width": empty, "mid": empty}

    width_list: list[Optional[float]] = [None] * (period - 1)
    mid_list: list[Optional[float]] = [None] * (period - 1)

    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1 : i + 1]
        avg = sum(window) / period
        variance = sum((p - avg) ** 2 for p in window) / period
        std = math.sqrt(variance)
        upper = avg + std_dev * std
        lower = avg - std_dev * std
        width_pct = (upper - lower) / avg * 100 if avg != 0 else 0.0
        mid_list.append(round(avg, 2))
        width_list.append(round(width_pct, 2))

    return {"width": width_list, "mid": mid_list}


def calc_multi_rsi(
    prices: list[float],
    periods: tuple[int, ...] = (6, 12, 24),
) -> dict[str, list[Optional[float]]]:
    """多周期 RSI

    同时计算多个周期的 RSI, 复用 calc_rsi.

    Returns:
        {"rsi_6": [...], "rsi_12": [...], "rsi_24": [...]} 等
    """
    result: dict[str, list[Optional[float]]] = {}
    for p in periods:
        result[f"rsi_{p}"] = calc_rsi(prices, p)
    return result


def evaluate_technical_score(
    code: str,
    tech_data: dict,
) -> tuple[int, list[str]]:
    """独立技术评分函数 (0-15 分)

    基于 calculate_technical_indicators 返回的 tech_data 计算技术加分,
    供 scoring.py 中 evaluate_stock() 的 tech bonus 使用.

    Args:
        code: 股票代码 (用于日志)
        tech_data: calculate_technical_indicators 返回的字典

    Returns:
        (score, reasons)  score 为 0-15 的整数, reasons 为人类可读原因列表
    """
    score = 0
    reasons: list[str] = []

    # --- MA 信号 (最多 3 分) ---
    ma_signal = tech_data.get("ma_signal", "unknown")
    if ma_signal == "bull":
        score += 3
        reasons.append("MA多头排列 ✓")
    elif ma_signal == "bear":
        reasons.append("MA空头排列")

    # --- MACD 信号 (最多 3 分) ---
    macd_signal = tech_data.get("macd_signal", "neutral")
    if macd_signal == "golden_cross":
        score += 3
        reasons.append("MACD金叉 ✓")
    elif macd_signal == "bullish":
        score += 1
        reasons.append("MACD偏多")
    elif macd_signal == "death_cross":
        reasons.append("MACD死叉")
    elif macd_signal == "bearish":
        reasons.append("MACD偏空")

    # --- RSI 信号 (最多 3 分) ---
    rsi_value = tech_data.get("rsi")
    if rsi_value is not None:
        if rsi_value < 30:
            score += 3
            reasons.append(f"RSI超卖区({rsi_value:.0f}) ✓")
        elif rsi_value < 40:
            score += 1
            reasons.append(f"RSI接近超卖({rsi_value:.0f})")
        elif rsi_value > 70:
            reasons.append(f"RSI超买区({rsi_value:.0f})")
        elif rsi_value > 60:
            reasons.append(f"RSI偏高({rsi_value:.0f})")

    # --- 布林带位置 (最多 3 分) ---
    boll_pos = tech_data.get("boll_position")
    if boll_pos is not None:
        if boll_pos < 0.2:
            score += 3
            reasons.append(f"布林带下轨附近({boll_pos:.0%}) ✓")
        elif boll_pos < 0.3:
            score += 1
            reasons.append(f"布林带偏下({boll_pos:.0%})")
        elif boll_pos > 0.8:
            reasons.append(f"布林带上轨附近({boll_pos:.0%})")

    # --- 5日涨幅 (最多 3 分) ---
    change_5d = tech_data.get("change_5d", 0)
    if change_5d is not None:
        if -5 <= change_5d <= 5:
            score += 1
            reasons.append(f"5日涨幅适中({change_5d:+.1f}%)")
        elif change_5d < -5:
            score += 3
            reasons.append(f"5日超跌({change_5d:+.1f}%) ✓")
        elif change_5d > 10:
            reasons.append(f"5日涨幅过大({change_5d:+.1f}%)")

    # --- OBV 量价确认 (最多 2 分) ---
    obv_trend = tech_data.get("obv_trend", "neutral")
    if obv_trend == "bullish":
        score += 2
        reasons.append("OBV量价齐升 ✓")
    elif obv_trend == "bearish":
        reasons.append("OBV量价背离")

    # --- 布林带宽度 (最多 1 分) — 收敛预示变盘 ---
    boll_width_pct = tech_data.get("boll_width_pct")
    if boll_width_pct is not None:
        if boll_width_pct < 5:  # 极度收敛，变盘在即
            score += 1
            reasons.append(f"布林带收敛({boll_width_pct:.1f}%) 变盘在即")

    # --- 多周期 RSI 共振 (最多 2 分) ---
    rsi_6 = tech_data.get("rsi_6")
    rsi_12 = tech_data.get("rsi_12")
    rsi_24 = tech_data.get("rsi_24")
    if rsi_6 is not None and rsi_12 is not None and rsi_24 is not None:
        # 三周期 RSI 均在超卖区 → 强烈买入信号
        if rsi_6 < 30 and rsi_12 < 40 and rsi_24 < 50:
            score += 2
            reasons.append(f"RSI多周期共振超卖 ✓")
        # 三周期 RSI 均在超买区 → 强烈卖出信号
        elif rsi_6 > 70 and rsi_12 > 60 and rsi_24 > 50:
            reasons.append("RSI多周期共振超买")

    # --- VWAP 支撑 (最多 2 分) ---
    vwap = tech_data.get("vwap")
    price = tech_data.get("price") or tech_data.get("ma5")
    if vwap is not None and price is not None and vwap > 0:
        pct_above_vwap = (price - vwap) / vwap * 100
        if -2 <= pct_above_vwap <= 2:
            score += 2  # 价格在 VWAP 附近，支撑强
            reasons.append(f"价格贴近VWAP({pct_above_vwap:+.1f}%) ✓")
        elif 2 < pct_above_vwap <= 5:
            score += 1

    return min(15, max(0, score)), reasons
    return min(15, max(0, score)), reasons
