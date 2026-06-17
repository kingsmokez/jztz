"""集合竞价选股模块 - 两阶段竞价逻辑

Phase 1: 预筛选 (趋势/量能/位置 三维筛选)
Phase 2: 确认 (跳空/量比/竞价金额/大盘 四维确认)

输出字段与 auction_pick.html 模板兼容:
- code, name, price, change_pct, market_cap
- gap_pct (高开幅度%)
- volume_ratio (量比)
- turnover_rate (换手率%)
- auction_amount (竞价成交额)
- pe, pb, roe, gross_margin, net_margin, rev_growth, profit_growth, debt_ratio
- phase1_score, phase2_score, final_score, recommendation, market_status
"""

from __future__ import annotations

__all__ = [
    "get_market_status",
    "get_auction_candidates",
    "get_candidates_from_tencent",
    "run_auction_picker",
    "compare_auction",
]

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Optional

from modules.data_fetcher import get_financial_data, get_realtime_quotes, get_stock_industry, preload_industry_cache
from modules.http_client import HEADERS, session
from modules.logger import log
from modules.market_env import get_market_env
from modules.scoring import calculate_buy_sell
from modules.models import filter_eligible_stocks
from modules.scoring import full_score, rank_stocks


def get_market_status() -> dict[str, Any]:
    """获取大盘(CSI 300)状态，返回市场状态描述和指标

    Returns:
        dict with keys:
        - status: "大涨"/"上涨"/"震荡"/"下跌"/"大跌"
        - change_pct: CSI 300 change percentage
        - volume_ratio: volume ratio vs previous day
    """
    try:
        url = "https://hq.sinajs.cn/list=sh000300"
        resp = session.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "gbk"
        text = resp.text.strip()

        # Parse: var hq_str_sh000300="...";
        match = re.search(r'"([^"]+)"', text)
        if not match:
            return {"status": "未知", "change_pct": 0.0, "volume_ratio": 1.0}

        parts = match.group(1).split(",")
        if len(parts) < 5:
            return {"status": "未知", "change_pct": 0.0, "volume_ratio": 1.0}

        # Sina format: name, today_open, yesterday_close, current, high, low, ...
        name = parts[0]
        today_open = float(parts[1]) if parts[1] else 0
        prev_close = float(parts[2]) if parts[2] else 0
        current = float(parts[3]) if parts[3] else 0
        high = float(parts[4]) if parts[4] else 0

        if prev_close <= 0:
            return {"status": "未知", "change_pct": 0.0, "volume_ratio": 1.0}

        change_pct = (current - prev_close) / prev_close * 100

        # Volume ratio (approximate from available data)
        volume_ratio = 1.0
        if len(parts) > 8:
            try:
                volume = float(parts[8]) if parts[8] else 0
                # Rough estimate: compare to average
                if volume > 0:
                    # Cannot compute real volume_ratio from single volume value
                    # Use neutral default; real volume_ratio comes from candidates data
                    volume_ratio = 1.0
            except (ValueError, IndexError):
                pass

        # Determine market status
        if change_pct > 2:
            status = "大涨"
        elif change_pct > 0.5:
            status = "上涨"
        elif change_pct > -0.5:
            status = "震荡"
        elif change_pct > -2:
            status = "下跌"
        else:
            status = "大跌"

        return {
            "status": status,
            "change_pct": round(change_pct, 2),
            "volume_ratio": volume_ratio,
            "name": name,
            "current": current,
            "prev_close": prev_close,
        }
    except Exception as e:
        log.warning(f"获取大盘状态失败: {e}")
        return {"status": "未知", "change_pct": 0.0, "volume_ratio": 1.0}


