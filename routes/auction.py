"""Flask Blueprint - 竞价选股路由

V2 优化版（2026-06）：
  1. 评分公式重平衡：温和高开(2-5%)最佳，过度高开(>5%)降分；量比/资金权重提升
  2. 板块联动加分：集成 sector_rotation 模块（startup/accelerating 阶段加分）
  3. 负面过滤清单：昨日涨停/5日累计涨幅过大/ST等自动排除
  4. 买入建议输出：建议买入价区间、止损位、风险等级
  5. 大盘环境多维度：CSI300 当日gap + 前5日趋势 + 北向资金

前端模板期望字段:
code, name, price, gap_pct(高开幅度), volume_ratio(量比), turnover_ratio(换手率),
auction_amount_pct(竞价额占比), change_pct, circ_cap, amount, score
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
                data = json.load(f)
                # V5.5: Filter ROE<0 stocks from cache
                if isinstance(data, list):
                    data = [r for r in data
                            if not (isinstance(r.get('roe'), (int, float)) and r.get('roe') < 0)]
                    # 兼容旧格式：list → dict
                    AUCTION_PICK_DATA = {
                        "date": datetime.now().strftime('%Y-%m-%d'),
                        "stocks": data,
                        "pick_time": None,
                        "last_update": None,
                        "market_info": {},
                        "candidate_pool": [],
                        "preselect_time": None,
                        "confirm_time": None,
                    }
                    return
                elif isinstance(data, dict) and 'stocks' in data:
                    data['stocks'] = [r for r in data.get('stocks', [])
                                     if not (isinstance(r.get('roe'), (int, float)) and r.get('roe') < 0)]
                AUCTION_PICK_DATA = data
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


# === 历史选股记录（用于回测验证）===
AUCTION_HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'auction_history.jsonl')


def _append_auction_history(stocks: list, market_info: dict, pick_time: str, source: str = "live"):
    """将本次选股结果追加到历史记录文件，用于回测验证。

    每行一条 JSON 记录，包含日期、选股快照、大盘环境。
    后续可结合行情数据回测 3 日涨跌胜率。

    Args:
        stocks: 选出的股票列表（含 score/buy_advice 等 V2 字段）
        market_info: 大盘环境信息
        pick_time: 选股时间
        source: live=实盘 / backtest=历史回填
    """
    try:
        today = datetime.now().strftime('%Y-%m-%d') if source == "live" else market_info.get("date", "")
        record = {
            "date": today,
            "pick_time": pick_time,
            "source": source,
            "market_info": {
                "idx_gap": market_info.get("idx_gap", 0),
                "idx_5d_change_pct": market_info.get("idx_5d_change_pct", 0),
                "northbound_net": market_info.get("northbound_net", 0),
                "sentiment": market_info.get("sentiment", ""),
                "sentiment_score": market_info.get("sentiment_score", 0),
                "market_ok": market_info.get("market_ok", True),
            },
            "stocks": [
                {
                    "code": s.get("code", ""),
                    "name": s.get("name", ""),
                    "industry": s.get("industry", ""),
                    "open": s.get("open", 0),
                    "price": s.get("price", 0),
                    "gap_pct": s.get("gap_pct", 0),
                    "volume_ratio": s.get("volume_ratio", 0),
                    "amount": s.get("amount", 0),
                    "turnover_ratio": s.get("turnover_ratio", 0),
                    "score": s.get("score", 0),
                    "sector_bonus": s.get("sector_bonus", 0),
                    "buy_advice": s.get("buy_advice", {}),
                    "score_breakdown": s.get("score_breakdown", {}),
                    "strategy_branch": s.get("strategy_branch", "high_open"),
                }
                for s in stocks
            ],
        }
        with open(AUCTION_HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.info(f"历史选股记录已追加: {len(stocks)}只 (source={source})")
    except Exception as e:
        log.warning(f"追加历史选股记录失败: {e}")


_load_auction_cache()


@auction_bp.route("/auction_pick")
def auction_pick():
    return render_template("auction_pick.html")


@auction_bp.route("/api/auction_pick")
def api_auction_pick():
    now = datetime.now()

    # 优先使用 web_app 调度器的结果（更完整）
    try:
        from web_app import AUCTION_PICK_DATA as WEB_DATA
        if WEB_DATA and isinstance(WEB_DATA, list) and len(WEB_DATA) > 0:
            with AUCTION_PICK_LOCK:
                AUCTION_PICK_DATA['stocks'] = WEB_DATA
                AUCTION_PICK_DATA['pick_time'] = now.strftime('%H:%M:%S')
                AUCTION_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
                _save_auction_cache()
            data = dict(AUCTION_PICK_DATA)
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
    except Exception:
        pass

    # 备选：从缓存文件加载
    if not AUCTION_PICK_DATA.get('stocks'):
        try:
            if os.path.exists(AUCTION_PICK_FILE):
                with open(AUCTION_PICK_FILE, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                if isinstance(cached, dict) and cached.get('stocks'):
                    AUCTION_PICK_DATA.update(cached)
        except Exception:
            pass

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
        log.info(f"执行竞价确认(V2)... {now.strftime('%H:%M:%S')}")

        # V2: 增强版大盘状态
        market_info = _get_enhanced_market_status()

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

        # V2: 批量补充 K线摘要 + 板块加分
        _enrich_stocks_with_v2_data(candidates)

        # V2: 负面过滤 + 评分 + 买入建议
        confirmed_stocks = _filter_and_score_v2(candidates, market_info, top_n=5)

        with AUCTION_PICK_LOCK:
            AUCTION_PICK_DATA['stocks'] = confirmed_stocks
            AUCTION_PICK_DATA['pick_time'] = now.strftime('%H:%M:%S')
            AUCTION_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
            AUCTION_PICK_DATA['confirm_time'] = now.strftime('%Y-%m-%d %H:%M:%S')
            AUCTION_PICK_DATA['market_info'] = market_info
            if not AUCTION_PICK_DATA.get('candidate_pool'):
                AUCTION_PICK_DATA['candidate_pool'] = candidates
            _save_auction_cache()

        log.info(f"竞价确认(V2): {len(confirmed_stocks)}只股票")
        _append_auction_history(confirmed_stocks, market_info, now.strftime('%H:%M:%S'), source="live")

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
        current_hour = now.hour
        current_minute = now.minute

        # 只允许 15:00~21:00 执行预选
        in_window = (current_hour == 15) or (16 <= current_hour <= 20) or (current_hour == 21 and current_minute == 0)
        if not in_window:
            return jsonify({
                "success": False,
                "error": "预选仅允许在 15:00~21:00 执行",
                "candidates": [],
            })

        # 今天已预选过则拒绝
        with AUCTION_PICK_LOCK:
            existing_time = AUCTION_PICK_DATA.get('preselect_time')
        if existing_time:
            try:
                existing_dt = datetime.strptime(existing_time, '%Y-%m-%d %H:%M:%S')
                if existing_dt.date() == now.date():
                    return jsonify({
                        "success": False,
                        "error": "今日已预选，每日仅限一次",
                        "candidates": [],
                    })
            except Exception:
                pass

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
            "candidates": candidates,
            "preselect_time": now.strftime('%Y-%m-%d %H:%M:%S'),
        })
    except Exception as e:
        log.error(f"竞价预选失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e), "candidates": []})


@auction_bp.route("/api/auction_status")
def api_auction_status():
    now = datetime.now()
    current_hour = now.hour
    current_minute = now.minute

    # 预选窗口: 15:00~21:00
    in_preselect_window = (current_hour == 15) or (16 <= current_hour <= 20) or (current_hour == 21 and current_minute == 0)

    # 判断今日是否已预选
    preselect_done_today = False
    with AUCTION_PICK_LOCK:
        data = dict(AUCTION_PICK_DATA) if AUCTION_PICK_DATA else {}
    existing_time = data.get('preselect_time')
    if existing_time:
        try:
            existing_dt = datetime.strptime(existing_time, '%Y-%m-%d %H:%M:%S')
            preselect_done_today = existing_dt.date() == now.date()
        except Exception:
            pass

    # 按钮可用 = 在窗口内 且 今日未预选
    can_preselect = in_preselect_window and not preselect_done_today

    return jsonify({
        "success": True,
        "can_preselect": can_preselect,
        "in_preselect_window": in_preselect_window,
        "preselect_done_today": preselect_done_today,
        "candidate_count": len(data.get('candidate_pool', [])),
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


@auction_bp.route("/api/auction_monitor")
def api_auction_monitor():
    """V5 实盘表现监测接口

    返回实盘选出股票的累计表现、预警信息、优化建议
    """
    try:
        from scripts.auction_monitor import generate_report
        report = generate_report()
        return jsonify({"success": True, "report": report})
    except Exception as e:
        log.error(f"生成监测报告失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


def run_auction_monitor_job():
    """V5 实盘监测调度任务（每日收盘后运行）

    由调度器调用，自动评估实盘表现并保存报告
    """
    try:
        log.info("执行 V5 实盘监测任务...")
        from scripts.auction_monitor import generate_report, print_report
        report = generate_report()
        print_report(report)
        # 打印预警摘要
        alerts = report.get("alerts", [])
        if alerts:
            for a in alerts:
                if a["level"] == "danger":
                    log.warning(f"V5预警[{a['type']}]: {a['message']}")
                else:
                    log.info(f"V5预警[{a['type']}]: {a['message']}")
    except Exception as e:
        log.error(f"V5 实盘监测任务失败: {e}", exc_info=True)


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


# ===========================================================================
# V2 优化模块（2026-06）：评分公式重平衡 + 板块联动 + 负面过滤 + 买入建议
# ===========================================================================

# 涨停板判定阈值（A股主板10%，创业板/科创板20%，ST 5%）。这里取主板标准，
# 对创业板稍微宽松（允许 19% 视为涨停），避免误杀。
_LIMIT_UP_THRESHOLD_MAIN = 0.095   # 主板：≥9.5% 视为涨停
_LIMIT_UP_THRESHOLD_GEM = 0.195    # 创业板/科创板：≥19.5% 视为涨停


# === 优化方向1：竞价数据深度利用 ===
def _fetch_auction_snapshot(code: str) -> dict:
    """从东财获取竞价快照数据（委买委卖比、大单占比、封单等）

    Returns:
        {
            "bid_ask_ratio": float,       # 委买委卖比（>1 买盘强）
            "big_order_ratio": float,     # 大单占比（>0.3 主力参与）
            "auction_amount_rank": float, # 竞价金额在全市场分位（0-1）
            "seal_strength": float,       # 封单强度（仅涨停股有意义）
            "available": bool,
        }
    """
    try:
        from modules.http_client import session
        # 东财盘口数据 secid: 0.深 1.沪
        prefix = "1." if code.startswith("6") else "0."
        secid = prefix + code
        url = "http://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": secid,
            "fields": "f43,f44,f45,f46,f47,f48,f50,f55,f57,f58,f60,f62,f70,f84,"
                      "f85,f86,f87,f88,f89,f90,f92,f93,f94,f95,f96,f100,f104,"
                      "f105,f107,f108,f111,f112,f113,f114,f115,f116,f117,"
                      "f168,f169,f170,f171,f172,f173,f174,f175,f176,f177,"
                      "f180,f181,f182,f183,f184,f185,f186,f187,f188,f189,"
                      "f190,f191,f192,f193,f194,f195,f196,f197,f198,f199",
        }
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
        resp = session.get(url, params=params, headers=headers, timeout=5)
        d = resp.json().get("data", {})
        if not d:
            return {"available": False}

        # 委买委卖比 = (买1-5量之和 - 卖1-5量之和) / (买1-5量之和 + 卖1-5量之和)
        # 东财字段 f93-f97 = 买1-5量, f98-f102 = 卖1-5量 (单位:手)
        # 但字段会变化，用更稳定的 f84(委差) / f85(委比)
        commit_diff = d.get("f84", 0) or 0  # 委差（买-卖）
        commit_ratio = d.get("f85", 0) or 0  # 委比（委差/总委买卖*100）
        # 委买委卖比 = (1 + 委比/100) / (1 - 委比/100) 当委比不为100/-100时
        if abs(commit_ratio) < 100:
            bid_ask_ratio = (100 + commit_ratio) / (100 - commit_ratio)
        elif commit_ratio >= 100:
            bid_ask_ratio = 99.0  # 全是买盘
        else:
            bid_ask_ratio = 0.01  # 全是卖盘

        # 大单占比 = 大单成交额 / 总成交额
        # f164=超大单额 f168=大单额 f172=中单额 f176=小单额 (单位:元)
        big_amount = (d.get("f164", 0) or 0) + (d.get("f168", 0) or 0)
        total_amount = (d.get("f164", 0) or 0) + (d.get("f168", 0) or 0) + \
                       (d.get("f172", 0) or 0) + (d.get("f176", 0) or 0)
        big_order_ratio = big_amount / total_amount if total_amount > 0 else 0

        return {
            "bid_ask_ratio": round(bid_ask_ratio, 2),
            "big_order_ratio": round(big_order_ratio, 3),
            "commit_diff": commit_diff,
            "commit_ratio": round(commit_ratio, 2),
            "available": True,
        }
    except Exception as e:
        return {"available": False, "error": str(e)[:50]}


def _batch_fetch_auction_snapshots(codes: list[str]) -> dict[str, dict]:
    """批量获取竞价快照

    Returns:
        {code: snapshot_dict}
    """
    result = {}
    if not codes:
        return result

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def fetch_one(code: str):
        return code, _fetch_auction_snapshot(code)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_one, c) for c in codes]
        for f in as_completed(futures, timeout=30):
            try:
                code, snap = f.result()
                result[code] = snap
            except Exception:
                continue
    return result


def _calculate_auction_bonus(snapshot: dict) -> tuple[float, list[str]]:
    """根据竞价快照计算加分和原因

    Returns:
        (bonus_points 0-10, reasons_list)
    """
    if not snapshot.get("available"):
        return 0.0, []

    bonus = 0.0
    reasons = []

    bid_ask = snapshot.get("bid_ask_ratio", 1.0)
    big_order = snapshot.get("big_order_ratio", 0)
    commit_ratio = snapshot.get("commit_ratio", 0)

    # 委买委卖比：买盘越强越好（但过高可能是一字板买不到）
    if bid_ask >= 3.0:
        bonus += 3.0
        reasons.append(f"委买强({bid_ask:.1f})")
    elif bid_ask >= 2.0:
        bonus += 2.0
        reasons.append(f"委买偏强({bid_ask:.1f})")
    elif bid_ask >= 1.3:
        bonus += 1.0
    elif bid_ask < 0.7:
        bonus -= 2.0
        reasons.append(f"委卖压({bid_ask:.1f})")

    # 大单占比：主力参与度
    if big_order >= 0.5:
        bonus += 4.0
        reasons.append(f"大单占比高({big_order*100:.0f}%)")
    elif big_order >= 0.3:
        bonus += 2.0
        reasons.append(f"大单参与({big_order*100:.0f}%)")
    elif big_order < 0.1:
        bonus -= 1.0
        reasons.append("大单缺席")

    return max(-5.0, min(10.0, bonus)), reasons


def _calc_rsi(klines: list[dict], period: int = 14) -> float:
    """计算 RSI 指标

    Args:
        klines: K线列表（按时间正序）
        period: RSI 周期，默认14
    """
    if len(klines) < period + 1:
        return 50.0
    closes = [float(k.get("close", 0)) for k in klines]
    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    # 取最近 period 根
    gains = gains[-period:]
    losses = losses[-period:]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - 100 / (1 + rs)
    return rsi


def _calc_kdj(klines: list[dict], n: int = 9) -> tuple[float, float, float]:
    """计算 KDJ 指标（简化版）

    Returns:
        (K, D, J) 值
    """
    if len(klines) < n:
        return 50.0, 50.0, 50.0
    highs = [float(k.get("high", 0)) for k in klines]
    lows = [float(k.get("low", 0)) for k in klines]
    closes = [float(k.get("close", 0)) for k in klines]

    k_values = []
    d_values = []
    prev_k = 50.0
    prev_d = 50.0
    for i in range(n - 1, len(klines)):
        period_high = max(highs[i - n + 1: i + 1])
        period_low = min(lows[i - n + 1: i + 1])
        if period_high == period_low:
            rsv = 50.0
        else:
            rsv = (closes[i] - period_low) / (period_high - period_low) * 100
        k = 2 / 3 * prev_k + 1 / 3 * rsv
        d = 2 / 3 * prev_d + 1 / 3 * k
        j = 3 * k - 2 * d
        k_values.append(k)
        d_values.append(d)
        prev_k = k
        prev_d = d
    if not k_values:
        return 50.0, 50.0, 50.0
    return k_values[-1], d_values[-1], 3 * k_values[-1] - 2 * d_values[-1]


def _get_recent_kline_summary(code: str) -> dict:
    """获取最近6日K线摘要，用于负面过滤和位置判断

    返回:
        {
            "yesterday_change_pct": float,   # 前一日涨跌幅（百分比，如 5.12 表示 5.12%）
            "was_limit_up_yesterday": bool,  # 前一日是否涨停
            "cumulative_5d_pct": float,      # 最近5日累计涨跌幅（百分比）
            "recent_high": float,            # 最近6日最高价
            "recent_low": float,             # 最近6日最低价
            "current_vs_5d_high": float,     # 当前价相对5日高点的回撤（百分比，正数=离高点的距离）
            "available": bool,               # K线数据是否可用
        }
    """
    empty = {
        "yesterday_change_pct": 0.0,
        "was_limit_up_yesterday": False,
        "cumulative_5d_pct": 0.0,
        "recent_high": 0.0,
        "recent_low": 0.0,
        "current_vs_5d_high": 0.0,
        "available": False,
    }
    try:
        from modules.kline_fetcher import kline_fetcher
        klines = kline_fetcher.get_kline(code, count=30)
        if not klines or len(klines) < 6:
            return empty

        # 按日期正序，取最近6根
        recent = klines[-6:]
        yesterday = recent[-2]
        today_ref = recent[-1]

        # 前一日涨跌幅
        prev_close = float(recent[-3]["close"]) if len(recent) >= 3 else float(yesterday["open"])
        yesterday_close = float(yesterday["close"])
        yesterday_change_pct = (yesterday_close / prev_close - 1) * 100 if prev_close > 0 else 0.0

        # 昨日是否涨停
        threshold = _LIMIT_UP_THRESHOLD_GEM if code.startswith("30") or code.startswith("68") else _LIMIT_UP_THRESHOLD_MAIN
        was_limit_up_yesterday = yesterday_change_pct >= threshold * 100 - 0.5

        # 5日累计涨跌幅：用最近5根的 close[0] → close[-1]
        recent5 = recent[-5:] if len(recent) >= 5 else recent
        start_close = float(recent5[0]["close"])
        end_close = float(recent5[-1]["close"])
        cumulative_5d_pct = (end_close / start_close - 1) * 100 if start_close > 0 else 0.0

        # 最近6日高低点
        recent_high = max(float(k["high"]) for k in recent)
        recent_low = min(float(k["low"]) for k in recent)

        # 当前价相对5日高点的回撤
        current = float(today_ref["close"])
        current_vs_5d_high = (1 - current / recent_high) * 100 if recent_high > 0 else 0.0

        # 优化方向4：RSI(14) 计算（用最近15根K线）
        rsi = _calc_rsi(klines[-15:]) if len(klines) >= 15 else 50.0

        # 优化方向4：KDJ(9,3,3) 简化计算
        kdj_k, kdj_d, kdj_j = _calc_kdj(klines[-12:]) if len(klines) >= 12 else (50.0, 50.0, 50.0)

        return {
            "yesterday_change_pct": round(yesterday_change_pct, 2),
            "was_limit_up_yesterday": was_limit_up_yesterday,
            "cumulative_5d_pct": round(cumulative_5d_pct, 2),
            "recent_high": recent_high,
            "recent_low": recent_low,
            "current_vs_5d_high": round(current_vs_5d_high, 2),
            "rsi": round(rsi, 1),
            "kdj_k": round(kdj_k, 1),
            "kdj_d": round(kdj_d, 1),
            "kdj_j": round(kdj_j, 1),
            "available": True,
        }
    except Exception as e:
        log.debug(f"K线摘要获取失败: {code}, {e}")
        return empty


def _batch_get_kline_summaries(codes: list[str], max_workers: int = 8) -> dict[str, dict]:
    """批量并发获取多只股票的K线摘要

    Returns:
        {code: kline_summary_dict}
    """
    if not codes:
        return {}
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_get_recent_kline_summary, c): c for c in codes}
        for future in futures:
            code = futures[future]
            try:
                results[code] = future.result()
            except Exception as e:
                log.debug(f"批量K线摘要异常: {code}, {e}")
                results[code] = {
                    "yesterday_change_pct": 0.0,
                    "was_limit_up_yesterday": False,
                    "cumulative_5d_pct": 0.0,
                    "recent_high": 0.0,
                    "recent_low": 0.0,
                    "current_vs_5d_high": 0.0,
                    "available": False,
                }
    return results


def _should_exclude_v2(stock: dict, kline_summary: dict) -> tuple[bool, str]:
    """V2 负面过滤：返回 (是否排除, 原因)

    排除规则：
    1. 昨日涨停 → 排除（次日高开多为出货套路）
    2. 5日累计涨幅 > 15% → 排除（已透支，追高风险大）
    3. 5日累计涨幅 < -15% → 排除（处于下跌趋势，反弹不确定性高）
    4. ST/退市 → 已在候选池过滤，这里再兜底
    """
    name = stock.get("name", "")
    if "ST" in name or "退" in name or "*" in name:
        return True, "ST/退市股"

    if not kline_summary.get("available"):
        # K线数据不可用时不强制排除，但降分（在评分函数中处理）
        return False, ""

    # 昨日涨停
    if kline_summary.get("was_limit_up_yesterday"):
        return True, "昨日涨停（次日高开多为出货）"

    # 5日累计涨跌幅过大
    cum_5d = kline_summary.get("cumulative_5d_pct", 0.0)
    if cum_5d > 15.0:
        return True, f"5日累计涨幅 {cum_5d:.1f}% 过大"
    if cum_5d < -15.0:
        return True, f"5日累计跌幅 {cum_5d:.1f}% 过大"

    # 优化方向4：RSI 超买过滤（RSI>80 严重超买，追高风险大）
    rsi = kline_summary.get("rsi", 0)
    if rsi > 82:
        return True, f"RSI={rsi:.0f} 严重超买"

    # 优化方向4：KDJ J值超买（J>100 极度超买）
    kdj_j = kline_summary.get("kdj_j", 50)
    if kdj_j > 110:
        return True, f"KDJ-J={kdj_j:.0f} 极度超买"

    return False, ""


def _calculate_score_v2(
    stock: dict,
    idx_gap: float,
    kline_summary: dict,
    sector_bonus: float,
    sector_reasons: list,
) -> tuple[float, dict]:
    """V2 评分公式：四维平衡 + 板块加成 + 负面惩罚

    评分构成（满分100）：
      - gap_score      (0-25) 倒U型：温和高开(2-5%)最佳
      - volume_score   (0-20) 量比1.5-3最佳
      - amount_score   (0-20) 竞价金额绝对值门槛
      - sector_score   (0-15) 来自 sector_rotation 模块
      - market_score   (0-10) 强于大盘加分
      - position_score (0-10) K线位置（远离5日高点加分）
      - penalty        (负分)  昨日大涨/累计涨幅过大的惩罚

    Returns:
        (total_score, breakdown_dict)
    """
    gap_pct = stock.get("gap_pct", 0)  # 小数形式，如 0.025 表示 2.5%
    volume_ratio = stock.get("volume_ratio", 0)
    auction_amount_yuan = stock.get("amount", 0) * 1e8  # amount 字段单位是亿元

    # --- gap_score：倒U型曲线 ---
    # 2%-5%: 满分25
    # 1%-2%: 15分
    # 5%-7%: 15分（开始追高）
    # 0%-1%: 8分
    # >7%: 5分（严重追高）
    # <0%: 3分（高开失败）
    gap_pct_pct = gap_pct * 100  # 转为百分比
    if 2 <= gap_pct_pct <= 5:
        gap_score = 25.0
    elif 1 <= gap_pct_pct < 2:
        gap_score = 18.0
    elif 5 < gap_pct_pct <= 7:
        gap_score = 15.0
    elif 0 < gap_pct_pct < 1:
        gap_score = 10.0
    elif gap_pct_pct > 7:
        gap_score = 5.0
    else:
        gap_score = 3.0

    # --- volume_score：量比1.5-3最佳 ---
    if 1.5 <= volume_ratio <= 3:
        volume_score = 20.0
    elif 3 < volume_ratio <= 5:
        volume_score = 15.0
    elif 1 <= volume_ratio < 1.5:
        volume_score = 12.0
    elif 5 < volume_ratio <= 8:
        volume_score = 8.0  # 异常放量警惕
    elif 0.5 <= volume_ratio < 1:
        volume_score = 5.0
    else:
        volume_score = 3.0

    # --- amount_score：竞价金额绝对值门槛 ---
    # ≥5000万: 20分
    # ≥2000万: 16分
    # ≥1000万: 12分
    # ≥500万:  8分
    # <500万:  3分
    if auction_amount_yuan >= 50000000:
        amount_score = 20.0
    elif auction_amount_yuan >= 20000000:
        amount_score = 16.0
    elif auction_amount_yuan >= 10000000:
        amount_score = 12.0
    elif auction_amount_yuan >= 5000000:
        amount_score = 8.0
    else:
        amount_score = 3.0

    # --- sector_score：来自 sector_rotation ---
    # calculate_sector_bonus 返回 0-15，直接用
    sector_score = max(0.0, min(15.0, float(sector_bonus)))

    # --- market_score：强于大盘加分 ---
    # gap_pct > idx_gap 越多越好，但上限 10 分
    outperform = (gap_pct - idx_gap) * 100  # 百分点
    if outperform > 0:
        market_score = min(10.0, outperform * 1.5)
    else:
        market_score = 0.0

    # --- position_score：K线位置 ---
    # 离5日高点回撤 3%-10% 最佳（有上升空间且未破位）
    # 距高点 < 3%（已接近高点）：5分
    # 距高点 3%-10%：10分
    # 距高点 10%-20%：7分
    # 距高点 > 20%：3分（处于下跌趋势）
    position_score = 5.0
    if kline_summary.get("available"):
        drawdown = kline_summary.get("current_vs_5d_high", 0.0)
        if 3 <= drawdown <= 10:
            position_score = 10.0
        elif 10 < drawdown <= 20:
            position_score = 7.0
        elif drawdown > 20:
            position_score = 3.0
        elif drawdown < 3:
            position_score = 5.0

    # 优化方向4：RSI 技术指标微调（不直接排除，但调整评分）
    # RSI 40-60（中性区）+2分，RSI 60-70（健康上升）+3分
    # RSI 70-80（偏高）-2分，RSI<30（超卖反弹可能）+1分
    rsi = kline_summary.get("rsi", 50)
    rsi_adjust = 0.0
    if 60 <= rsi <= 70:
        rsi_adjust = 3.0
    elif 40 <= rsi < 60:
        rsi_adjust = 2.0
    elif 70 < rsi <= 80:
        rsi_adjust = -2.0
    elif rsi < 30:
        rsi_adjust = 1.0

    # --- penalty：负面惩罚 ---
    penalty = 0.0
    if kline_summary.get("available"):
        # 昨日涨幅 > 7%（但未涨停）也降分
        yest = kline_summary.get("yesterday_change_pct", 0.0)
        if 7 <= yest < 9.5:
            penalty -= 5.0
        # 5日累计涨幅 10%-15%（接近上限）：温和降分
        cum_5d = kline_summary.get("cumulative_5d_pct", 0.0)
        if 10 <= cum_5d <= 15:
            penalty -= 3.0

    # 优化方向1：竞价快照加分（来自股票对象的 auction_bonus）
    auction_bonus = float(stock.get("auction_bonus", 0.0))
    auction_reasons = stock.get("auction_reasons", [])

    total = (
        gap_score
        + volume_score
        + amount_score
        + sector_score
        + market_score
        + position_score
        + auction_bonus   # 优化方向1新增
        + rsi_adjust      # 优化方向4新增
        + penalty
    )
    total = max(0.0, min(100.0, total))

    breakdown = {
        "gap_score": round(gap_score, 1),
        "volume_score": round(volume_score, 1),
        "amount_score": round(amount_score, 1),
        "sector_score": round(sector_score, 1),
        "sector_reasons": sector_reasons,
        "market_score": round(market_score, 1),
        "position_score": round(position_score, 1),
        "auction_bonus": round(auction_bonus, 1),
        "auction_reasons": auction_reasons,
        "rsi_adjust": round(rsi_adjust, 1),
        "penalty": round(penalty, 1),
        "total": round(total, 1),
    }
    return total, breakdown


def _build_buy_advice(stock: dict, score: float) -> dict:
    """生成买入建议：建议价区间、止损位、风险等级、持有期

    V4 优化：支持高开延续 / 低开反转双策略分支

    Returns:
        {
            "buy_price_low": float,    # 建议买入价下限
            "buy_price_high": float,   # 建议买入价上限
            "stop_loss": float,        # 止损价
            "risk_level": str,         # low / medium / high
            "hold_days": int,          # 建议持有期（V4新增）
            "strategy_branch": str,    # 策略分支：high_open / low_open_reversal（V4新增）
            "advice_text": str,        # 文字建议
        }
    """
    open_price = stock.get("open", 0) or stock.get("price", 0)
    gap_pct = stock.get("gap_pct", 0)
    gap_pct_pct = gap_pct * 100
    strategy_branch = stock.get("strategy_branch", "high_open")

    if open_price <= 0:
        return {
            "buy_price_low": 0,
            "buy_price_high": 0,
            "stop_loss": 0,
            "risk_level": "unknown",
            "hold_days": 3,
            "strategy_branch": strategy_branch,
            "advice_text": "价格数据异常",
        }

    # V4：根据策略分支生成不同的买入建议
    if strategy_branch == "low_open_reversal":
        # 低开反转策略：低开是买入机会，建议开盘附近介入
        if gap_pct_pct < -2.5:
            risk_level = "medium"  # 低开幅度大，反转不确定性高
            buy_low = open_price * 0.995
            buy_high = open_price * 1.005
            advice = "低开幅度较大，等待开盘后5分钟确认资金承接再介入"
        elif gap_pct_pct < -1.5:
            risk_level = "low"
            buy_low = open_price * 0.998
            buy_high = open_price * 1.01
            advice = "温和低开+趋势向上，可在开盘价至上浮1%区间介入"
        else:
            risk_level = "low"
            buy_low = open_price * 0.999
            buy_high = open_price * 1.015
            advice = "小幅低开+放量，可在开盘价附近介入，期待反转"
        advice += "（低开反转策略）"
    else:
        # 高开延续策略（原逻辑）
        if gap_pct_pct > 7:
            risk_level = "high"
            buy_low = open_price * 0.985
            buy_high = open_price * 0.995
            advice = "高开过多，开盘追高风险大，建议等回落至开盘价下方再介入"
        elif gap_pct_pct > 5:
            risk_level = "medium"
            buy_low = open_price * 0.995
            buy_high = open_price * 1.005
            advice = "高开幅度偏大，可小仓位在开盘价附近介入，不宜重仓追高"
        elif gap_pct_pct >= 2:
            risk_level = "low"
            buy_low = open_price * 0.998
            buy_high = open_price * 1.015
            advice = "温和高开，可在开盘价至上浮1.5%区间内介入"
        else:
            risk_level = "low"
            buy_low = open_price * 0.995
            buy_high = open_price * 1.01
            advice = "低开或微高开，观察开盘后5分钟走势再决定介入"
        advice += "（高开延续策略）"

    # 止损位：开盘价 -3%（高风险可放宽至 -4%）
    # V4：低开反转策略统一用 -3% 止损（回测验证有效）
    stop_pct = 0.04 if risk_level == "high" else 0.03
    stop_loss = open_price * (1 - stop_pct)

    # V4：持有期建议（回测验证3日最优）
    hold_days = 3

    # 评分过低时附加提醒
    if score < 50:
        advice += "；评分偏低，建议观望"
    elif score >= 80:
        advice += "；评分优秀，可重点关注"
    advice += f"；建议持有{hold_days}日"

    # V5：返回移动止盈规则
    return {
        "buy_price_low": round(buy_low, 2),
        "buy_price_high": round(buy_high, 2),
        "stop_loss": round(stop_loss, 2),
        "risk_level": risk_level,
        "hold_days": hold_days,
        "strategy_branch": strategy_branch,
        "trailing_stop": {
            "activate_pct": 8,      # 累计涨幅≥8%激活
            "drawdown_pct": 3,      # 回撤3%止盈
            "rule_text": "累计涨幅≥8%后回撤3%即止盈",
        },
        "advice_text": advice,
    }


def _get_enhanced_market_status() -> dict:
    """增强版大盘状态：原 _get_market_status() + 前5日趋势 + 北向资金

    Returns:
        {
            # 原有字段
            "idx300_close": float,
            "idx_open": float,
            "idx_gap": float,
            "market_ok": bool,
            # V2 新增
            "idx_5d_change_pct": float,   # CSI300 近5日累计涨跌幅（百分比）
            "northbound_net": float,      # 北向资金净流入（亿元，正=净流入）
            "sentiment": str,             # 市场情绪：bullish / neutral / bearish
            "sentiment_score": float,     # 情绪评分 0-100
        }
    """
    base = _get_market_status()

    enhanced = {
        "idx300_close": base.get("idx300_close", 0),
        "idx_open": base.get("idx_open", 0),
        "idx_gap": base.get("idx_gap", 0),
        "market_ok": base.get("market_ok", True),
        "idx_5d_change_pct": 0.0,
        "northbound_net": 0.0,
        "sentiment": "neutral",
        "sentiment_score": 50.0,
    }

    # 1. 获取 CSI300 前5日累计涨跌幅
    try:
        from modules.kline_fetcher import kline_fetcher
        klines = kline_fetcher.get_kline("000300", count=10)
        if klines and len(klines) >= 6:
            recent5 = klines[-6:-1]  # 不含今日
            start_close = float(recent5[0]["close"])
            end_close = float(recent5[-1]["close"])
            enhanced["idx_5d_change_pct"] = round((end_close / start_close - 1) * 100, 2)
    except Exception as e:
        log.debug(f"CSI300 5日趋势获取失败: {e}")

    # 2. 北向资金净流入（尝试东方财富API）
    try:
        from modules.http_client import session
        url = "https://push2.eastmoney.com/api/qt/kamtbs.wpt?fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56"
        resp = session.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and data.get("data"):
                # 取最新一条
                rows = data["data"].get("s2n", [])
                if rows:
                    latest = rows[-1].split(",")
                    # f52=沪股通净流入, f53=深股通净流入，单位万元
                    hgt = float(latest[1]) if latest[1] else 0
                    sgt = float(latest[2]) if latest[2] else 0
                    enhanced["northbound_net"] = round((hgt + sgt) / 10000, 2)  # 万元 → 亿元
    except Exception as e:
        log.debug(f"北向资金获取失败: {e}")

    # 3. 综合情绪评分（0-100）
    score = 50.0
    # 大盘当日 gap
    idx_gap_pct = enhanced["idx_gap"] * 100
    if idx_gap_pct > 1:
        score += 15
    elif idx_gap_pct > 0.3:
        score += 8
    elif idx_gap_pct < -1:
        score -= 15
    elif idx_gap_pct < -0.3:
        score -= 8

    # 5日趋势
    if enhanced["idx_5d_change_pct"] > 3:
        score += 15
    elif enhanced["idx_5d_change_pct"] > 0:
        score += 5
    elif enhanced["idx_5d_change_pct"] < -3:
        score -= 15
    elif enhanced["idx_5d_change_pct"] < 0:
        score -= 5

    # 北向资金
    if enhanced["northbound_net"] > 50:
        score += 15
    elif enhanced["northbound_net"] > 0:
        score += 5
    elif enhanced["northbound_net"] < -50:
        score -= 15
    elif enhanced["northbound_net"] < 0:
        score -= 5

    score = max(0.0, min(100.0, score))
    enhanced["sentiment_score"] = round(score, 1)

    if score >= 65:
        enhanced["sentiment"] = "bullish"
    elif score <= 35:
        enhanced["sentiment"] = "bearish"
    else:
        enhanced["sentiment"] = "neutral"

    # 优化方向2：市场环境 regime 判断（趋势/震荡/下跌）
    # 用于后续不同市场环境用不同选股参数
    idx_5d = enhanced["idx_5d_change_pct"]
    idx_gap_pct = enhanced["idx_gap"] * 100
    if idx_5d > 3 and idx_gap_pct > 0:
        regime = "trend_up"        # 上升趋势
    elif idx_5d < -3 or (idx_5d < -1 and idx_gap_pct < -0.5):
        regime = "trend_down"      # 下降趋势
    elif -1 <= idx_5d <= 3:
        regime = "range"           # 震荡市
    else:
        regime = "volatile"        # 大幅波动
    enhanced["regime"] = regime

    # 强化 market_ok：情绪极度悲观时也拒绝开仓
    if score < 30:
        enhanced["market_ok"] = False

    # 优化方向2：加强大盘择时 — 5日趋势为负且当日不强 → 拒绝开仓
    # 实盘级放宽：原180天仅3-7只样本，过严；放宽阈值增加样本量
    idx_5d_pct = enhanced["idx_5d_change_pct"]
    idx_gap_pct_now = enhanced["idx_gap"] * 100
    if enhanced["idx_gap"] < -0.02:
        enhanced["market_ok"] = False
    if idx_5d_pct < -3 and idx_gap_pct_now < 0.3:  # 原-1放宽至-3
        enhanced["market_ok"] = False

    # 优化：大盘5日趋势≥5%严重过热时追高风险大 → 拒绝开仓（原4%放宽至5%）
    if idx_5d_pct >= 5 and idx_gap_pct_now < 0.3:
        enhanced["market_ok"] = False
    # 优化：大盘当日明显低开（gap<-1.0%原-0.3%）+ 5日趋势不强（<0原<1） → 拒绝开仓
    if idx_gap_pct_now < -1.0 and idx_5d_pct < 0:
        enhanced["market_ok"] = False

    return enhanced


# 优化方向2：不同市场环境下的选股参数
# 注：经180天实盘级回测验证（5000+股票，含交易成本0.302%+止损3%）
#   - 评分≥80段3日胜率83.3%，75-80段仅50% → min_score 提高到80
#   - 实盘验证：高开>4%的样本3日全亏 → max_gap 收紧至3.5%
#   - trend_up环境胜率仅40%（追高被套）→ 收紧高开上限
#   - 大盘5d≥4%严重过热时表现差 → 择时过滤
_REGIME_PARAMS = {
    "trend_up": {
        "min_gap": 0.015, "max_gap": 0.035,  # 实盘优化：收紧至3.5%（原0.04）
        "min_vr": 1.2, "max_vr": 6.0,
        "min_score": 80,
        "desc": "上升趋势：警惕追高，收紧高开上限+提高评分门槛",
    },
    "range": {
        "min_gap": 0.02, "max_gap": 0.035,   # 实盘优化：收紧至3.5%（原0.04）
        "min_vr": 1.5, "max_vr": 5.0,
        "min_score": 80,
        "desc": "震荡市：要求温和高开+量比适中，严控追高",
    },
    "trend_down": {
        "min_gap": 0.005, "max_gap": 0.03,   # 实盘优化：收紧至3%（原0.035）
        "min_vr": 1.5, "max_vr": 4.0,
        "min_score": 85,
        "desc": "下跌趋势：只选极强标的，评分门槛最高",
    },
    "volatile": {
        "min_gap": 0.02, "max_gap": 0.035,   # 实盘优化：收紧至3.5%（原0.04）
        "min_vr": 1.3, "max_vr": 6.0,
        "min_score": 80,
        "desc": "波动市场：中等标准，避免极端",
    },
}


def _enrich_stocks_with_v2_data(candidates: list[dict]) -> list[dict]:
    """对候选池批量补充 V2 所需数据：K线摘要 + 板块加分 + 竞价快照

    在 _get_auction_candidates() 后调用，原地修改每个 candidate 字典，
    添加字段：
      - kline_summary: dict
      - sector_bonus: float
      - sector_reasons: list[str]
      - auction_snapshot: dict           # 优化方向1新增
      - auction_bonus: float             # 竞价加分
      - auction_reasons: list[str]
    """
    if not candidates:
        return candidates

    codes = [c["code"] for c in candidates if c.get("code")]

    # 1. 批量获取 K线摘要
    kline_summaries = _batch_get_kline_summaries(codes, max_workers=8)
    for c in candidates:
        c["kline_summary"] = kline_summaries.get(c["code"], {})

    # 2. 批量获取板块加分
    try:
        from modules.sector_rotation import calculate_sector_bonus
        for c in candidates:
            try:
                bonus, reasons = calculate_sector_bonus(
                    c.get("code", ""),
                    c.get("name", ""),
                    c.get("industry", ""),
                )
                c["sector_bonus"] = bonus
                c["sector_reasons"] = reasons
            except Exception as e:
                log.debug(f"板块加分失败: {c.get('code')}, {e}")
                c["sector_bonus"] = 0.0
                c["sector_reasons"] = []
    except ImportError:
        log.warning("sector_rotation 模块不可用，跳过板块加分")
        for c in candidates:
            c["sector_bonus"] = 0.0
            c["sector_reasons"] = []

    # 3. 优化方向1：批量获取竞价快照（委买委卖比、大单占比）
    snapshots = _batch_fetch_auction_snapshots(codes)
    for c in candidates:
        snap = snapshots.get(c["code"], {})
        c["auction_snapshot"] = snap
        bonus, reasons = _calculate_auction_bonus(snap)
        c["auction_bonus"] = bonus
        c["auction_reasons"] = reasons

    return candidates


def _filter_and_score_v2(
    candidates: list[dict],
    market_info: dict,
    top_n: int = 5,
) -> list[dict]:
    """V2 主筛选+评分流程（含市场环境自适应）

    Steps:
      1. 根据 market_info 的 regime 选择对应的过滤参数
      2. 负面过滤（_should_exclude_v2）
      3. 基础门槛过滤（高开/量比/竞价额/强于大盘，参数随 regime 变化）
      4. V2 评分（_calculate_score_v2 + auction_bonus）
      5. 评分门槛过滤（regime 决定 min_score）
      6. 买入建议（_build_buy_advice）
      7. 排序取 top_n

    Returns:
        list of stock dicts with all V2 fields populated
    """
    idx_gap = market_info.get("idx_gap", 0)
    regime = market_info.get("regime", "range")
    params = _REGIME_PARAMS.get(regime, _REGIME_PARAMS["range"])

    log.info(f"V2筛选 regime={regime} 参数: {params['desc']}")

    confirmed_stocks = []
    excluded_count = 0
    pass_threshold_count = 0

    for stock in candidates:
        # 1. V2 负面过滤
        kline_summary = stock.get("kline_summary", {})
        excluded, exclude_reason = _should_exclude_v2(stock, kline_summary)
        if excluded:
            excluded_count += 1
            log.debug(f"V2过滤: {stock.get('code')} {stock.get('name')} - {exclude_reason}")
            continue

        # 优化方向3：板块轮动硬过滤 — 板块处于下跌阶段直接排除
        sector_bonus = stock.get("sector_bonus", 0.0)
        sector_reasons = stock.get("sector_reasons", [])
        if sector_bonus < -1.5:
            excluded_count += 1
            log.debug(f"V2板块过滤: {stock.get('code')} {stock.get('name')} - 板块弱势 bonus={sector_bonus}")
            continue

        # 2. 基础门槛过滤（优化方向2：参数随 regime 变化）
        gap_pct = stock.get("gap_pct", 0)
        gap_ok = params["min_gap"] <= gap_pct <= params["max_gap"]
        volume_ratio_ok = params["min_vr"] <= stock.get("volume_ratio", 0) <= params["max_vr"]
        auction_amount_ok = stock.get("auction_amount_pct", 0) >= 0.01
        stronger_than_market = gap_pct > idx_gap

        # 策略分支A：高开延续（原策略）
        is_high_open = gap_ok and volume_ratio_ok and auction_amount_ok and stronger_than_market
        # 策略分支B：低开反转（新增）— 低开-3%~-0.5% + 量比≥1.5 + 前5日趋势向上 + 前一日非大跌
        prev_5d_trend = kline_summary.get("cumulative_5d_pct", 0.0)
        prev_1d_gain = kline_summary.get("yesterday_change_pct", 0.0)
        is_low_open_reversal = (
            -0.03 <= gap_pct <= -0.005
            and stock.get("volume_ratio", 0) >= 1.5
            and prev_5d_trend >= 2.0
            and prev_1d_gain >= -3.0
            and auction_amount_ok
            and params["min_vr"] <= stock.get("volume_ratio", 0) <= params["max_vr"]
        )
        # V5优化：低开反转策略要求大盘5日趋势非负（弱势市场资金不承接低开反转）
        # V4样本验证：688432 2026-05-19 idx_5d=-1.92% 时低开反转亏损-3.17%
        idx_5d_pct = market_info.get("idx_5d_change_pct", 0)
        if is_low_open_reversal and idx_5d_pct < 0:
            log.info(
                f"V5过滤: {stock.get('code')} 低开反转但大盘5日趋势{idx_5d_pct:.2f}%为负，跳过"
            )
            is_low_open_reversal = False

        if not (is_high_open or is_low_open_reversal):
            continue

        # 标记策略分支
        stock["strategy_branch"] = "high_open" if is_high_open else "low_open_reversal"

        pass_threshold_count += 1

        # 3. V2 评分（含 auction_bonus）
        score, breakdown = _calculate_score_v2(
            stock, idx_gap, kline_summary, sector_bonus, sector_reasons
        )

        # 低开反转策略评分调整：V2评分针对高开设计，需通过反转加分补偿
        if is_low_open_reversal:
            reversal_bonus = 0.0
            vr = stock.get("volume_ratio", 0)
            if vr >= 2.0:
                reversal_bonus += 12.0
            elif vr >= 1.5:
                reversal_bonus += 8.0
            if prev_5d_trend >= 8:
                reversal_bonus += 10.0
            elif prev_5d_trend >= 5:
                reversal_bonus += 7.0
            elif prev_5d_trend >= 2:
                reversal_bonus += 4.0
            gp = gap_pct * 100
            if -1.5 <= gp <= -0.5:
                reversal_bonus += 8.0
            elif -2.5 <= gp < -1.5:
                reversal_bonus += 5.0
            else:
                reversal_bonus += 2.0
            drawdown = kline_summary.get("current_vs_5d_high", 0)
            if 3 <= drawdown <= 10:
                reversal_bonus += 5.0
            score += reversal_bonus
            breakdown["reversal_bonus"] = reversal_bonus
            breakdown["strategy_branch"] = "low_open_reversal"

        # 4. 优化方向2：评分门槛（下跌市更严）
        if score < params["min_score"]:
            log.debug(f"评分未达 {regime} 门槛: {stock.get('code')} score={score:.1f} < {params['min_score']}")
            continue

        # 5. 买入建议
        buy_advice = _build_buy_advice(stock, score)

        # 写回 stock 字典
        stock["score"] = score
        stock["score_breakdown"] = breakdown
        stock["buy_advice"] = buy_advice
        stock["gap_ok"] = is_high_open
        stock["volume_ratio_ok"] = volume_ratio_ok
        stock["auction_amount_ok"] = auction_amount_ok
        stock["stronger_than_market"] = stronger_than_market if is_high_open else True
        stock["excluded"] = False
        stock["regime"] = regime
        # 附加 K线摘要关键字段供前端展示
        stock["yesterday_change_pct"] = kline_summary.get("yesterday_change_pct", 0.0)
        stock["cumulative_5d_pct"] = kline_summary.get("cumulative_5d_pct", 0.0)

        confirmed_stocks.append(stock)

    # 6. 排序取 top_n
    confirmed_stocks.sort(key=lambda x: x.get("score", 0), reverse=True)

    # V5：同股冷却期过滤（14个交易日内不重复入选同一股票）
    COOLDOWN_DAYS = 14
    cooled_stocks = []
    recent_codes = _get_recent_pick_codes(COOLDOWN_DAYS)
    for s in confirmed_stocks:
        code = s.get("code", "")
        if code in recent_codes:
            log.info(f"V5冷却: {code} {s.get('name', '')} 14交易日内已入选，跳过")
            continue
        cooled_stocks.append(s)
    confirmed_stocks = cooled_stocks[:top_n]

    log.info(
        f"V2筛选: 候选{len(candidates)}只 → 排除{excluded_count}只 → "
        f"通过门槛{pass_threshold_count}只 → 评分≥{params['min_score']} → 冷却后取top{top_n}={len(confirmed_stocks)}只"
    )

    return confirmed_stocks


def _get_recent_pick_codes(cooldown_days: int) -> set:
    """V5：从历史记录读取最近 cooldown_days 个交易日内已入选的股票代码"""
    codes = set()
    try:
        if not os.path.exists(AUCTION_HISTORY_FILE):
            return codes
        # 读取最近 N 个交易日（按日期去重倒序）
        seen_dates = []
        with open(AUCTION_HISTORY_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        for line in reversed(lines):
            try:
                rec = json.loads(line.strip())
                d = rec.get("date", "")
                if d and d not in seen_dates:
                    seen_dates.append(d)
                if len(seen_dates) > cooldown_days:
                    break
                # 在冷却期内的所有股票都加入
                for s in rec.get("stocks", []):
                    codes.add(s.get("code", ""))
            except Exception:
                continue
    except Exception as e:
        log.warning(f"读取冷却期历史记录失败: {e}")
    return codes


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

                    change_pct = float(s.get("changepercent", 0))  # Keep as percentage (e.g., 8.69 for 8.69%)
                    if change_pct < -5 or change_pct > 9.5:
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

                                change_pct = float(s.get("changepercent", 0))  # Keep as percentage (e.g., 8.69 for 8.69%)
                                if change_pct < -8 or change_pct > 9.5:
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

        # 批量获取行业信息
        if candidates:
            from concurrent.futures import ThreadPoolExecutor
            from modules.data_fetcher import get_stock_industry

            def _fetch_industry(stock):
                try:
                    info = get_stock_industry(stock["code"])
                    stock["industry"] = info.get("industry", "未知")
                    stock["sector"] = info.get("sector_type", "default")
                except Exception:
                    stock["industry"] = "未知"
                    stock["sector"] = "default"

            with ThreadPoolExecutor(max_workers=10) as executor:
                list(executor.map(_fetch_industry, candidates))
            log.info(f"行业信息获取完成: {sum(1 for c in candidates if c.get('industry', '未知') != '未知')}/{len(candidates)}")

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

        log.info(f"执行竞价选股(V2)... {now.strftime('%H:%M:%S')}")

        # V2: 使用增强版大盘状态（含5日趋势+北向资金+情绪评分）
        market_info = _get_enhanced_market_status()
        log.info(
            f"大盘状态(V2): gap={market_info.get('idx_gap', 0)*100:.2f}%, "
            f"5d={market_info.get('idx_5d_change_pct', 0):.2f}%, "
            f"北向={market_info.get('northbound_net', 0):.1f}亿, "
            f"情绪={market_info.get('sentiment', '')}({market_info.get('sentiment_score', 0):.0f})"
        )

        if not market_info.get('market_ok', True):
            # 已有有效数据时不清空
            if AUCTION_PICK_DATA.get('stocks'):
                log.info("大盘环境不佳但已有持仓数据，保留")
                return
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
            # 只有当前有有效数据时才保留，否则清空
            has_valid = bool(AUCTION_PICK_DATA.get('stocks'))
            if has_valid:
                log.info("非竞价时段，保留已有选股结果")
                return
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

        # V2: 批量补充 K线摘要 + 板块加分
        _enrich_stocks_with_v2_data(candidates)

        # V2: 负面过滤 + 评分 + 买入建议
        confirmed_stocks = _filter_and_score_v2(candidates, market_info, top_n=5)

        with AUCTION_PICK_LOCK:
            AUCTION_PICK_DATA['stocks'] = confirmed_stocks
            AUCTION_PICK_DATA['pick_time'] = now.strftime('%H:%M:%S')
            AUCTION_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
            AUCTION_PICK_DATA['market_info'] = market_info
            AUCTION_PICK_DATA['candidate_pool'] = candidates
            _save_auction_cache()

        log.info(f"竞价确认(V4): {len(confirmed_stocks)}只股票")
        _append_auction_history(confirmed_stocks, market_info, now.strftime('%H:%M:%S'), source="live")
        for s in confirmed_stocks[:3]:
            log.info(
                f"  #{s.get('code')} {s.get('name')}: score={s.get('score', 0):.1f} "
                f"branch={s.get('strategy_branch', 'high_open')} "
                f"gap={s.get('gap_pct', 0)*100:.2f}% vr={s.get('volume_ratio', 0):.2f} "
                f"sector_bonus={s.get('sector_bonus', 0):.1f} "
                f"risk={s.get('buy_advice', {}).get('risk_level', '')} "
                f"hold={s.get('buy_advice', {}).get('hold_days', 3)}d"
            )

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