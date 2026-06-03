"""技术指标计算模块"""

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
    """计算KDJ"""
    length = len(closes)
    if length < n:
        return {"k": [None] * length, "d": [None] * length, "j": [None] * length}

    k_values: list[Optional[float]] = [None] * (n - 1)
    d_values: list[Optional[float]] = [None] * (n - 1)
    j_values: list[Optional[float]] = [None] * (n - 1)

    k = 50.0
    d = 50.0

    for i in range(n - 1, length):
        low_n = min(lows[i - n + 1 : i + 1])
        high_n = max(highs[i - n + 1 : i + 1])
        rsv = (closes[i] - low_n) / (high_n - low_n) * 100 if high_n != low_n else 50
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


def calculate_technical_indicators(code: str, days: int = 30) -> Optional[dict]:
    """获取技术指标（兼容旧版API）

    返回dict格式：rsi, macd_signal, ma_signal, boll_position, change_5d等
    """
    try:
        from modules.data_fetcher import _get_session, _config

        # 腾讯K线API需要带市场前缀: sz/sh
        if code.startswith("6"):
            prefix = "sh"
        else:
            prefix = "sz"
        full_code = f"{prefix}{code}"

        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={full_code},day,,,120,qfq"
        resp = _get_session().get(url, timeout=5)
        data = resp.json()

        day_data = data.get("data", {}).get(full_code, {}).get("qfqday", [])
        if not day_data:
            return None

        closes = [float(d[2]) for d in day_data if len(d) > 2]
        if len(closes) < days:
            return None

        closes = closes[-days:]
        highs_all = [float(d[3]) for d in day_data if len(d) > 3]
        highs = highs_all[-days:] if len(highs_all) >= days else highs_all
        lows_all = [float(d[4]) for d in day_data if len(d) > 4]
        lows = lows_all[-days:] if len(lows_all) >= days else lows_all
        highs_20d = highs_all[-20:] if len(highs_all) >= 20 else highs_all

        # 计算MA
        ma5 = calc_ma(closes, 5)
        ma10 = calc_ma(closes, 10)
        ma20 = calc_ma(closes, 20)

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

        return {
            "rsi": round(rsi_value, 1),
            "macd_signal": macd_signal,
            "ma_signal": ma_signal,
            "boll_position": round(boll_position, 2),
            "ma5": ma5[-1],
            "ma10": ma10[-1],
            "ma20": ma20[-1],
            "price": closes[-1],
            "high_20d": max(highs_20d) if highs_20d else closes[-1],
            "change_5d": change_5d,
        }
    except Exception as e:
        log.debug(f"技术指标获取失败: {code}, {e}")
        return None