def get_auction_candidates() -> list[dict[str, Any]]:
    """从新浪API获取集合竞价候选股票

    URL: https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_Bill.GetBillList
    Filters by amount >= 5000000 (500万)

    Returns:
        List of dicts with: code, name, amount, price, change_pct, volume_ratio
    """
    candidates: list[dict[str, Any]] = []
    try:
        url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_Bill.GetBillList"
        params = {
            "page": 1,
            "num": 60,
            "sort": "ticktime",
            "asc": 0,
            "volume": 50000,
            "type": 0,
            "node": "hs_a",
        }

        resp = session.get(url, params=params, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            log.warning(f"新浪竞价API请求失败: {resp.status_code}")
            return candidates

        try:
            data = resp.json()
        except json.JSONDecodeError:
            # Sina sometimes returns non-JSON, try to parse
            text = resp.text.strip()
            if text.startswith("(") and text.endswith(")"):
                text = text[1:-1]
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                log.warning("新浪竞价API返回非JSON数据")
                return candidates

        if not isinstance(data, list):
            log.warning("新浪竞价API返回数据格式异常")
            return candidates

        for item in data:
            try:
                code = str(item.get("symbol", "")).strip()
                name = str(item.get("name", "")).strip()
                amount = float(item.get("amount", 0))

                # Filter: amount >= 500万
                if amount < 5000000:
                    continue

                price = float(item.get("price", 0))
                change_pct = float(item.get("changepercent", 0))
                volume_ratio = float(item.get("volume_ratio", 1.0))
                turnover_rate = float(item.get("turnoverratio", 0))

                # changepercent from Sina API is already a percentage (e.g., 5.12 for 5.12%)
                # Keep as-is for display, internal calculations use decimal
                change_pct_display = change_pct
                change_pct_decimal = change_pct / 100.0

                candidates.append({
                    "code": code,
                    "name": name,
                    "amount": amount,
                    "price": price,
                    "change_pct": change_pct_display,
                    "change_pct_decimal": change_pct_decimal,
                    "volume_ratio": volume_ratio,
                    "turnover_rate": turnover_rate,
                })
            except (ValueError, TypeError):
                continue

        log.info(f"新浪竞价候选: {len(candidates)}只")
        return candidates

    except Exception as e:
        log.warning(f"获取新浪竞价候选失败: {e}")
        return candidates


def get_candidates_from_tencent() -> list[dict[str, Any]]:
    """从腾讯API获取A股列表作为备选数据源

    Gets all A-share list from Tencent API, filters by market cap >= 30亿
    and PE between 5 and 200.

    Returns:
        List of candidate dicts with code, name, price, change_pct, etc.
    """
    candidates: list[dict[str, Any]] = []
    try:
        # Tencent API for A-share list
        url = "http://qt.gtimg.cn/q=sh000001,sz399001"
        resp = session.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "gbk"

        # This is a simplified fallback - in practice we'd iterate all stocks
        # For now, return empty and let the main flow handle it
        log.info("腾讯备选数据源: 暂无数据")
        return candidates

    except Exception as e:
        log.warning(f"获取腾讯备选数据失败: {e}")
        return candidates


def _get_auction_stocks() -> list[dict[str, Any]]:
    """备选数据源：从其他API获取竞价数据

    Returns:
        List of stock dicts compatible with the two-phase scoring system
    """
    candidates: list[dict[str, Any]] = []
    try:
        # Fallback to a basic market data fetch
        # Try to get some active stocks from Sina
        url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
        params = {
            "page": 1,
            "num": 50,
            "sort": "amount",
            "asc": 0,
            "node": "hs_a",
            "_s_r_a": "page",
        }

        resp = session.get(url, params=params, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return candidates

        try:
            data = resp.json()
        except json.JSONDecodeError:
            return candidates

        if not isinstance(data, list):
            return candidates

        for item in data:
            try:
                code = str(item.get("code", "")).strip()
                name = str(item.get("name", "")).strip()
                price = float(item.get("trade", 0))
                change_pct = float(item.get("changepercent", 0))
                volume = float(item.get("volume", 0))
                amount = float(item.get("amount", 0))

                # Calculate volume ratio (approximate)
                turnover = float(item.get("turnoverratio", 0))
                volume_ratio = round(turnover / 1.5, 2) if turnover > 0 else 1.0

                candidates.append({
                    "code": code,
                    "name": name,
                    "price": price,
                    "change_pct": change_pct,
                    "volume_ratio": volume_ratio,
                    "volume": volume,
                    "amount": amount,
                })
            except (ValueError, TypeError):
                continue

        return candidates

    except Exception as e:
        log.warning(f"备选数据源获取失败: {e}")
        return candidates


def _phase1_score(stock: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """Phase 1: Preselection scoring (趋势/量能/位置 三维筛选)

    Args:
        stock: Stock data dict with price, volume, etc.

    Returns:
        Tuple of (score, details_dict)
        Score ranges 0-100 (trend: 0-35, volume: 0-35, position: 0-30)
    """
    details: dict[str, Any] = {}

    # Trend filter: price vs MA5/MA10 relationship
    trend_score = 0.0
    price = stock.get("price", 0)
    change_pct = stock.get("change_pct", 0)

    if change_pct > 5:
        trend_score = 35
    elif change_pct > 2:
        trend_score = 28
    elif change_pct > 0:
        trend_score = 20
    elif change_pct > -2:
        trend_score = 10
    else:
        trend_score = 5

    details["trend_score"] = trend_score
    details["trend_reason"] = f"change_pct={change_pct:.2f}%"

    # Volume filter: volume ratio > 1.2 or volume > 5-day average
    volume_ratio = stock.get("volume_ratio", 1.0)
    volume_score = 0.0

    # 量比评分: 适度放量最佳(1.2-3), 极度放量(>5)可能是异常需降分
    if volume_ratio > 5:
        volume_score = 10  # 异常放量，警惕操纵或恐慌
    elif volume_ratio > 3:
        volume_score = 22  # 明显放量，谨慎
    elif volume_ratio > 1.5:
        volume_score = 30  # 适度放量，最佳区间
    elif volume_ratio > 1.2:
        volume_score = 25  # 温和放量
    elif volume_ratio > 0.8:
        volume_score = 10  # 缩量
    else:
        volume_score = 5   # 严重缩量

    details["volume_score"] = volume_score
    details["volume_ratio"] = volume_ratio

    # Position filter: avoid stocks that are already over-extended
    # Prefer stocks in a reasonable range (not already limit-up or limit-down)
    position_score = 0.0
    if 0 <= change_pct <= 5:
        # Positive but not overextended — best position
        position_score = 30
    elif -2 <= change_pct < 0:
        # Slight pullback — could be opportunity
        position_score = 25
    elif 5 < change_pct <= 7:
        # Extended but not limit-up
        position_score = 15
    elif -5 <= change_pct < -2:
        # Deeper pullback
        position_score = 15
    elif change_pct > 7:
        # Already overextended (near limit-up) — don't chase
        position_score = 5
    else:
        # Severe drop
        position_score = 5

    details["position_score"] = position_score

    total = trend_score + volume_score + position_score
    return total, details


def _phase2_score(
    stock: dict[str, Any],
    market_status: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    """Phase 2: Confirmation scoring (跳空/量比/竞价金额/大盘 四维确认)

    Args:
        stock: Stock data dict
        market_status: Market status from get_market_status()

    Returns:
        Tuple of (score, details_dict)
        Score ranges 0-100 (gap: 0-25, volume: 0-25, amount: 0-25, market: 0-25)
    """
    details: dict[str, Any] = {}

    # Gap filter: positive gap (open > previous close) preferred
    gap_score = 0.0
    gap_pct = stock.get("gap_pct", 0)
    if gap_pct > 0:
        if 1 <= gap_pct <= 5:
            gap_score = 25
        elif gap_pct <= 7:
            gap_score = 20
        elif gap_pct > 7:
            gap_score = 15
        else:
            gap_score = 10
    else:
        gap_score = max(0, 10 + gap_pct * 5)  # Negative gap reduces score

    details["gap_score"] = gap_score
    details["gap_pct"] = gap_pct

    # Volume ratio filter: > 1.5 preferred
    volume_ratio = stock.get("volume_ratio", 1.0)
    volume_ratio_score = 0.0
    # 量比确认: 适度放量确认趋势, 极度放量需警惕
    if volume_ratio > 5:
        volume_ratio_score = 8   # 异常放量，警惕
    elif volume_ratio > 3:
        volume_ratio_score = 18  # 明显放量
    elif volume_ratio > 1.5:
        volume_ratio_score = 22  # 适度放量，最佳
    elif volume_ratio > 1.0:
        volume_ratio_score = 15  # 温和放量
    else:
        volume_ratio_score = 5   # 缩量

    details["volume_ratio_score"] = volume_ratio_score

    # Auction amount filter: > 2000万 preferred
    amount = stock.get("amount", 0)
    auction_amount = stock.get("auction_amount", amount)
    amount_score = 0.0
    if auction_amount >= 50000000:  # >= 5000万
        amount_score = 25
    elif auction_amount >= 20000000:  # >= 2000万
        amount_score = 22
    elif auction_amount >= 10000000:  # >= 1000万
        amount_score = 18
    elif auction_amount >= 5000000:  # >= 500万
        amount_score = 12
    elif auction_amount >= 1000000:  # >= 100万
        amount_score = 8
    else:
        amount_score = 5

    details["amount_score"] = amount_score
    details["auction_amount"] = auction_amount

    # Market status filter: bonus for good market, penalty for bad
    market_score = 0.0
    mkt_status = market_status.get("status", "未知")
    mkt_change = market_status.get("change_pct", 0)

    if mkt_status == "大涨":
        market_score = 25
    elif mkt_status == "上涨":
        market_score = 20
    elif mkt_status == "震荡":
        market_score = 15
    elif mkt_status == "下跌":
        market_score = 8
    elif mkt_status == "大跌":
        market_score = 3
    else:
        market_score = 10

    details["market_score"] = market_score
    details["market_status"] = mkt_status

    total = gap_score + volume_ratio_score + amount_score + market_score
    return total, details


def run_auction_picker(top_n: int = 20) -> list[dict[str, Any]]:
    """执行集合竞价选股，返回与模板兼容的股票列表

    Two-phase logic:
    1. Phase 1 (Preselection): Trend/Volume/Position 3D screening
    2. Phase 2 (Confirmation): Gap/VolumeRatio/AuctionAmount/Market 4D confirmation

    Returns:
        List of stock dicts with fields:
        - code, name, price, change_pct, volume_ratio, auction_amount
        - phase1_score, phase2_score, final_score
        - recommendation, market_status
    """
    env = None
    try:
        env = get_market_env()
        if not env.can_pick():
            log.warning(f"竞价选股: 市场环境不佳(status={env.status}, trend={env.trend})，暂停选股")
            return []
        top_n = env.adjusted_top_n(top_n)
        log.info(f"竞价选股: 市场环境 status={env.status}, trend={env.trend}, top_n={top_n}")
    except Exception as e:
        log.warning(f"竞价选股: 市场环境检测失败: {e}，使用默认参数")
        env = None

    log.info("开始集合竞价选股...")

    # Step 1: Get market status
    market_status = get_market_status()
    log.info(f"大盘状态: {market_status['status']} (change={market_status['change_pct']:.2f}%)")

    # Step 2: Get auction candidates (primary source)
    candidates = get_auction_candidates()

    # Fallback to backup source if primary yields few results
    if len(candidates) < 10:
        log.info("主数据源候选较少，尝试备选数据源...")
        backup = _get_auction_stocks()
        if backup:
            # Merge, avoiding duplicates
            existing_codes = {c["code"] for c in candidates}
            for b in backup:
                if b["code"] not in existing_codes:
                    candidates.append(b)

    if not candidates:
        log.warning("竞价选股: 无候选股票")
        return []

    log.info(f"竞价选股: 共 {len(candidates)} 只候选股")
    preload_industry_cache([c["code"] for c in candidates if c.get("code")])

    # Step 3: Phase 1 - Preselection scoring
    phase1_results: list[dict[str, Any]] = []
    for stock in candidates:
        try:
            score, details = _phase1_score(stock)
            stock_copy = dict(stock)
            stock_copy["phase1_score"] = score
            stock_copy["phase1_details"] = details
            phase1_results.append(stock_copy)
        except Exception as e:
            log.debug(f"Phase1评分失败: {stock.get('code', '')}, {e}")
            continue

    # Filter: only pass stocks with phase1_score >= threshold
    phase1_threshold = 50  # Minimum to pass preselection
    phase1_passed = [s for s in phase1_results if s["phase1_score"] >= phase1_threshold]
    phase1_passed.sort(key=lambda x: x["phase1_score"], reverse=True)

    # Limit to top candidates for phase 2
    phase1_passed = phase1_passed[:min(len(phase1_passed), top_n * 3)]

    log.info(f"Phase 1 预筛选: {len(phase1_passed)}/{len(phase1_results)} 只通过")

    if not phase1_passed:
        log.warning("Phase 1 预筛选后无通过股票")
        return []

    # Step 4: Phase 2 - Confirmation scoring
    final_results: list[dict[str, Any]] = []
    for stock in phase1_passed:
        try:
            score, details = _phase2_score(stock, market_status)
            stock["phase2_score"] = score
            stock["phase2_details"] = details

            # Final score: weighted combination
            # Phase 1 (trend/volume/position) is foundational, Phase 2 (gap/volume/amount/market) is confirmation
            final_score = stock["phase1_score"] * 0.4 + score * 0.6
            stock["final_score"] = round(final_score, 2)

            # Recommendation based on final score
            if final_score >= 80:
                recommendation = "强烈推荐"
            elif final_score >= 65:
                recommendation = "推荐"
            elif final_score >= 50:
                recommendation = "关注"
            else:
                recommendation = "观望"

            stock["recommendation"] = recommendation
            stock["market_status"] = market_status["status"]

            final_results.append(stock)
        except Exception as e:
            log.debug(f"Phase2评分失败: {stock.get('code', '')}, {e}")
            continue

    # Sort by final score descending
    final_results.sort(key=lambda x: x["final_score"], reverse=True)
    top_results = final_results[:top_n]

    # Add industry info
    if top_results:
        def _fetch_industry(stock):
            try:
                info = get_stock_industry(stock["code"])
                stock["industry"] = info.get("industry", "未知")
                stock["sector"] = info.get("sector_type", "default")
            except Exception:
                stock["industry"] = "未知"
                stock["sector"] = "default"
        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(_fetch_industry, top_results))

    # Add buy/sell points + V5 evaluation + ROE filtering
    try:
        from modules.data_fetcher import get_financial_data
        fin_codes = [s.get('code', '') for s in top_results if s.get('code')]
        fin_map = get_financial_data(fin_codes)
    except Exception:
        fin_map = {}

    for stock in top_results:
        try:
            from modules.technical import calculate_technical_indicators
            from modules.scoring import multi_factor_evaluate
            code = stock.get('code', '')
            tech = calculate_technical_indicators(code, days=30) if code else None

            # Get financial data for proper evaluation
            fin = fin_map.get(code)
            roe = fin.roe if fin else 0
            pe = fin.pe if fin and fin.pe > 0 else 0
            pb = fin.pb if fin and fin.pb > 0 else 0
            gross_margin = fin.gross_margin if fin else 0
            net_margin = fin.net_margin if fin else 0
            rev_growth = fin.revenue_growth if fin else 0
            profit_growth = fin.profit_growth if fin else 0
            debt_ratio = fin.debt_ratio if fin else 0

            stock['roe'] = roe
            stock['pe'] = pe
            stock['pb'] = pb

            stock_for_bs = {
                'code': code,
                'name': stock.get('name', ''),
                'price': stock.get('price', 0),
                'pe': pe, 'pb': pb,
                'market_cap': 0,
                'turnover_rate': 0,
                'change_pct': stock.get('change_pct', 0),
                'roe': roe, 'gross_margin': gross_margin,
                'net_margin': net_margin, 'rev_growth': rev_growth,
                'profit_growth': profit_growth, 'debt_ratio': debt_ratio,
            }
            bs = calculate_buy_sell(stock_for_bs, stock.get('final_score', 50), tech_data=tech)
            if bs:
                stock['buy_sell'] = bs

            # V5 multi-factor evaluation
            try:
                v5_result = multi_factor_evaluate(stock_for_bs)
                if v5_result:
                    v5_total = v5_result.get('v5_total', 0)
                    # V5.5 ROE/Q penalty
                    if 0 < roe < 5:
                        v5_total *= 0.80
                    elif 0 < roe < 8:
                        v5_total *= 0.90
                    q_val = v5_result.get('v5_factors', {}).get('quality', 50)
                    if q_val < 25:
                        v5_total *= 0.85
                    stock['v5_score'] = round(v5_total, 1)
                    stock['v5_factors'] = v5_result.get('v5_factors')
                    stock['v5_reasons'] = v5_result.get('v5_reasons')
                    stock['v5_recommendation'] = v5_result.get('v5_recommendation')
            except Exception:
                pass
        except Exception:
            pass

    # V5.5: Filter out stocks with ROE < 0
    pre_filter = len(top_results)
    top_results = [s for s in top_results
                   if not (isinstance(s.get('roe'), (int, float)) and s.get('roe') < 0)]
    if len(top_results) < pre_filter:
        log.info(f'竞价ROE过滤: {pre_filter} -> {len(top_results)}')

    log.info(f"竞价选股完成: 入选 {len(top_results)} 只")
    for r in top_results[:5]:
        log.info(
            f"  {r['code']} {r['name']}: "
            f"final={r['final_score']:.1f} (p1={r['phase1_score']:.1f}, p2={r['phase2_score']:.1f}) "
            f"[{r['recommendation']}]"
        )

    return top_results


def compare_auction(params: dict) -> dict:
    """集合竞价对比分析 - 对多只股票同时评分并排名"""
    try:
        codes = params.get("codes", [])
        if not codes or len(codes) < 2:
            return {"success": False, "error": "至少需要2只股票进行对比"}

        quotes = get_realtime_quotes(codes)
        eligible = filter_eligible_stocks(quotes)

        if not eligible:
            return {"success": False, "error": "无合格股票"}

        financials = get_financial_data(list(eligible.keys()))

        scored = []
        for code, q in eligible.items():
            try:
                f = financials.get(code)
                result = full_score(q, f)
                scored.append(result)
            except Exception as e:
                log.debug(f"竞价对比评分失败: {code}, {e}")

        ranked = rank_stocks(scored)
        return {"success": True, "data": ranked}

    except Exception as e:
        log.error(f"竞价对比失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}