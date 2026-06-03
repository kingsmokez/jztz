"""Flask Blueprint - 竞价选股路由

恢复旧版完整竞价选股逻辑，前端模板期望字段:
code, name, price, gap_pct(高开幅度), volume_ratio(量比), turnover_ratio(换手率),
auction_amount_pct(竞价额占比), change_pct, circ_cap, amount, score
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime

from flask import Blueprint, render_template, jsonify

from modules.logger import log

auction_bp = Blueprint("auction", __name__)

AUCTION_PICK_LOCK = threading.Lock()
AUCTION_PICK_DATA: dict = {}


def _format_auction_stocks(data) -> list[dict]:
    if not data:
        return []
    stocks = []
    for item in data:
        if not isinstance(item, dict):
            continue

        # Handle gap_pct: ensure it's stored as percentage (e.g., 5.12 for 5.12%)
        gap_pct = item.get("gap_pct")
        if gap_pct is None:
            # Fallback to change_pct
            change_pct = item.get("change_pct", 0)
            if abs(change_pct) < 1:
                # change_pct is in decimal form (e.g., 0.0512), convert to percentage
                gap_pct = change_pct * 100.0
            else:
                # change_pct is already in percentage form (e.g., 5.12)
                gap_pct = change_pct

        stock = {
            "code": item.get("code", ""),
            "name": item.get("name", ""),
            "price": item.get("price", 0),
            "change_pct": item.get("change_pct", 0),
            "gap_pct": gap_pct,
            "volume_ratio": item.get("volume_ratio", 0),
            "turnover_ratio": item.get("turnover_ratio", item.get("turnover_rate", 0)),
            "auction_amount_pct": item.get("auction_amount_pct", 0),
            "circ_cap": item.get("circ_cap", item.get("market_cap", 0)),
            "amount": item.get("amount", 0),
            "score": item.get("final_score", item.get("score", 0)),
            "final_score": item.get("final_score", 0),
            "phase1_score": item.get("phase1_score", 0),
            "phase2_score": item.get("phase2_score", 0),
            "recommendation": item.get("recommendation", ""),
            "market_status": item.get("market_status", ""),
            "industry": item.get("industry", ""),
            "sector": item.get("sector", ""),
        }
        stocks.append(stock)
    return stocks
AUCTION_PICK_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'auction_pick_cache.json')


def _load_auction_cache():
    global AUCTION_PICK_DATA
    try:
        if os.path.exists(AUCTION_PICK_FILE):
            with open(AUCTION_PICK_FILE, 'r', encoding='utf-8') as f:
                AUCTION_PICK_DATA = json.load(f)
    except Exception as e:
        log.warning(f"加载竞价选股缓存失败: {e}")
    if not AUCTION_PICK_DATA:
        AUCTION_PICK_DATA = {
            "date": datetime.now().strftime('%Y-%m-%d'),
            "stocks": [],
            "pick_time": None,
            "last_update": None,
            "market_info": {},
            "candidate_pool": [],
            "preselect_time": None,
            "confirm_time": None,
        }


def _save_auction_cache():
    try:
        with open(AUCTION_PICK_FILE, 'w', encoding='utf-8') as f:
            json.dump(AUCTION_PICK_DATA, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"保存竞价选股缓存失败: {e}")


_load_auction_cache()


@auction_bp.route("/auction_pick")
def auction_pick():
    return render_template("auction_pick.html")


@auction_bp.route("/api/auction_pick")
def api_auction_pick():
    now = datetime.now()

    # Note: get_auction_data() from web_app returns data from modules/auction_picker
    # which is incomplete (missing turnover_ratio, gap_pct, etc.).
    # We skip it and always use the local cache or fetch fresh data.

    current_hour = now.hour
    current_minute = now.minute

    is_auction_time = (current_hour == 9 and current_minute >= 25) or \
                      (current_hour == 9 and current_minute < 35)

    last_update = AUCTION_PICK_DATA.get('last_update')
    need_refresh = False

    if last_update:
        try:
            last_dt = datetime.strptime(last_update, '%Y-%m-%d %H:%M:%S')
            if (now - last_dt).total_seconds() > 3600:
                need_refresh = True
        except Exception:
            need_refresh = True
    else:
        need_refresh = True

    if is_auction_time or need_refresh:
        _execute_auction_pick()

    with AUCTION_PICK_LOCK:
        data = dict(AUCTION_PICK_DATA) if AUCTION_PICK_DATA else {}

    return jsonify({
        "success": True,
        "stocks": data.get('stocks', []),
        "pick_time": data.get('pick_time'),
        "last_update": data.get('last_update'),
        "market_info": data.get('market_info', {}),
        "candidate_count": len(data.get('candidate_pool', [])),
        "preselect_time": data.get('preselect_time'),
        "confirm_time": data.get('confirm_time'),
    })


@auction_bp.route("/api/auction_confirm")
def api_auction_confirm():
    try:
        now = datetime.now()
        log.info(f"执行竞价确认（第二阶段）... {now.strftime('%H:%M:%S')}")

        market_info = _get_market_status()

        if not market_info.get('market_ok', True):
            with AUCTION_PICK_LOCK:
                AUCTION_PICK_DATA['stocks'] = []
                AUCTION_PICK_DATA['pick_time'] = now.strftime('%H:%M:%S')
                AUCTION_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
                AUCTION_PICK_DATA['market_info'] = market_info
                _save_auction_cache()
            return jsonify({
                "success": True,
                "stocks": [],
                "pick_time": now.strftime('%H:%M:%S'),
                "market_info": market_info,
                "message": "大盘环境不佳，不开新仓"
            })

        candidates = AUCTION_PICK_DATA.get('candidate_pool', [])
        if not candidates:
            candidates = _get_auction_candidates()
        else:
            refreshed = _get_auction_candidates()
            if refreshed:
                candidates = refreshed

        if not candidates:
            return jsonify({
                "success": True,
                "stocks": [],
                "pick_time": now.strftime('%H:%M:%S'),
                "market_info": market_info,
                "message": "无候选股票，请先执行第一阶段预选"
            })

        confirmed_stocks = []
        idx_gap = market_info.get('idx_gap', 0)
        min_gap = 0.02 if idx_gap > 0.015 else 0.01

        for stock in candidates:
            gap_ok = min_gap <= stock.get('gap_pct', 0) <= 0.045
            volume_ratio_ok = 1.8 <= stock.get('volume_ratio', 0) <= 5
            auction_amount_ok = stock.get('auction_amount_pct', 0) >= 0.03
            stronger_than_market = stock.get('gap_pct', 0) > idx_gap

            if gap_ok and volume_ratio_ok and auction_amount_ok and stronger_than_market:
                score = (
                    stock.get('gap_pct', 0) * 100 * 2 +
                    min(stock.get('gap_pct', 0) / max(idx_gap, 0.001), 5) * 10 +
                    stock.get('volume_ratio', 0) * 5
                )
                stock['score'] = score
                stock['gap_ok'] = gap_ok
                stock['volume_ratio_ok'] = volume_ratio_ok
                stock['auction_amount_ok'] = auction_amount_ok
                stock['stronger_than_market'] = stronger_than_market
                confirmed_stocks.append(stock)

        confirmed_stocks.sort(key=lambda x: x.get('score', 0), reverse=True)
        confirmed_stocks = confirmed_stocks[:5]

        with AUCTION_PICK_LOCK:
            AUCTION_PICK_DATA['stocks'] = confirmed_stocks
            AUCTION_PICK_DATA['pick_time'] = now.strftime('%H:%M:%S')
            AUCTION_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
            AUCTION_PICK_DATA['confirm_time'] = now.strftime('%Y-%m-%d %H:%M:%S')
            AUCTION_PICK_DATA['market_info'] = market_info
            if not AUCTION_PICK_DATA.get('candidate_pool'):
                AUCTION_PICK_DATA['candidate_pool'] = candidates
            _save_auction_cache()

        log.info(f"竞价确认: {len(confirmed_stocks)}只股票")

        return jsonify({
            "success": True,
            "stocks": confirmed_stocks,
            "pick_time": now.strftime('%H:%M:%S'),
            "last_update": now.strftime('%Y-%m-%d %H:%M:%S'),
            "market_info": market_info,
            "candidate_count": len(candidates),
        })

    except Exception as e:
        log.error(f"竞价确认失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e), "stocks": []})


@auction_bp.route("/api/auction_preselect")
def api_auction_preselect():
    try:
        now = datetime.now()
        candidates = _get_auction_candidates()

        with AUCTION_PICK_LOCK:
            AUCTION_PICK_DATA['candidate_pool'] = candidates
            AUCTION_PICK_DATA['preselect_time'] = now.strftime('%Y-%m-%d %H:%M:%S')
            AUCTION_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
            _save_auction_cache()

        log.info(f"竞价预选完成: {len(candidates)}只候选")
        return jsonify({
            "success": True,
            "candidate_count": len(candidates),
            "preselect_time": now.strftime('%Y-%m-%d %H:%M:%S'),
        })
    except Exception as e:
        log.error(f"竞价预选失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)})


@auction_bp.route("/api/auction_status")
def api_auction_status():
    now = datetime.now()
    current_hour = now.hour
    can_preselect = 9 <= current_hour <= 22

    with AUCTION_PICK_LOCK:
        data = dict(AUCTION_PICK_DATA) if AUCTION_PICK_DATA else {}

    return jsonify({
        "success": True,
        "can_preselect": can_preselect,
        "candidate_count": len(data.get('candidate_pool', [])),
        "preselect_done_today": bool(data.get('candidate_pool')),
        "preselect_time": data.get('preselect_time'),
    })


@auction_bp.route("/api/auction_candidate_pool")
def api_auction_candidate_pool():
    with AUCTION_PICK_LOCK:
        data = dict(AUCTION_PICK_DATA) if AUCTION_PICK_DATA else {}

    return jsonify({
        "success": True,
        "candidates": data.get('candidate_pool', []),
        "preselect_time": data.get('preselect_time'),
    })


@auction_bp.route("/api/auction_clear")
def api_auction_clear():
    global AUCTION_PICK_DATA
    with AUCTION_PICK_LOCK:
        AUCTION_PICK_DATA = {
            "date": datetime.now().strftime('%Y-%m-%d'),
            "stocks": [],
            "pick_time": None,
            "last_update": None,
            "market_info": {},
            "candidate_pool": [],
            "preselect_time": None,
            "confirm_time": None,
        }
        _save_auction_cache()
    return jsonify({"success": True, "message": "竞价选股缓存已清除"})


# === 竞价选股核心逻辑（恢复旧版）===

def _get_market_status():
    try:
        from modules.http_client import session
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/"
        }
        url = "http://qt.gtimg.cn/q=sh000300"
        resp = session.get(url, headers=headers, timeout=10)
        lines = resp.text.strip().split(';')

        idx300_prev_close = 0
        idx300_open = 0

        for line in lines:
            if '=' in line:
                content = line.split('=', 1)[1].strip('"')
            else:
                continue
            parts = content.split('~')
            if len(parts) > 10:
                idx300_price = float(parts[3]) if parts[3] else 0
                idx300_prev_close = float(parts[4]) if parts[4] else 0
                idx300_open = float(parts[5]) if parts[5] else idx300_prev_close
                break

        prev_close = idx300_prev_close
        idx_gap = (idx300_open / prev_close - 1) if prev_close > 0 else 0
        market_ok = idx_gap >= -0.015

        return {
            "idx300_close": idx300_prev_close,
            "idx_open": idx300_open,
            "idx_gap": idx_gap,
            "market_ok": market_ok,
        }
    except Exception as e:
        log.warning(f"获取大盘状态失败: {e}")
        return {"idx300_close": 0, "idx_open": 0, "idx_gap": 0, "market_ok": True}


def _get_auction_candidates():
    try:
        from modules.http_client import session
        candidates = []
        seen_codes = set()
        log.info("获取竞价候选池(新浪API)...")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn/"
        }

        sina_url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

        for page in range(1, 6):
            params = {
                "page": page,
                "num": 100,
                "sort": "changepercent",
                "asc": 0,
                "node": "hs_a",
                "symbol": "",
                "_s_r_a": "page"
            }

            resp = session.get(sina_url, params=params, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue

            try:
                stocks_data = resp.json()
            except Exception:
                continue

            if not isinstance(stocks_data, list):
                continue

            for s in stocks_data:
                try:
                    code = s.get("code", "")
                    name = s.get("name", "")

                    if code.startswith("688"):
                        continue
                    if code.startswith("8") or code.startswith("4"):
                        continue
                    if "ST" in name or "退" in name or "*" in name:
                        continue

                    change_pct = float(s.get("changepercent", 0)) / 100
                    if change_pct < -0.05 or change_pct > 0.095:
                        continue

                    circ_cap = float(s.get("nmc", 0)) / 10000
                    if circ_cap < 20 or circ_cap > 500:
                        continue

                    amount = float(s.get("amount", 0)) / 100000000
                    if amount < 1 or amount > 50:
                        continue

                    if code in seen_codes:
                        continue

                    open_price = float(s.get("open", 0)) if s.get("open") else 0
                    settlement = float(s.get("settlement", 0)) if s.get("settlement") else 0
                    gap_pct = (open_price / settlement - 1) if settlement > 0 else 0

                    turnover = float(s.get("turnoverratio", 0)) if s.get("turnoverratio") else 0
                    # 量比估算: 基于换手率的更合理近似
                    # 换手率1% → 量比约0.8; 2% → 1.2; 3% → 1.5; 5% → 2.5; 8% → 4.0
                    if turnover <= 0:
                        volume_ratio = 1.0
                    elif turnover < 0.5:
                        volume_ratio = round(turnover * 0.6, 2)
                    elif turnover < 2:
                        volume_ratio = round(0.5 + turnover * 0.35, 2)
                    elif turnover < 5:
                        volume_ratio = round(1.0 + (turnover - 2) * 0.5, 2)
                    else:
                        volume_ratio = round(2.5 + (turnover - 5) * 0.5, 2)

                    yesterday_amount = circ_cap * 100000000 * turnover * 0.008 if circ_cap > 0 and turnover > 0 else 1
                    auction_amount_pct = round(amount * 100000000 / yesterday_amount, 4) if yesterday_amount > 0 else 0

                    seen_codes.add(code)
                    candidates.append({
                        "code": code,
                        "name": name,
                        "price": float(s.get("trade", 0)) if s.get("trade") else 0,
                        "open": open_price,
                        "settlement": settlement,
                        "gap_pct": gap_pct,
                        "change_pct": change_pct,
                        "circ_cap": circ_cap,
                        "amount": amount,
                        "turnover_ratio": turnover,
                        "volume_ratio": volume_ratio,
                        "auction_amount_pct": auction_amount_pct,
                        "score": 0,
                    })
                except Exception:
                    continue

            if len(candidates) >= 50:
                break

        if len(candidates) < 20:
            log.info("候选较少，从成交量榜补充...")
            params2 = {
                "page": 1,
                "num": 100,
                "sort": "amount",
                "asc": 0,
                "node": "hs_a",
            }

            resp2 = session.get(sina_url, params=params2, headers=headers, timeout=15)
            if resp2.status_code == 200:
                try:
                    stocks2 = resp2.json()
                    if isinstance(stocks2, list):
                        for s in stocks2:
                            try:
                                code = s.get("code", "")
                                name = s.get("name", "")

                                if code in seen_codes:
                                    continue
                                if code.startswith("688"):
                                    continue
                                if code.startswith("8") or code.startswith("4"):
                                    continue
                                if "ST" in name or "退" in name or "*" in name:
                                    continue

                                change_pct = float(s.get("changepercent", 0)) / 100
                                if change_pct < -0.08 or change_pct > 0.095:
                                    continue

                                circ_cap = float(s.get("nmc", 0)) / 10000
                                if circ_cap < 15 or circ_cap > 600:
                                    continue

                                amount = float(s.get("amount", 0)) / 100000000
                                if amount < 0.5 or amount > 80:
                                    continue

                                open_price = float(s.get("open", 0)) if s.get("open") else 0
                                settlement = float(s.get("settlement", 0)) if s.get("settlement") else 0
                                gap_pct = (open_price / settlement - 1) if settlement > 0 else 0

                                turnover = float(s.get("turnoverratio", 0)) if s.get("turnoverratio") else 0
                                # 量比估算: 基于换手率的更合理近似
                                if turnover <= 0:
                                    volume_ratio = 1.0
                                elif turnover < 0.5:
                                    volume_ratio = round(turnover * 0.6, 2)
                                elif turnover < 2:
                                    volume_ratio = round(0.5 + turnover * 0.35, 2)
                                elif turnover < 5:
                                    volume_ratio = round(1.0 + (turnover - 2) * 0.5, 2)
                                else:
                                    volume_ratio = round(2.5 + (turnover - 5) * 0.5, 2)
                                yesterday_amount = circ_cap * 100000000 * turnover * 0.008 if circ_cap > 0 and turnover > 0 else 1
                                auction_amount_pct = round(amount * 100000000 / yesterday_amount, 4) if yesterday_amount > 0 else 0

                                seen_codes.add(code)
                                candidates.append({
                                    "code": code,
                                    "name": name,
                                    "price": float(s.get("trade", 0)) if s.get("trade") else 0,
                                    "open": open_price,
                                    "settlement": settlement,
                                    "gap_pct": gap_pct,
                                    "change_pct": change_pct,
                                    "circ_cap": circ_cap,
                                    "amount": amount,
                                    "turnover_ratio": turnover,
                                    "volume_ratio": volume_ratio,
                                    "auction_amount_pct": auction_amount_pct,
                                    "score": 0,
                                })
                            except Exception:
                                continue
                except Exception:
                    pass

        log.info(f"竞价候选池: {len(candidates)}只")
        return candidates

    except Exception as e:
        log.error(f"获取竞价候选池失败: {e}", exc_info=True)
        return []


def _execute_auction_pick():
    global AUCTION_PICK_DATA

    try:
        now = datetime.now()
        current_hour = now.hour

        # 允许非交易时间执行（用于测试和获取行业信息）
        # if current_hour < 9 or current_hour > 15:
        #     return

        log.info(f"执行竞价选股... {now.strftime('%H:%M:%S')}")

        market_info = _get_market_status()

        if not market_info.get('market_ok', True):
            with AUCTION_PICK_LOCK:
                AUCTION_PICK_DATA['stocks'] = []
                AUCTION_PICK_DATA['pick_time'] = now.strftime('%H:%M:%S')
                AUCTION_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
                AUCTION_PICK_DATA['market_info'] = market_info
                _save_auction_cache()
            log.info("大盘环境不佳，不开新仓")
            return

        candidates = _get_auction_candidates()

        if not candidates:
            with AUCTION_PICK_LOCK:
                AUCTION_PICK_DATA['stocks'] = []
                AUCTION_PICK_DATA['pick_time'] = now.strftime('%H:%M:%S')
                AUCTION_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
                AUCTION_PICK_DATA['market_info'] = market_info
                AUCTION_PICK_DATA['candidate_pool'] = []
                _save_auction_cache()
            log.info("无候选股票")
            return

        log.info(f"候选池: {len(candidates)}只")

        confirmed_stocks = []
        idx_gap = market_info.get('idx_gap', 0)
        min_gap = 0.02 if idx_gap > 0.015 else 0.01

        for stock in candidates:
            gap_ok = min_gap <= stock.get('gap_pct', 0) <= 0.045
            volume_ratio_ok = 1.8 <= stock.get('volume_ratio', 0) <= 5
            auction_amount_ok = stock.get('auction_amount_pct', 0) >= 0.03
            stronger_than_market = stock.get('gap_pct', 0) > idx_gap

            if gap_ok and volume_ratio_ok and auction_amount_ok and stronger_than_market:
                score = (
                    stock.get('gap_pct', 0) * 100 * 2 +
                    min(stock.get('gap_pct', 0) / max(idx_gap, 0.001), 5) * 10 +
                    stock.get('volume_ratio', 0) * 5
                )
                stock['score'] = score
                stock['gap_ok'] = gap_ok
                stock['volume_ratio_ok'] = volume_ratio_ok
                stock['auction_amount_ok'] = auction_amount_ok
                stock['stronger_than_market'] = stronger_than_market
                confirmed_stocks.append(stock)

        confirmed_stocks.sort(key=lambda x: x.get('score', 0), reverse=True)
        confirmed_stocks = confirmed_stocks[:3]

        # 添加行业信息
        if confirmed_stocks:
            from modules.data_fetcher import get_stock_industry
            for stock in confirmed_stocks:
                try:
                    info = get_stock_industry(stock["code"])
                    stock["industry"] = info.get("industry", "其他")
                    stock["sector"] = info.get("sector_type", "default")
                except Exception:
                    stock["industry"] = "其他"
                    stock["sector"] = "default"

        with AUCTION_PICK_LOCK:
            AUCTION_PICK_DATA['stocks'] = confirmed_stocks
            AUCTION_PICK_DATA['pick_time'] = now.strftime('%H:%M:%S')
            AUCTION_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
            AUCTION_PICK_DATA['market_info'] = market_info
            AUCTION_PICK_DATA['candidate_pool'] = candidates
            _save_auction_cache()

        log.info(f"竞价确认: {len(confirmed_stocks)}只股票")

    except Exception as e:
        log.error(f"竞价选股失败: {e}", exc_info=True)


def auto_auction_preselect():
    """15:30自动执行竞价预选 - 生成候选池供次日竞价确认使用"""
    global AUCTION_PICK_DATA
    try:
        now = datetime.now()
        log.info(f"执行自动竞价预选... {now.strftime('%H:%M:%S')}")

        candidates = _get_auction_candidates()

        with AUCTION_PICK_LOCK:
            AUCTION_PICK_DATA['candidate_pool'] = candidates
            AUCTION_PICK_DATA['preselect_time'] = now.strftime('%Y-%m-%d %H:%M:%S')
            AUCTION_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
            _save_auction_cache()

        log.info(f"自动竞价预选完成: {len(candidates)}只候选")
    except Exception as e:
        log.error(f"自动竞价预选失败: {e}", exc_info=True)
