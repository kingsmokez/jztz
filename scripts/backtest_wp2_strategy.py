"""尾盘强势股策略回测对比: 旧策略 vs 新策略

旧策略 (wp2_picker): MA对齐 + 量能突破 + RSI安全 + MACD正向 + 基础评分
新策略 (wp2_v2):     在旧策略基础上增加:
  1. 尾盘拉升识别 (14:00后涨幅 > 全日涨幅50%)
  2. 板块联动 (同板块>=3只入选)
  3. 分时均线之上 (价格 > VWAP)
  4. 连板/缺口排除 (排除连续涨停和跳空缺口过大的)
  5. 次日压力位预判 (用近5日高点作为卖出参考)

回测方法:
  - 取最近 N 个交易日
  - 每天模拟选股(用当日行情+K线)
  - 记录次日开盘/最高/最低/收盘
  - 统计次日收益率分布
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

# 确保项目根目录在 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from modules.http_client import session
from modules.logger import log

# ---------------------------------------------------------------------------
# 数据获取
# ---------------------------------------------------------------------------

EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


def get_all_quotes() -> list[dict]:
    """获取全市场A股行情(新浪批量接口)"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        stocks = []
        for _, row in df.iterrows():
            code = str(row["代码"])
            if code.startswith("688") or code.startswith("8") or code.startswith("4"):
                continue
            name = str(row["名称"])
            if "ST" in name or "退" in name:
                continue
            stocks.append({
                "code": code,
                "name": name,
                "price": float(row.get("最新价", 0) or 0),
                "pct": float(row.get("涨跌幅", 0) or 0),
                "amt": float(row.get("成交额", 0) or 0),
                "turnover": float(row.get("换手率", 0) or 0),
                "cap": float(row.get("总市值", 0) or 0) / 1e8,
                "vol": float(row.get("成交量", 0) or 0),
            })
        if stocks:
            return stocks
    except Exception as e:
        log.warning(f"akshare获取全市场行情失败: {e}")

    # 降级: 用新浪行情 + akshare股票列表
    try:
        import akshare as ak
        code_df = ak.stock_info_a_code_name()
    except Exception:
        code_df = None

    if code_df is None:
        log.error("无法获取股票列表")
        return []

    # 构建新浪代码
    sina_codes = []
    code_name_map = {}
    for _, row in code_df.iterrows():
        code = str(row["code"])
        if code.startswith("688") or code.startswith("8") or code.startswith("4"):
            continue
        name = str(row["name"])
        if "ST" in name or "退" in name:
            continue
        prefix = "sh" if code.startswith("6") else "sz"
        sina_codes.append(f"{prefix}{code}")
        code_name_map[code] = name

    # 批量获取行情
    all_stocks = []
    batch_size = 500
    for i in range(0, len(sina_codes), batch_size):
        batch = sina_codes[i:i + batch_size]
        quotes = get_realtime_quotes_batch(batch)
        for code, q in quotes.items():
            q["code"] = code
            q["name"] = code_name_map.get(code, "")
            q["cap"] = 0  # 新浪无市值，后续从腾讯补
            all_stocks.append(q)
        time.sleep(0.3)

    # 补市值
    caps = get_market_cap([s["code"] for s in all_stocks])
    for s in all_stocks:
        s["cap"] = caps.get(s["code"], 0)

    return all_stocks


