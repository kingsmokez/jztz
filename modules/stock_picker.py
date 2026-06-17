"""每日选股模块 - 输出格式与daily_pick.html模板完全兼容"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from modules.data_fetcher import get_realtime_quotes, get_financial_data, get_preset_financials, get_stock_industry, preload_industry_cache
from modules.logger import log
from modules.models import StockQuote, FinancialData
from modules.scoring import evaluate_stock, industry_concentration_limit, calculate_buy_sell
from modules.market_env import get_market_env


def _fetch_industry_for_results(results: list[dict]) -> None:
    """为结果列表批量获取行业信息"""
    from concurrent.futures import ThreadPoolExecutor
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


def run_picker(top_n: int = 80) -> list[dict]:
    """执行每日选股 - 与旧版逻辑对齐

    流程:
    1. 获取全市场行情
    2. 基础过滤（ST/北交所/低换手率/低市值）
    3. 批量获取财务数据
    4. 补充PB（预设数据或PE×ROE/100估算）
    5. 批量计算技术指标
    6. 五维评分
    7. 按v5_score排序，返回Top N
    """
    env = None
    try:
        env = get_market_env()
        if env.can_pick():
            top_n = env.adjusted_top_n(top_n)
            log.info(f"每日选股: 市场环境 status={env.status}, trend={env.trend}, top_n={top_n}")
        else:
            top_n = max(3, top_n // 3)
            log.warning(f"每日选股: 市场环境不佳(status={env.status}, trend={env.trend})，缩减至top_n={top_n}")
    except Exception as e:
        log.warning(f"每日选股: 市场环境检测失败: {e}，使用默认参数")
        env = None

    quotes = get_realtime_quotes()
    if not quotes:
        log.warning("每日选股: 无法获取行情数据")
        return []

    candidates = {}
    for code, q in quotes.items():
        name = q.name or ""
        if "ST" in name or "*" in name or "退" in name or name.startswith("N"):
            continue
        if code.startswith("9") or code.startswith("8") or code.startswith("4"):
            continue
        if q.price <= 1:
            continue
        if q.market_cap > 0 and q.market_cap < 10:
            continue
        if 0 < q.turnover < 0.3:
            continue
        candidates[code] = q

    if not candidates:
        log.warning("每日选股: 预筛选后无候选股")
        return []

    total_scanned = len(candidates)
    log.info(f"每日选股: 预筛选 {total_scanned} 只候选股")

    codes = list(candidates.keys())
    preload_industry_cache(codes)
    financials = get_financial_data(codes)
    log.info(f"每日选股: 财务数据 {len(financials)}/{len(codes)}")

    preset_financials = get_preset_financials()

    log.info(f"每日选股: 批量计算技术指标 {len(candidates)} 只...")
    tech_cache = {}

    def calc_tech(code: str):
        try:
            from modules.technical import calculate_technical_indicators
            tech = calculate_technical_indicators(code, days=30)
            return (code, tech)
        except Exception:
            return (code, None)

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(calc_tech, c) for c in codes]
        completed = 0
        for future in as_completed(futures):
            try:
                code, tech = future.result(timeout=20)
                if tech:
                    tech_cache[code] = tech
                completed += 1
                if completed % 200 == 0:
                    log.info(f"  技术指标计算进度: {completed}/{len(codes)}")
            except Exception:
                pass

    log.info(f"每日选股: 技术指标计算完成，缓存 {len(tech_cache)} 只")

    results = []

    def score_one(code: str) -> Optional[dict]:
        try:
            q = candidates[code]
            f = financials.get(code)

            roe = f.roe if f else 0
            gross_margin = f.gross_margin if f else 0
            net_margin = f.net_margin if f else 0
            rev_growth = f.revenue_growth if f else 0
            profit_growth = f.profit_growth if f else 0
            debt_ratio = f.debt_ratio if f else 0

            pb = q.pb
            if pb <= 0:
                if code in preset_financials and preset_financials[code].get('pb', 0) > 0:
                    pb = preset_financials[code]['pb']
                elif q.pe > 0 and roe > 0:
                    pb = round(q.pe * roe / 100, 2)

            stock_dict = {
                "code": code,
                "name": q.name,
                "price": q.price,
                "change_pct": q.change_pct,
                "pe": q.pe,
                "pb": pb,
                "market_cap": q.market_cap,
                "turnover_rate": q.turnover,
                "amount": q.amount,
                "roe": roe,
                "gross_margin": gross_margin,
                "net_margin": net_margin,
                "rev_growth": rev_growth,
                "profit_growth": profit_growth,
                "debt_ratio": debt_ratio,
            }

            tech_data = tech_cache.get(code)
            eval_result = evaluate_stock(stock_dict, tech_data=tech_data)
            if not eval_result:
                return None

            score = eval_result.get("score", 0)
            if score < 25:  # V5.5: 降低最低分阈值(V5.5更严格)
                return None

            result = {
                "code": code,
                "name": q.name,
                "price": q.price,
                "change_pct": round(q.change_pct, 2),
                "pe": round(q.pe, 1) if q.pe > 0 else 0,
                "pb": round(pb, 2),
                "market_cap": round(q.market_cap, 2),
                "turnover_rate": round(q.turnover, 2) if q.turnover else 0,
                "amount": round(q.amount, 2) if q.amount else 0,
                "roe": round(roe, 1),
                "gross_margin": round(gross_margin, 1),
                "net_margin": round(net_margin, 1),
                "rev_growth": round(rev_growth, 1),
                "profit_growth": round(profit_growth, 1),
                "debt_ratio": round(debt_ratio, 1),
                "score": round(score, 1),
                "total_score": round(score, 1),
                "v5_score": eval_result.get("v5_score", round(score, 1)),
                "v5_factors": eval_result.get("v5_factors", {}),
                "v5_reasons": eval_result.get("v5_reasons", []),
                "v5_recommendation": eval_result.get("v5_recommendation", ""),
                "dimensions": eval_result.get("dimensions", {
                    "profitability": 0, "growth": 0, "health": 0,
                    "valuation": 0, "cashflow": 0,
                }),
                "buy_sell": eval_result.get("buy_sell"),
                "reasons": eval_result.get("reasons", []),
                "industry": eval_result.get("industry", "未知"),
                "sector": eval_result.get("sector_type", "default"),
            }
            return result
        except Exception as e:
            log.debug(f"每日评分失败: {code}, {e}")
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

    results.sort(key=lambda x: x.get("v5_score", x["score"]), reverse=True)

    top_results = results[:max(top_n * 5, top_n)]  # V5.5: 更宽的候选池，确保筛选后仍有足够股票

    # Industry concentration limit: max 2 stocks per industry, but keep target count.
    target_count = min(top_n, len(results))
    top_results = industry_concentration_limit(
        top_results,
        max_per_industry=2,
        min_count=target_count,
    )[:target_count]

    # Add industry/sector info to results
    if env:
        env_info = {
            "market_status": env.status,
            "market_trend": env.trend,
            "position_multiplier": round(env.position_size_multiplier(), 2),
            "score_multiplier": round(env.score_multiplier(), 2),
        }
        for r in top_results:
            r["env_info"] = env_info

    if top_results:
        _fetch_industry_for_results(top_results)
        top_results[0]['_total_scanned'] = total_scanned

    log.info(f"每日选股: 扫描 {total_scanned} 只, 符合条件 {len(results)} 只, 返回 {len(top_results)} 只")
    return top_results

