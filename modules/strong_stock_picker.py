"""强势选股模块 - 输出格式与strong_pick.html模板完全兼容"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from modules.data_fetcher import get_realtime_quotes, get_financial_data, get_stock_industry
from modules.logger import log
from modules.models import StockQuote, FinancialData


def _fetch_industry_for_results(results: list[dict]) -> None:
    """为结果列表批量获取行业信息"""
    def _fetch(stock):
        try:
            info = get_stock_industry(stock["code"])
            stock["industry"] = info.get("industry", "未知")
            stock["sector"] = info.get("sector_type", "default")
        except Exception:
            stock["industry"] = "未知"
            stock["sector"] = "default"

    with ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(_fetch, results))


def run_strong_stock_picker(top_n: int = 30) -> list[dict]:
    """执行强势选股，返回与模板兼容的股票列表

    模板期望字段:
    - code, name, price, change_pct, market_cap
    - score (综合评分)
    - volume_ratio (量比)
    - turnover_rate (换手率)
    - position_pct (布林带位置)
    - rsi (RSI指标)
    - golden_cross (MACD金叉: bool)
    - pullback_stable (回踩站稳: bool)
    - has_limit_up (曾涨停: bool)
    - gentle_volume (温和放量: bool)
    - moderate_volume (适度放量: bool)
    - extreme_volume (过度放量: bool)
    - pe, pb, roe, gross_margin, net_margin, rev_growth, profit_growth, debt_ratio
    """
    # 1. 获取行情数据
    quotes = get_realtime_quotes()
    if not quotes:
        log.warning("强势选股: 无法获取行情数据")
        return []

    # 2. 预筛选: 排除ST、北交所、价格异常、涨幅>9.5%(已涨停不追)
    candidates = {}
    for code, q in quotes.items():
        name = q.name or ""
        if "ST" in name or "*" in name:
            continue
        if code.startswith("9") or code.startswith("688"):
            continue
        if q.price <= 2 or q.price > 300:
            continue
        if q.market_cap > 0 and (q.market_cap < 20 or q.market_cap > 3000):
            continue
        # 保留有一定涨幅但未涨停的股票
        if -2 <= q.change_pct <= 9.5:
            candidates[code] = q

    if not candidates:
        log.warning("强势选股: 预筛选后无候选股")
        return []

    log.info(f"强势选股: 预筛选 {len(candidates)} 只候选股")

    # 3. 获取财务数据
    codes = list(candidates.keys())
    financials = get_financial_data(codes)
    log.info(f"强势选股: 财务数据 {len(financials)}/{len(codes)}")

    # 3.5 批量计算技术指标（避免逐只调用超时）
    tech_cache: dict[str, dict] = {}

    def calc_tech(code: str):
        try:
            from modules.technical import calculate_technical_indicators
            tech = calculate_technical_indicators(code, days=60)
            return (code, tech)
        except Exception:
            return (code, None)

    log.info(f"强势选股: 批量计算技术指标 {len(candidates)} 只...")
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(calc_tech, c) for c in codes]
        for future in as_completed(futures):
            try:
                code, tech = future.result(timeout=20)
                if tech:
                    tech_cache[code] = tech
            except Exception:
                pass
    log.info(f"强势选股: 技术指标计算完成 {len(tech_cache)}/{len(candidates)}")

    # 4. 逐只评分并构建输出
    results = []

    def score_one(code: str) -> Optional[dict]:
        try:
            q = candidates[code]
            f = financials.get(code)

            # 获取技术指标（从缓存）
            tech = tech_cache.get(code)
            rsi = tech.get("rsi", 50.0) if tech else 50.0
            golden_cross = tech.get("macd_signal") == "golden_cross" if tech else False
            boll_position = tech.get("boll_position", 0.5) if tech else 0.5
            ma_signal = tech.get("ma_signal", "") if tech else ""
            change_5d = tech.get("change_5d", 0.0) if tech else 0.0

            # 量比计算: 用换手率/近期平均换手率近似
            turnover_rate = q.turnover
            volume_ratio = q.volume_ratio if q.volume_ratio > 0 else _calc_volume_ratio(q)

            # 信号判断
            pullback_stable = _check_pullback_stable(q, ma_signal)
            has_limit_up = q.high > 0 and q.prev_close > 0 and q.high / q.prev_close >= 1.095
            gentle_volume = 1.0 <= volume_ratio <= 2.0 and turnover_rate > 0
            moderate_volume = 2.0 < volume_ratio <= 4.0 and turnover_rate > 0
            extreme_volume = volume_ratio > 4.0 and turnover_rate > 0

            breakthrough_pct = -1
            try:
                high_20d = tech.get("high_20d", 0) if tech else 0
                if q.price > 0 and high_20d > 0:
                    breakthrough_pct = round((q.price / high_20d - 1) * 100, 2)
            except Exception:
                pass

            # 综合评分
            score = _calc_strong_score(q, f, rsi, golden_cross, volume_ratio, boll_position)

            # 财务数据
            roe = f.roe if f else 0
            gross_margin = f.gross_margin if f else 0
            net_margin = f.net_margin if f else 0
            rev_growth = f.revenue_growth if f else 0
            profit_growth = f.profit_growth if f else 0
            debt_ratio = f.debt_ratio if f else 0
            pe = f.pe if f and f.pe > 0 else q.pe
            pb = f.pb if f and f.pb > 0 else q.pb

            return {
                "code": code,
                "name": q.name,
                "price": q.price,
                "change_pct": round(q.change_pct, 2),
                "change_5d": round(change_5d, 2),
                "market_cap": round(q.market_cap, 2),
                "score": round(score, 1),
                "volume_ratio": round(volume_ratio, 2),
                "turnover_rate": round(turnover_rate, 2),
                "position_pct": round(boll_position * 100, 1),
                "breakthrough_pct": breakthrough_pct,
                "rsi": round(rsi, 1),
                "golden_cross": golden_cross,
                "pullback_stable": pullback_stable,
                "has_limit_up": has_limit_up,
                "gentle_volume": gentle_volume,
                "moderate_volume": moderate_volume,
                "extreme_volume": extreme_volume,
                "pe": round(pe, 1) if pe > 0 else 0,
                "pb": round(pb, 2) if pb > 0 else 0,
                "roe": round(roe, 1) if roe else 0,
                "gross_margin": round(gross_margin, 1) if gross_margin else 0,
                "net_margin": round(net_margin, 1) if net_margin else 0,
                "rev_growth": round(rev_growth, 1) if rev_growth else 0,
                "profit_growth": round(profit_growth, 1) if profit_growth else 0,
                "debt_ratio": round(debt_ratio, 1) if debt_ratio else 0,
            }
        except Exception as e:
            log.debug(f"强势评分失败: {code}, {e}")
            return None

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(score_one, c): c for c in candidates}
        for future in as_completed(futures):
            try:
                result = future.result(timeout=30)
                if result:
                    results.append(result)
            except Exception:
                pass

    # 5. 按评分排序
    results.sort(key=lambda x: x["score"], reverse=True)

    # 6. 只返回 top_n
    top_results = results[:top_n]

    # 7. 添加行业信息
    if top_results:
        _fetch_industry_for_results(top_results)

    log.info(f"强势选股: 入选 {len(top_results)} 只")
    return top_results


def _calc_volume_ratio(q: StockQuote) -> float:
    """计算量比（近似值）

    量比 = 当日累计成交量 / 过去5日同时段平均成交量
    这里用换手率近似估算：换手率 > 3% 通常量比 > 1.5
    """
    turnover = q.turnover
    if turnover <= 0:
        return 0.0
    # 简化估算: 基于换手率
    # 换手率1% → 量比约0.8; 2% → 1.2; 3% → 1.5; 5% → 2.5; 8% → 4.0
    if turnover < 0.5:
        return round(turnover * 0.6, 2)
    elif turnover < 2:
        return round(0.5 + turnover * 0.35, 2)
    elif turnover < 5:
        return round(1.0 + (turnover - 2) * 0.5, 2)
    else:
        return round(2.5 + (turnover - 5) * 0.5, 2)


def _check_pullback_stable(q: StockQuote, ma_signal: str) -> bool:
    """判断是否回踩均线站稳"""
    if ma_signal != "bull":
        return False
    if q.prev_close <= 0 or q.price <= 0:
        return False
    # 今日最低价接近某条均线但未跌破，收盘在均线之上
    # 简化判断: 今日有下影线且收阳
    has_lower_shadow = q.low < q.price and (q.price - q.low) > (q.high - q.price)
    is_up = q.change_pct > 0
    return has_lower_shadow and is_up


def _calc_strong_score(q: StockQuote, f: Optional[FinancialData],
                       rsi: float, golden_cross: bool,
                       volume_ratio: float, boll_position: float) -> float:
    """强势选股综合评分 (0-100)"""
    score = 0.0

    # 1. 涨幅得分 (0-25): 涨幅2-6%最优
    chg = q.change_pct
    if 2 <= chg <= 4:
        score += 25
    elif 4 < chg <= 6:
        score += 22
    elif 1 <= chg < 2:
        score += 18
    elif 0 <= chg < 1:
        score += 10
    elif 6 < chg <= 8:
        score += 15  # 涨太多可能追高
    elif chg < 0:
        score += max(0, 5 + chg)

    # 2. 量比得分 (0-20): 量比1.5-3最优（适度放量）
    if 1.5 <= volume_ratio <= 3:
        score += 20
    elif 1.0 <= volume_ratio < 1.5:
        score += 12
    elif 3 < volume_ratio <= 5:
        score += 15
    elif 0.5 <= volume_ratio < 1.0:
        score += 5
    elif volume_ratio > 5:
        score += 8  # 过度放量

    # 3. 技术指标得分 (0-25)
    tech_score = 0
    if 50 <= rsi <= 65:
        tech_score += 10
    elif 40 <= rsi < 50:
        tech_score += 6
    elif 65 < rsi <= 75:
        tech_score += 5

    if golden_cross:
        tech_score += 8
    if boll_position >= 0.7:
        tech_score += 4
    elif 0.5 <= boll_position < 0.7:
        tech_score += 7
    score += min(25, tech_score)

    # 4. 基本面得分 (0-30)
    fundamental = 0
    if f:
        if f.roe >= 15:
            fundamental += 10
        elif f.roe >= 10:
            fundamental += 6

        if f.gross_margin >= 30:
            fundamental += 6
        elif f.gross_margin >= 20:
            fundamental += 3

        if 0 < f.pe <= 20:
            fundamental += 8
        elif 20 < f.pe <= 35:
            fundamental += 4

        if 0 < f.debt_ratio <= 50:
            fundamental += 6
        elif 50 < f.debt_ratio <= 70:
            fundamental += 3
    else:
        # 没有财务数据时，用行情数据估算
        if 0 < q.pe <= 20:
            fundamental += 8
        elif 20 < q.pe <= 35:
            fundamental += 4

    score += fundamental

    return min(100, max(0, score))