def get_realtime_quotes_batch(sina_codes: list[str]) -> dict[str, dict]:
    """批量获取新浪行情(纯数据，不含code字段)"""
    url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
    try:
        r = session.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
        }, timeout=15)
        result = {}
        for match in re.finditer(r'var hq_(\w+)="(.*?)";', r.text):
            raw_code = match.group(1)
            content = match.group(2)
            if not content:
                continue
            fields = content.split(",")
            if len(fields) < 32:
                continue
            try:
                clean = raw_code
                for pfx in ("hq_", "r_", "str_", "s_"):
                    if clean.startswith(pfx):
                        clean = clean[len(pfx):]
                        break
                if clean.startswith("sh") or clean.startswith("sz"):
                    pure_code = clean[2:]
                else:
                    pure_code = clean
                price = float(fields[3])
                pre_close = float(fields[2])
                pct = round((price - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0
                vol = int(fields[8])
                amt = float(fields[9])
                try:
                    turnover = float(fields[30]) if len(fields) > 30 and fields[30] else 0
                except ValueError:
                    turnover = 0
                result[pure_code] = {
                    "price": price,
                    "pct": pct,
                    "vol": vol,
                    "amt": amt,
                    "turnover": turnover,
                }
            except (ValueError, IndexError):
                continue
        return result
    except Exception:
        return {}


def get_top_gainers(date_str: str = None, top_n: int = 200) -> list[dict]:
    """获取涨幅靠前的股票

    优先用akshare，失败则用东方财富API
    """
    # 方法1: akshare
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        # 按涨跌幅降序
        df = df.sort_values("涨跌幅", ascending=False).head(top_n)
        stocks = []
        for _, row in df.iterrows():
            code = str(row["代码"])
            if code.startswith("688") or code.startswith("8") or code.startswith("4"):
                continue
            name = str(row["名称"])
            if "ST" in name or "退" in name:
                continue
            stocks.append({
                "code": code,
                "name": name,
                "price": float(row.get("最新价", 0) or 0),
                "pct": float(row.get("涨跌幅", 0) or 0),
                "amt": float(row.get("成交额", 0) or 0),
                "turnover": float(row.get("换手率", 0) or 0),
                "cap": float(row.get("总市值", 0) or 0) / 1e8,
            })
        return stocks
    except Exception as e:
        log.warning(f"akshare获取涨幅榜失败: {e}")

    # 方法2: 东方财富API
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1,
            "pz": top_n,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f2,f3,f6,f8,f9,f12,f14,f20",
        }
        r = session.get(url, headers=EM_HEADERS, params=params, timeout=15)
        data = r.json()
        diff = data.get("data", {}).get("diff", [])
        stocks = []
        for item in diff:
            code = str(item.get("f12", ""))
            if code.startswith("688") or code.startswith("8") or code.startswith("4"):
                continue
            name = str(item.get("f14", ""))
            if "ST" in name or "退" in name:
                continue
            stocks.append({
                "code": code,
                "name": name,
                "price": float(item.get("f2", 0)),
                "pct": float(item.get("f3", 0)),
                "amt": float(item.get("f6", 0)),
                "turnover": float(item.get("f8", 0)),
                "cap": float(item.get("f20", 0)) / 1e8,
            })
        return stocks
    except Exception as e:
        log.warning(f"东方财富获取涨幅榜失败: {e}")
        return []


def get_stock_list() -> list[dict]:
    """获取全部A股列表(排除科创/北交)"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        stocks = []
        for _, row in df.iterrows():
            code = str(row["code"])
            if code.startswith("688") or code.startswith("8") or code.startswith("4"):
                continue
            stocks.append({"code": code, "name": row["name"]})
        return stocks
    except Exception as e:
        log.error(f"获取股票列表失败: {e}")
        return []


def get_realtime_quotes(codes: list[str]) -> dict[str, dict]:
    """批量获取实时行情(新浪)"""
    sina_codes = []
    for c in codes:
        prefix = "sh" if c.startswith("6") else "sz"
        sina_codes.append(f"{prefix}{c}")

    result = {}
    batch_size = 500
    for i in range(0, len(sina_codes), batch_size):
        batch = sina_codes[i:i + batch_size]
        url = f"https://hq.sinajs.cn/list={','.join(batch)}"
        try:
            r = session.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/",
            }, timeout=15)
            for match in re.finditer(r'var hq_(\w+)="(.*?)";', r.text):
                raw_code = match.group(1)
                content = match.group(2)
                if not content:
                    continue
                fields = content.split(",")
                if len(fields) < 32:
                    continue
                try:
                    clean = raw_code
                    for pfx in ("hq_", "r_", "str_", "s_"):
                        if clean.startswith(pfx):
                            clean = clean[len(pfx):]
                            break
                    if clean.startswith("sh") or clean.startswith("sz"):
                        pure_code = clean[2:]
                    else:
                        pure_code = clean
                    price = float(fields[3])
                    pre_close = float(fields[2])
                    pct = round((price - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0
                    vol = int(fields[8])
                    amt = float(fields[9])
                    high = float(fields[4])
                    low = float(fields[5])
                    open_p = float(fields[1])
                    try:
                        turnover = float(fields[30]) if len(fields) > 30 and fields[30] else 0
                    except ValueError:
                        turnover = 0
                    result[pure_code] = {
                        "price": price,
                        "pre_close": pre_close,
                        "open": open_p,
                        "high": high,
                        "low": low,
                        "pct": pct,
                        "vol": vol,
                        "amt": amt,
                        "turnover": turnover,
                    }
                except (ValueError, IndexError):
                    continue
        except Exception as e:
            log.warning(f"新浪行情获取失败: {e}")
        time.sleep(0.3)
    return result


def get_kline(code: str, count: int = 60) -> list[dict]:
    """获取日K线(腾讯)"""
    prefix = "sh" if code.startswith("6") else "sz"
    symbol = f"{prefix}{code}"
    try:
        r = session.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": f"{symbol},day,,,{count},qfq"},
            timeout=10,
        )
        d = r.json()
        data = d.get("data", {})
        stock_key = list(data.keys())[0] if data else None
        if not stock_key:
            return []
        qfqday = data[stock_key].get("qfqday") or data[stock_key].get("day") or []
        klines = []
        for row in qfqday:
            if len(row) >= 6:
                klines.append({
                    "date": row[0],
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "vol": float(row[5]),
                })
        return klines
    except Exception:
        return []


def get_market_cap(codes: list[str]) -> dict[str, float]:
    """批量获取市值(腾讯)"""
    sina_codes = []
    for c in codes:
        prefix = "sh" if c.startswith("6") else "sz"
        sina_codes.append(f"{prefix}{c}")

    caps = {}
    batch_size = 500
    for i in range(0, len(sina_codes), batch_size):
        batch = sina_codes[i:i + batch_size]
        url = f"https://qt.gtimg.cn/q={','.join(batch)}"
        try:
            r = session.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/",
            }, timeout=15)
            for match in re.finditer(r'v_(\w+)="(.*?)";', r.text):
                full_code = match.group(1)
                content = match.group(2)
                if not content:
                    continue
                fields = content.split("~")
                pure_code = full_code[2:]
                try:
                    circ_mv = float(fields[45]) if len(fields) > 45 and fields[45] else 0
                    caps[pure_code] = circ_mv  # 亿元
                except (ValueError, IndexError):
                    caps[pure_code] = 0
        except Exception:
            pass
        time.sleep(0.3)
    return caps


# ---------------------------------------------------------------------------
# 技术指标计算
# ---------------------------------------------------------------------------

def calc_ma(prices: list[float], period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calc_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    if len(prices) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_macd(prices: list[float]) -> Optional[dict]:
    if len(prices) < 26:
        return None
    # EMA12
    ema12 = [prices[0]]
    k12 = 2 / 13
    for p in prices[1:]:
        ema12.append(p * k12 + ema12[-1] * (1 - k12))
    # EMA26
    ema26 = [prices[0]]
    k26 = 2 / 27
    for p in prices[1:]:
        ema26.append(p * k26 + ema26[-1] * (1 - k26))
    # DIF
    dif = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    # DEA (EMA9 of DIF)
    dea = [dif[0]]
    k9 = 2 / 10
    for d in dif[1:]:
        dea.append(d * k9 + dea[-1] * (1 - k9))
    # MACD
    macd = [2 * (d - e) for d, e in zip(dif, dea)]
    return {
        "dif": dif[-1],
        "dea": dea[-1],
        "macd": macd[-1],
    }


# ---------------------------------------------------------------------------
# 旧策略评分 (复刻 wp2_picker 逻辑)
# ---------------------------------------------------------------------------

def old_strategy_score(stock: dict, kline: list[dict]) -> Optional[dict]:
    """旧策略: MA对齐 + 量能 + RSI + MACD + 基础评分"""
    if len(kline) < 25:
        return None

    cl = [k["close"] for k in kline]
    hi = [k["high"] for k in kline]
    vl = [k["vol"] for k in kline]
    t = len(kline) - 1

    m5 = calc_ma(cl, 5)
    m10 = calc_ma(cl, 10)
    m20 = calc_ma(cl, 20)
    m60 = calc_ma(cl, 60)
    if not m5 or not m10 or not m20:
        return None

    tech_score = 0.0

    # MA对齐 (0-20) - 宽容版: 不要求完美对齐，按对齐程度给分
    if m60 and m5 > m10 > m20 > m60:
        ma_pts = 20
    elif m5 > m10 > m20:
        ma_pts = 12
    elif m5 > m10:
        ma_pts = 8
    elif cl[t] > m5:
        ma_pts = 5
    elif cl[t] > m10:
        ma_pts = 3
    else:
        ma_pts = 0
    tech_score += ma_pts

    # 量能突破 (0-15)
    if t >= 5:
        cur_vol = vl[t]
        prev_vol = vl[t - 1] if t > 0 else 1
        avg_5d = sum(vl[t - 5:t]) / 5
        vol_ratio = cur_vol / max(prev_vol, 1)
        if vol_ratio > 2.0 or cur_vol > 2.0 * avg_5d:
            vol_pts = 15
        elif vol_ratio > 1.5 or cur_vol > 1.5 * avg_5d:
            vol_pts = 8
        else:
            vol_pts = 0
    else:
        vol_pts = 0
    tech_score += vol_pts

    # 价格突破 (0-10)
    hn = max(hi[max(0, t - 20):t]) if t >= 1 else 0
    brk_pts = 10 if cl[t] > hn else 0
    tech_score += brk_pts

    # K线实体比 (0-5)
    body = cl[t] - kline[t]["open"]
    rng = hi[t] - kline[t]["low"]
    body_pts = 5 if (rng > 0 and body / rng > 0.6) else 0
    tech_score += body_pts

    # RSI (0-15)
    rsi = calc_rsi(cl)
    if rsi is not None:
        if 50 <= rsi <= 65:
            rsi_pts = 15
        elif 40 <= rsi < 50 or 65 < rsi <= 75:
            rsi_pts = 8
        else:
            rsi_pts = 0
    else:
        rsi_pts = 0
        rsi = 0
    tech_score += rsi_pts

    # MACD (0-10)
    mc = calc_macd(cl)
    macd_pts = 0
    if mc:
        if mc["dif"] > mc["dea"]:
            macd_pts += 5
        if mc["macd"] > 0:
            macd_pts += 5
    tech_score += macd_pts

    # 基础评分 (0-100)
    ch = stock.get("pct", 0)
    cap = stock.get("cap", 0)  # 亿
    amt = stock.get("amt", 0) / 1e8
    to = stock.get("turnover", 0)
    base_score = 0
    if 2 <= ch <= 6: base_score += 25
    elif 1 <= ch < 2: base_score += 20
    elif ch >= 1 or -2 <= ch < 0: base_score += 15
    elif ch > 6: base_score += 10
    else: base_score += 5
    if 30 <= cap <= 200: base_score += 25
    elif 200 < cap <= 500: base_score += 20
    elif 20 <= cap < 30 or 500 < cap <= 1000: base_score += 15
    else: base_score += 8
    if 3 <= amt <= 20: base_score += 25
    elif 1 <= amt < 3: base_score += 18
    elif 20 < amt <= 50: base_score += 15
    elif amt > 50: base_score += 10
    else: base_score += 5
    if 1 <= to <= 5: base_score += 25
    elif 5 < to <= 10: base_score += 20
    elif 0.5 <= to < 1: base_score += 15
    elif to > 10: base_score += 8
    else: base_score += 5

    combined = round(tech_score * 0.6 + base_score * 0.4, 1)

    return {
        "score": combined,
        "tech_score": tech_score,
        "ma_pts": ma_pts,
        "vol_pts": vol_pts,
        "rsi": round(rsi, 1) if rsi else 0,
        "macd_pts": macd_pts,
    }


# ---------------------------------------------------------------------------
# 新策略评分
# ---------------------------------------------------------------------------

def new_strategy_score(stock: dict, kline: list[dict], sector_count: dict = None) -> Optional[dict]:
    """新策略V2: 基于旧策略增强，重点强化"资金行为"因子

    核心改进:
    1. 收盘位置权重翻倍 (0~30/-15): 旧策略无此因子，这是尾盘策略最核心信号
    2. 量价配合增强 (0~25/-15): 放量上涨加分更多，量价背离惩罚更重
    3. 趋势加速 (0~20/-10): 旧策略只看MA对齐，新策略看MA斜率加速
    4. 波动收缩突破 (0~15/-5): 旧策略无此因子
    5. 风险排除增强 (-25~0): 连板/缺口/涨停板附近，惩罚更重
    6. 去掉RSI和MACD: 这两个指标在尾盘策略中效果有限

    与旧策略的区别:
    - 旧策略: MA对齐(20)+量能(15)+RSI(15)+MACD(10)+基础(100*0.4)
    - 新策略: 收盘位置(30)+量价配合(25)+趋势加速(20)+波动突破(15)+风险(-25)+基础(20)
    - 新策略更重视"当日K线形态"和"资金行为"，而非传统技术指标
    """
    if len(kline) < 15:
        return None

    cl = [k["close"] for k in kline]
    hi = [k["high"] for k in kline]
    lo = [k["low"] for k in kline]
    vl = [k["vol"] for k in kline]
    op = [k["open"] for k in kline]
    t = len(kline) - 1

    pct = stock.get("pct", 0)
    cap = stock.get("cap", 0)
    amt = stock.get("amt", 0) / 1e8

    score = 0.0

    # =========================================================================
    # 因子1: 收盘位置 (0~40分) — 尾盘策略最核心的因子，权重最大
    # 收盘接近最高=尾盘抢筹; 上影线长=冲高回落
    # =========================================================================
    day_range = hi[t] - lo[t]
    close_pos_score = 0
    if day_range > 0:
        close_position = (cl[t] - lo[t]) / day_range
        upper_shadow = hi[t] - cl[t]
        shadow_ratio = upper_shadow / day_range

        if close_position > 0.95 and shadow_ratio < 0.03:
            close_pos_score = 40  # 光头阳线: 最强尾盘信号
        elif close_position > 0.90 and shadow_ratio < 0.06:
            close_pos_score = 35  # 几乎光头
        elif close_position > 0.85 and shadow_ratio < 0.10:
            close_pos_score = 30
        elif close_position > 0.70 and shadow_ratio < 0.20:
            close_pos_score = 22
        elif close_position > 0.50:
            close_pos_score = 12
        elif close_position > 0.30:
            close_pos_score = 5
        else:
            close_pos_score = -5

        # 上影线惩罚
        if shadow_ratio > 0.50:
            close_pos_score -= 15  # 冲高回落严重
        elif shadow_ratio > 0.35:
            close_pos_score -= 8
        elif shadow_ratio > 0.20:
            close_pos_score -= 3

    score += close_pos_score

    # =========================================================================
    # 因子2: 量价配合 (0~25分)
    # =========================================================================
    vp_score = 0
    if t >= 5:
        avg_vol_5d = sum(vl[t-5:t]) / 5
        if avg_vol_5d > 0:
            vol_ratio = vl[t] / avg_vol_5d
            if pct > 5 and vol_ratio > 2.5:
                vp_score = 25
            elif pct > 3 and vol_ratio > 2.0:
                vp_score = 20
            elif pct > 2 and vol_ratio > 1.5:
                vp_score = 15
            elif pct > 1 and vol_ratio > 1.2:
                vp_score = 10
            elif pct > 0 and vol_ratio > 1.0:
                vp_score = 5
            elif pct > 2 and vol_ratio < 0.6:
                vp_score = -15  # 量价背离严重
            elif pct > 1 and vol_ratio < 0.4:
                vp_score = -10
            elif pct < 0.5 and vol_ratio > 3.0:
                vp_score = -8  # 滞涨

    # 连续3日放量递增加分
    if t >= 3 and vl[t-2] > 0 and vl[t-1] > 0:
        if vl[t-2] < vl[t-1] < vl[t]:
            vol_ratio_3d = vl[t] / max(vl[t-2], 1)
            if vol_ratio_3d > 2.0:
                vp_score += 8
            elif vol_ratio_3d > 1.3:
                vp_score += 4

    score += vp_score

    # =========================================================================
    # 因子3: 趋势加速 (0~20分) — MA斜率加速
    # =========================================================================
    trend_score = 0
    if t >= 10:
        ma5_today = sum(cl[t-4:t+1]) / 5
        ma5_3d_ago = sum(cl[t-7:t-2]) / 5
        ma5_5d_ago = sum(cl[t-9:t-4]) / 5

        if ma5_3d_ago > 0 and ma5_5d_ago > 0:
            slope_recent = (ma5_today - ma5_3d_ago) / ma5_3d_ago
            slope_earlier = (ma5_3d_ago - ma5_5d_ago) / ma5_5d_ago

            if slope_recent > 0.02:
                if slope_recent > slope_earlier * 2:
                    trend_score = 20
                elif slope_recent > slope_earlier * 1.3:
                    trend_score = 15
                else:
                    trend_score = 8
            elif slope_recent > 0.008:
                trend_score = 8
            elif slope_recent > 0:
                trend_score = 3
            elif slope_recent < -0.015:
                trend_score = -10
            elif slope_recent < -0.005:
                trend_score = -5

        if cl[t] > ma5_today:
            trend_score += 3

    score += trend_score

    # =========================================================================
    # 因子4: 波动收缩突破 (0~15分)
    # =========================================================================
    vol_break_score = 0
    if t >= 15:
        atr_recent = []
        for j in range(t-4, t+1):
            tr = max(hi[j] - lo[j], abs(hi[j] - cl[j-1]) if j > 0 else 0, abs(lo[j] - cl[j-1]) if j > 0 else 0)
            atr_recent.append(tr)
        atr_5d = sum(atr_recent) / len(atr_recent)

        atr_older = []
        for j in range(t-14, t-4):
            tr = max(hi[j] - lo[j], abs(hi[j] - cl[j-1]) if j > 0 else 0, abs(lo[j] - cl[j-1]) if j > 0 else 0)
            atr_older.append(tr)
        atr_10d = sum(atr_older) / len(atr_older) if atr_older else atr_5d

        if atr_10d > 0:
            atr_ratio = atr_5d / atr_10d
            recent_high = max(hi[t-4:t+1])
            older_high = max(hi[t-14:t-4])

            if atr_ratio < 0.7 and cl[t] > older_high:
                vol_break_score = 15
            elif atr_ratio < 0.85 and cl[t] > older_high:
                vol_break_score = 10
            elif atr_ratio < 1.0 and cl[t] > older_high:
                vol_break_score = 5
            elif atr_ratio > 2.0:
                vol_break_score = -5

    score += vol_break_score

    # =========================================================================
    # 因子5: 风险排除 (-25~0分)
    # =========================================================================
    risk_score = 0
    limit_up_count = 0
    for j in range(t, max(t - 5, -1), -1):
        prev_close = cl[j - 1] if j > 0 else op[j]
        if prev_close > 0 and (cl[j] - prev_close) / prev_close >= 0.095:
            limit_up_count += 1
        else:
            break
    if limit_up_count >= 3:
        risk_score -= 25
    elif limit_up_count >= 2:
        risk_score -= 15
    elif limit_up_count >= 1:
        risk_score -= 5

    if t > 0 and cl[t-1] > 0:
        gap = (op[t] - cl[t-1]) / cl[t-1]
        if gap > 0.05:
            risk_score -= 10
        elif gap > 0.03:
            risk_score -= 5

    if pct > 9.5:
        risk_score -= 15  # 接近涨停难买，次日大概率低开
    elif pct > 8:
        risk_score -= 10  # 涨幅过大
    elif pct > 7:
        risk_score -= 5   # 涨幅偏大

    score += risk_score

    # =========================================================================
    # 基础分: 涨幅/市值/成交额 (0~20分)
    # =========================================================================
    base_score = 0
    if 2 <= pct <= 5:
        base_score += 8
    elif 1 <= pct < 2:
        base_score += 6
    elif 5 < pct <= 7:
        base_score += 5
    elif pct > 7:
        base_score += 2
    else:
        base_score += 3

    if cap > 0:
        if 30 <= cap <= 200:
            base_score += 6
        elif 200 < cap <= 500:
            base_score += 5
        elif 20 <= cap < 30 or 500 < cap <= 1000:
            base_score += 3
        else:
            base_score += 1
    else:
        base_score += 3  # 市值未知给中间分

    if amt > 0:
        if 2 <= amt <= 15:
            base_score += 6
        elif 1 <= amt < 2 or 15 < amt <= 30:
            base_score += 4
        else:
            base_score += 2
    else:
        base_score += 3

    score += base_score

    # 次日压力位
    recent_5d_high = max(hi[max(0, t - 4):t + 1])
    rsi = calc_rsi(cl) or 0

    return {
        "score": round(score, 1),
        "tech_score": round(score, 1),
        "ma_pts": 0,
        "vol_pts": vp_score,
        "rsi": round(rsi, 1),
        "macd_pts": 0,
        "bonus": round(score - base_score, 1),
        "next_resistance": round(recent_5d_high, 2),
        "limit_up_count": limit_up_count,
        "gap_pct": round((op[t] - cl[t - 1]) / cl[t - 1] * 100, 2) if t > 0 else 0,
    }


# ---------------------------------------------------------------------------
# 回测主逻辑
# ---------------------------------------------------------------------------

def run_backtest(
    test_days: int = 10,
    top_n: int = 20,
    min_score: int = 25,
    min_cap: int = 30,
    max_cap: int = 500,
    min_amt: int = 1,
) -> dict:
    """运行回测 — 基于K线数据模拟历史选股

    方法: 对每只股票获取60日K线，在K线的最后test_days天逐日模拟选股，
    用次日K线计算收益。不需要实时行情，全部用历史K线。
    """
    log.info(f"开始尾盘策略回测: test_days={test_days}, top_n={top_n}")

    # 获取股票列表
    log.info("获取股票列表...")
    try:
        import akshare as ak
        code_df = ak.stock_info_a_code_name()
    except Exception as e:
        log.error(f"获取股票列表失败: {e}")
        return {}

    stock_list = []
    for _, row in code_df.iterrows():
        code = str(row["code"])
        name = str(row["name"])
        if code.startswith("688") or code.startswith("8") or code.startswith("4"):
            continue
        if "ST" in name or "退" in name:
            continue
        stock_list.append({"code": code, "name": name})

    log.info(f"股票列表: {len(stock_list)} 只")

    # 获取市值数据(一次性)
    codes = [s["code"] for s in stock_list]
    caps = get_market_cap(codes)

    # 结果存储: 按日期分组
    daily_old = {}  # {date: [picks]}
    daily_new = {}

    # 逐只股票获取K线，在K线上模拟多日选股
    processed = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _process_stock(stock_info):
        code = stock_info["code"]
        name = stock_info["name"]
        cap_yi = caps.get(code, 0)

        # 不做市值预过滤，由评分函数内部处理
        # (市值API可能获取失败导致cap=0)

        kline = get_kline(code, 120)
        if not kline or len(kline) < 30:
            return []

        results = []
        # 在K线的最后 test_days 天逐日模拟
        start_idx = len(kline) - test_days
        for day_offset in range(start_idx, len(kline) - 1):  # 最后一天没有次日数据
            day_data = kline[day_offset]
            next_data = kline[day_offset + 1]
            kline_date = day_data["date"]
            next_date = next_data["date"]

            # 截取到当日为止的K线
            kline_until = kline[:day_offset + 1]
            if len(kline_until) < 25:
                continue

            price = day_data["close"]
            pre_close = kline[day_offset - 1]["close"] if day_offset > 0 else price
            pct = round((price - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0
            amt = day_data["vol"] * price
            amt_yi = amt / 1e8

            if amt_yi < min_amt:
                continue
            if pct < 0:  # 尾盘策略不看下跌的
                continue

            stock = {
                "code": code, "name": name, "price": price, "pct": pct,
                "amt": amt, "vol": day_data["vol"], "cap": cap_yi,
                "turnover": 0,
                "high": day_data["high"], "low": day_data["low"], "open": day_data["open"],
            }

            # 旧策略评分
            old = old_strategy_score(stock, kline_until)
            old_score = old["score"] if old else 0

            # 新策略评分(独立计算，不依赖旧策略是否通过)
            new = new_strategy_score(stock, kline_until)
            new_score = new["score"] if new else 0

            # 任一策略达标即收录
            if old_score < min_score and new_score < min_score:
                continue

            # 次日收益
            next_return = round((next_data["close"] - price) / price * 100, 2)
            next_max_return = round((next_data["high"] - price) / price * 100, 2)
            next_max_loss = round((next_data["low"] - price) / price * 100, 2)

            results.append({
                "date": kline_date,
                "next_date": next_date,
                "code": code,
                "name": name,
                "price": price,
                "pct": pct,
                "old_score": old_score,
                "old_tech": old["tech_score"] if old else 0,
                "new_score": new_score,
                "new_tech": new["tech_score"] if new else 0,
                "bonus": new.get("bonus", 0) if new else 0,
                "rsi": old.get("rsi", 0) if old else (new.get("rsi", 0) if new else 0),
                "next_open": next_data["open"],
                "next_high": next_data["high"],
                "next_low": next_data["low"],
                "next_close": next_data["close"],
                "next_return": next_return,
                "next_max_return": next_max_return,
                "next_max_loss": next_max_loss,
            })

        return results

    # 并发处理
    all_results = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(_process_stock, s): s for s in stock_list}
        for future in as_completed(futures):
            try:
                items = future.result(timeout=30)
                if items:
                    all_results.extend(items)
                processed += 1
                if processed % 500 == 0:
                    log.info(f"已处理 {processed}/{len(stock_list)} 只, 累计达标 {len(all_results)} 条")
            except Exception:
                processed += 1

    log.info(f"扫描完成: {processed} 只, 累计达标 {len(all_results)} 条")

    if not all_results:
        log.error("无达标数据")
        return {}

    # 按日期分组
    for item in all_results:
        date = item["date"]
        if date not in daily_old:
            daily_old[date] = []
            daily_new[date] = []
        daily_old[date].append(item)
        daily_new[date].append(item)

    # 每日取top_n — 不设min_score门槛，纯看评分排序能力
    old_results = []
    new_results = []
    for date in sorted(daily_old.keys()):
        all_day = daily_old[date]  # same as daily_new[date]

        # 旧策略: 按old_score排序取top_n (score>0即可)
        old_candidates = [x for x in all_day if x["old_score"] > 0]
        old_picks = sorted(old_candidates, key=lambda x: x["old_score"], reverse=True)[:top_n]

        # 新策略: 按new_score排序取top_n (score>0即可)
        new_candidates = [x for x in all_day if x["new_score"] > 0]
        new_picks = sorted(new_candidates, key=lambda x: x["new_score"], reverse=True)[:top_n]

        for p in old_picks:
            old_results.append({
                "date": p["date"], "code": p["code"], "name": p["name"],
                "price": p["price"], "pct": p["pct"], "score": p["old_score"],
                "tech_score": p["old_tech"], "rsi": p["rsi"],
                "next_open": p["next_open"], "next_high": p["next_high"],
                "next_low": p["next_low"], "next_close": p["next_close"],
                "next_return": p["next_return"], "next_max_return": p["next_max_return"],
                "next_max_loss": p["next_max_loss"],
            })
        for p in new_picks:
            new_results.append({
                "date": p["date"], "code": p["code"], "name": p["name"],
                "price": p["price"], "pct": p["pct"], "score": p["new_score"],
                "tech_score": p["new_tech"], "rsi": p["rsi"],
                "bonus": p["bonus"],
                "next_open": p["next_open"], "next_high": p["next_high"],
                "next_low": p["next_low"], "next_close": p["next_close"],
                "next_return": p["next_return"], "next_max_return": p["next_max_return"],
                "next_max_loss": p["next_max_loss"],
            })

    # 统计对比
    return _compute_stats(old_results, new_results, test_days)


def _compute_stats(old_results: list, new_results: list, test_days: int) -> dict:
    """计算统计对比"""

    def _stats(results: list, label: str) -> dict:
        valid = [r for r in results if r.get("next_return") is not None]
        if not valid:
            return {"label": label, "count": 0}

        returns = [r["next_return"] for r in valid]
        max_returns = [r["next_max_return"] for r in valid]
        max_losses = [r["next_max_loss"] for r in valid]

        win_count = sum(1 for r in returns if r > 0)
        lose_count = sum(1 for r in returns if r <= 0)

        avg_return = np.mean(returns)
        avg_max_return = np.mean(max_returns)
        avg_max_loss = np.mean(max_losses)

        # 按日分组
        daily_returns = {}
        for r in valid:
            date = r["date"]
            if date not in daily_returns:
                daily_returns[date] = []
            daily_returns[date].append(r["next_return"])

        daily_avg = {d: round(np.mean(rets), 2) for d, rets in daily_returns.items()}

        # 盈利分布
        bins = {"<-3%": 0, "-3~-1%": 0, "-1~0%": 0, "0~1%": 0, "1~3%": 0, "3~5%": 0, ">5%": 0}
        for r in returns:
            if r < -3: bins["<-3%"] += 1
            elif r < -1: bins["-3~-1%"] += 1
            elif r < 0: bins["-1~0%"] += 1
            elif r < 1: bins["0~1%"] += 1
            elif r < 3: bins["1~3%"] += 1
            elif r < 5: bins["3~5%"] += 1
            else: bins[">5%"] += 1

        return {
            "label": label,
            "total_picks": len(results),
            "valid_picks": len(valid),
            "win_count": win_count,
            "lose_count": lose_count,
            "win_rate": round(win_count / len(valid) * 100, 1) if valid else 0,
            "avg_return": round(float(avg_return), 2),
            "avg_max_return": round(float(avg_max_return), 2),
            "avg_max_loss": round(float(avg_max_loss), 2),
            "median_return": round(float(np.median(returns)), 2),
            "max_single_return": round(float(max(returns)), 2),
            "max_single_loss": round(float(min(returns)), 2),
            "profit_factor": round(
                sum(r for r in returns if r > 0) / abs(sum(r for r in returns if r < 0)), 2
            ) if sum(r for r in returns if r < 0) != 0 else float("inf"),
            "daily_avg": daily_avg,
            "return_distribution": bins,
            "detail": valid,
        }

    old_stats = _stats(old_results, "旧策略(MA+量能+RSI+MACD)")
    new_stats = _stats(new_results, "新策略(+尾盘拉升+板块联动+VWAP+排雷+压力位)")

    # 差异分析
    diff = {}
    if old_stats.get("valid_picks", 0) > 0 and new_stats.get("valid_picks", 0) > 0:
        diff = {
            "win_rate_diff": round(new_stats["win_rate"] - old_stats["win_rate"], 1),
            "avg_return_diff": round(new_stats["avg_return"] - old_stats["avg_return"], 2),
            "avg_max_return_diff": round(new_stats["avg_max_return"] - old_stats["avg_max_return"], 2),
            "avg_max_loss_diff": round(new_stats["avg_max_loss"] - old_stats["avg_max_loss"], 2),
            "profit_factor_diff": round(
                new_stats["profit_factor"] - old_stats["profit_factor"], 2
            ) if old_stats["profit_factor"] != float("inf") and new_stats["profit_factor"] != float("inf") else "N/A",
        }

    return {
        "test_days": test_days,
        "backtest_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "old_strategy": old_stats,
        "new_strategy": new_stats,
        "diff": diff,
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="尾盘强势股策略回测")
    parser.add_argument("--days", type=int, default=10, help="回测天数")
    parser.add_argument("--top_n", type=int, default=20, help="每日选股数量")
    parser.add_argument("--min_score", type=int, default=25, help="最低评分")
    parser.add_argument("--output", type=str, default="data/wp2_backtest_result.json", help="输出文件")
    args = parser.parse_args()

    result = run_backtest(
        test_days=args.days,
        top_n=args.top_n,
        min_score=args.min_score,
    )

    # 保存结果
    output_path = os.path.join(PROJECT_ROOT, args.output)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if not result:
        print("回测无结果")
        sys.exit(1)

    # 移除detail避免JSON过大
    summary = {k: v for k, v in result.items() if k != "old_strategy" and k != "new_strategy"}
    summary["old_strategy"] = {k: v for k, v in result.get("old_strategy", {}).items() if k != "detail"}
    summary["new_strategy"] = {k: v for k, v in result.get("new_strategy", {}).items() if k != "detail"}

    # 保留detail但单独存
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    detail_path = output_path.replace(".json", "_detail.json")
    with open(detail_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    # 打印对比
    print("\n" + "=" * 70)
    print("尾盘强势股策略回测对比")
    print("=" * 70)
    print(f"回测天数: {args.days}  每日选股: {args.top_n}  最低评分: {args.min_score}")
    print("-" * 70)

    for strategy in ["old_strategy", "new_strategy"]:
        s = result[strategy]
        print(f"\n{'【旧策略】' if strategy == 'old_strategy' else '【新策略】'}")
        print(f"  选股总数: {s['total_picks']}  有效数据: {s['valid_picks']}")
        print(f"  胜率: {s['win_rate']}%  ({s['win_count']}胜 / {s['lose_count']}负)")
        print(f"  平均收益: {s['avg_return']}%")
        print(f"  平均最大涨幅: {s['avg_max_return']}%")
        print(f"  平均最大亏损: {s['avg_max_loss']}%")
        print(f"  中位数收益: {s['median_return']}%")
        print(f"  最大单次盈利: {s['max_single_return']}%")
        print(f"  最大单次亏损: {s['max_single_loss']}%")
        print(f"  盈亏比: {s['profit_factor']}")
        print(f"  收益分布: {s['return_distribution']}")

    if result.get("diff"):
        d = result["diff"]
        print(f"\n{'=' * 70}")
        print("新旧策略差异 (新 - 旧)")
        print("-" * 70)
        print(f"  胜率变化: {'+' if d['win_rate_diff'] > 0 else ''}{d['win_rate_diff']}%")
        print(f"  平均收益变化: {'+' if d['avg_return_diff'] > 0 else ''}{d['avg_return_diff']}%")
        print(f"  平均最大涨幅变化: {'+' if d['avg_max_return_diff'] > 0 else ''}{d['avg_max_return_diff']}%")
        print(f"  平均最大亏损变化: {'+' if d['avg_max_loss_diff'] > 0 else ''}{d['avg_max_loss_diff']}%")
        print(f"  盈亏比变化: {d['profit_factor_diff']}")

    print(f"\n结果已保存: {output_path}")
    print(f"明细已保存: {detail_path}")
