"""强势选股模块 - 输出格式与strong_pick.html模板完全兼容

V5.5改进:
1. ROE预过滤 - 排除亏损股(ROE<0)
2. V5评分整合 - 强势评分(技术60%) + V5评分(基本面40%)
3. V5.5 ROE/Q惩罚 - 与evaluate_stock一致的惩罚逻辑
4. 北交所/B股过滤
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from modules.data_fetcher import get_realtime_quotes, get_financial_data, get_stock_industry, preload_industry_cache
from modules.market_env import get_market_env
from modules.logger import log
from modules.scoring import industry_concentration_limit, calculate_buy_sell, multi_factor_evaluate
from modules.models import StockQuote, FinancialData


def _get_limit_threshold(code: str) -> float:
    """获取涨停阈值: 创业板(300xxx/301xxx)和科创板(688xxx)为20%, 其他为10%"""
    if code.startswith("300") or code.startswith("301"):
        return 19.5
    if code.startswith("688"):
        return 19.5
    return 9.5


def _fetch_industry_for_results(results: list[dict]) -> None:
    """为结果列表批量获取行业信息 — 直接从缓存读取"""
    from modules.data_fetcher import _industry_cache as _ic

    for stock in results:
        code = stock.get("code", "")
        cached = _ic.get(code)
        if isinstance(cached, dict):
            industry = cached.get("industry", "未知")
            stock["industry"] = industry
            stock["sector"] = cached.get("sector_type", "default")
        else:
            # 缓存中没有，调用API获取
            try:
                info = get_stock_industry(code)
                stock["industry"] = info.get("industry", "未知")
                stock["sector"] = info.get("sector_type", "default")
            except Exception:
                stock["industry"] = "未知"
                stock["sector"] = "default"

    # 调试：检查问题股票
    import re
    for s in results:
        if re.match(r'^\d+$', str(s.get("industry", ""))):
            log.warning(f"行业异常: {s.get('code','')} {s.get('name','')} -> industry='{s.get('industry','')}'")


def run_strong_stock_picker(top_n: int = 30) -> list[dict]:
    """执行强势选股，返回与模板兼容的股票列表"""
    env = None
    try:
        env = get_market_env()
        if env.can_pick():
            log.info(f"强势股选股: 市场环境 status={env.status}, trend={env.trend}, top_n={top_n}")
        else:
            top_n = max(5, top_n // 3)
            log.warning(f"强势股选股: 市场环境不佳(status={env.status}, trend={env.trend})，缩减至top_n={top_n}")
        top_n = max(top_n, env.adjusted_top_n(top_n))
    except Exception as e:
        log.warning(f"强势股选股: 市场环境检测失败: {e}，使用默认参数")
        env = None

    # 1. 获取行情数据
    quotes = get_realtime_quotes()
    if not quotes:
        log.warning("强势选股: 无法获取行情数据")
        return []

    # 2. 预筛选: 排除ST、北交所、B股、价格异常、涨幅>涨停
    candidates = {}
    for code, q in quotes.items():
        name = q.name or ""
        if "ST" in name or "*" in name:
            continue
        # V5.5: 过滤北交所/B股
        if code.startswith("8") or code.startswith("4") or code.startswith("920"):
            continue
        if code.startswith("900") or code.startswith("200"):
            continue
        if code.startswith("9"):
            continue
        if q.price <= 2 or q.price > 300:
            continue
        if q.market_cap > 0 and (q.market_cap < 20 or q.market_cap > 3000):
            continue
        # 保留有一定涨幅但未涨停的股票
        limit_pct = _get_limit_threshold(code)
        if -2 <= q.change_pct <= limit_pct:
            candidates[code] = q

    if not candidates:
        log.warning("强势选股: 预筛选后无候选股")
        return []

    log.info(f"强势选股: 预筛选 {len(candidates)} 只候选股")

    # 3. 获取财务数据
    codes = list(candidates.keys())
    preload_industry_cache(codes)
    financials = get_financial_data(codes)
    log.info(f"强势选股: 财务数据 {len(financials)}/{len(codes)}")

    # V5.5: 基本面预过滤 — 排除亏损股(ROE<0)
    filtered_codes = []
    for code in codes:
        f = financials.get(code)
        roe = f.roe if f else 0
        if roe < 0:
            continue  # 亏损企业不应入选强势股
        filtered_codes.append(code)
    
    if len(filtered_codes) < len(codes):
        log.info(f"强势选股: ROE过滤 剔除 {len(codes) - len(filtered_codes)} 只亏损股，剩余 {len(filtered_codes)} 只")
    codes = filtered_codes
    candidates = {code: candidates[code] for code in codes if code in candidates}

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
    try:
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(calc_tech, c) for c in codes]
            for future in as_completed(futures):
                try:
                    code, tech = future.result(timeout=20)
                    if tech:
                        tech_cache[code] = tech
                except Exception:
                    pass
    except RuntimeError as e:
        # interpreter shutdown 时 ThreadPoolExecutor.submit() 会抛此异常
        if "interpreter shutdown" in str(e).lower() or "cannot schedule" in str(e).lower():
            log.warning(f"强势选股: 技术指标计算因解释器关闭而中断")
            return []
        raise
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

            # 量比计算: 优先使用API真实值
            turnover_rate = q.turnover
            volume_ratio = q.volume_ratio if q.volume_ratio > 0 else _calc_volume_ratio(q)

            # 信号判断
            pullback_stable = _check_pullback_stable(q, ma_signal)
            limit_ratio = 1.0 + (_get_limit_threshold(code) + 0.5) / 100
            has_limit_up = q.high > 0 and q.prev_close > 0 and q.high / q.prev_close >= limit_ratio
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

            # 财务数据
            roe = f.roe if f else 0
            gross_margin = f.gross_margin if f else 0
            net_margin = f.net_margin if f else 0
            rev_growth = f.revenue_growth if f else 0
            profit_growth = f.profit_growth if f else 0
            debt_ratio = f.debt_ratio if f else 0
            pe = f.pe if f and f.pe > 0 else q.pe
            pb = f.pb if f and f.pb > 0 else q.pb

            # 综合评分（技术面）
            tech_score = _calc_strong_score(q, f, rsi, golden_cross, volume_ratio, boll_position, code)
            
            # V5 multi-factor evaluation（基本面）
            v5_result = None
            v5_total = 0.0
            try:
                stock_for_v5 = {
                    "code": code, "name": q.name, "price": q.price,
                    "pe": pe if pe > 0 else 0, "pb": pb if pb > 0 else 0,
                    "market_cap": q.market_cap, "turnover_rate": turnover_rate,
                    "roe": roe, "gross_margin": gross_margin,
                    "net_margin": net_margin, "rev_growth": rev_growth,
                    "profit_growth": profit_growth, "debt_ratio": debt_ratio,
                    "change_pct": q.change_pct,
                }
                v5_result = multi_factor_evaluate(stock_for_v5)
                v5_total = v5_result.get("v5_total", 0) or v5_result.get("total_score", 0)
                
                # V5.5: ROE/Q惩罚（与evaluate_stock一致）
                if 0 < roe < 5:
                    v5_total *= 0.80
                elif 0 < roe < 8:
                    v5_total *= 0.90
                q_val = v5_result.get("v5_factors", {}).get("quality", 50)
                if q_val < 25:
                    v5_total *= 0.85
            except Exception as e:
                log.debug(f"V5 evaluation failed for {code}: {e}")

            # V5.5: 混合评分 = 技术面60% + 基本面40%
            # 强势股既要有技术面支撑，也要有基本面质量
            blended_score = tech_score * 0.6 + v5_total * 0.4

            # Calculate buy/sell points
            buy_sell = None
            try:
                from modules.technical import calculate_technical_indicators
                tech_bs = calculate_technical_indicators(code, days=30)
                stock_for_bs = {
                    "code": code, "name": q.name, "price": q.price,
                    "pe": pe if pe > 0 else 0, "pb": pb if pb > 0 else 0,
                    "market_cap": q.market_cap, "turnover_rate": turnover_rate,
                    "roe": roe, "gross_margin": gross_margin,
                    "net_margin": net_margin, "rev_growth": rev_growth,
                    "profit_growth": profit_growth, "debt_ratio": debt_ratio,
                    "change_pct": q.change_pct,
                }
                buy_sell = calculate_buy_sell(stock_for_bs, blended_score, tech_data=tech_bs)
            except Exception as e:
                import logging; logger = logging.getLogger(__name__); logger.warning('buy_sell calc failed for %s: %s', code, e)

            result = {
                "code": code,
                "name": q.name,
                "price": q.price,
                "change_pct": round(q.change_pct, 2),
                "change_5d": round(change_5d, 2),
                "market_cap": round(q.market_cap, 2),
                "score": round(blended_score, 1),  # V5.5: 使用混合评分
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
                "buy_sell": buy_sell,
            }
            if v5_result:
                result["v5_score"] = round(v5_total, 1)
                result["v5_factors"] = v5_result.get("v5_factors") or v5_result.get("factors")
                result["v5_reasons"] = v5_result.get("v5_reasons") or v5_result.get("reasons")
                result["v5_recommendation"] = v5_result.get("v5_recommendation") or v5_result.get("recommendation")

            # 生成选股理由
            reason_parts = []
            if result.get("v5_reasons"):
                reason_parts.append(result["v5_reasons"])
            if golden_cross:
                reason_parts.append("金叉")
            if volume_ratio > 2.0:
                reason_parts.append(f"量比{volume_ratio:.1f}倍")
            elif volume_ratio > 1.5:
                reason_parts.append(f"量比{volume_ratio:.1f}倍")
            if 0 < rsi < 30:
                reason_parts.append("RSI超卖")
            elif rsi > 70:
                reason_parts.append("RSI偏高")
            if pullback_stable:
                reason_parts.append("回调企稳")
            if pe > 0 and pe < 15:
                reason_parts.append(f"PE{pe:.0f}低估")
            if roe > 15:
                reason_parts.append(f"ROE{roe:.0f}%优秀")
            result["reasons"] = " | ".join(reason_parts) if reason_parts else None

            return result
        except Exception as e:
            log.debug(f"强势评分失败: {code}, {e}")
            return None

    try:
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(score_one, c): c for c in candidates}
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=30)
                    if result:
                        results.append(result)
                except Exception:
                    pass
    except RuntimeError as e:
        # interpreter shutdown 时 ThreadPoolExecutor.submit() 会抛此异常
        if "interpreter shutdown" in str(e).lower() or "cannot schedule" in str(e).lower():
            log.warning("强势选股: 评分计算因解释器关闭而中断，返回已有结果")
            return results
        raise

    # 5. 按评分排序
    results.sort(key=lambda x: x["score"], reverse=True)

    # 6. 使用更宽的候选池，确保行业分散后有足够股票
    top_pool = results[:max(top_n * 3, top_n)]
    if top_pool:
        _fetch_industry_for_results(top_pool)

    # Industry concentration limit
    target_count = min(top_n, len(results))
    top_results = industry_concentration_limit(
        top_pool,
        max_per_industry=2,
        min_count=target_count,
    )[:target_count]

    # 7. 添加行业信息
    if top_results:
        _fetch_industry_for_results(top_results)

    log.info(f"强势选股: 入选 {len(top_results)} 只")
    return top_results


def _calc_volume_ratio(q: StockQuote) -> float:
    """计算量比（近似值）— 优先使用API真实值，此函数为兜底"""
    turnover = q.turnover
    if turnover <= 0:
        return 0.0
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
    has_lower_shadow = q.low < q.price and (q.price - q.low) > (q.high - q.price)
    is_up = q.change_pct > 0
    return has_lower_shadow and is_up


def _calc_strong_score(q: StockQuote, f: Optional[FinancialData],
                       rsi: float, golden_cross: bool,
                       volume_ratio: float, boll_position: float, code: str = "") -> float:
    """强势选股技术面评分 (0-100)
    
    V5.5: 此函数仅计算技术面评分，不再包含基本面评分。
    基本面评分由multi_factor_evaluate()处理，在score_one()中混合。
    """
    score = 0.0
    # 1. 涨幅得分 (0-25): 涨幅2-6%最优
    chg = q.change_pct
    is_gem_or_star = code.startswith("300") or code.startswith("301") or code.startswith("688")
    if is_gem_or_star:
        if 2 <= chg <= 4:
            score += 25
        elif 4 < chg <= 6:
            score += 22
        elif 6 < chg <= 10:
            score += 20
        elif 10 < chg <= 15:
            score += 15
        elif 15 < chg <= 19:
            score += 8
        elif 1 <= chg < 2:
            score += 18
        elif 0 <= chg < 1:
            score += 10
        elif chg < 0:
            score += max(0, 5 + chg)
    else:
        if 2 <= chg <= 4:
            score += 25
        elif 4 < chg <= 6:
            score += 22
        elif 1 <= chg < 2:
            score += 18
        elif 0 <= chg < 1:
            score += 10
        elif 6 < chg <= 8:
            score += 15
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
        score += 8

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

    # 4. 基本面得分 (0-30) — V5.5: 保留但权重降低为参考
    # 注意：基本面主要由V5评分处理，这里给少量加分
    fundamental = 0
    if f:
        if f.roe >= 15:
            fundamental += 8
        elif f.roe >= 10:
            fundamental += 5
        elif f.roe >= 5:
            fundamental += 2

        if f.gross_margin >= 30:
            fundamental += 4
        elif f.gross_margin >= 20:
            fundamental += 2

        if 0 < f.pe <= 20:
            fundamental += 6
        elif 20 < f.pe <= 35:
            fundamental += 3

        if 0 < f.debt_ratio <= 50:
            fundamental += 4
        elif 50 < f.debt_ratio <= 70:
            fundamental += 2
    else:
        if 0 < q.pe <= 20:
            fundamental += 6
        elif 20 < q.pe <= 35:
            fundamental += 3

    score += fundamental

    return min(100, max(0, score))
