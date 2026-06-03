"""Flask Blueprint - 每日选股路由

恢复旧版早盘/午盘双时段逻辑:
- 早盘: 侧重基本面和估值，排除涨幅>5%，选Top 5
- 午盘: 侧重当日行情表现，排除跌幅>-5%，选Top 5
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Optional

from flask import Blueprint, render_template, request, jsonify

from modules.logger import log

daily_bp = Blueprint("daily", __name__)

DAILY_PICK_LOCK = threading.Lock()
DAILY_PICK_DATA: dict = {}
DAILY_PICK_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'daily_pick_cache.json')


def _load_daily_cache():
    global DAILY_PICK_DATA
    try:
        if os.path.exists(DAILY_PICK_FILE):
            with open(DAILY_PICK_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('date') == datetime.now().strftime('%Y-%m-%d'):
                    DAILY_PICK_DATA = data
                    return
    except Exception as e:
        log.warning(f"加载每日选股缓存失败: {e}")
    DAILY_PICK_DATA = {
        "date": datetime.now().strftime('%Y-%m-%d'),
        "morning": None,
        "afternoon": None,
        "last_update": None,
    }


def _save_daily_cache():
    try:
        with open(DAILY_PICK_FILE, 'w', encoding='utf-8') as f:
            json.dump(DAILY_PICK_DATA, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"保存每日选股缓存失败: {e}")


_load_daily_cache()


@daily_bp.route("/")
def index():
    return render_template("index.html")


@daily_bp.route("/daily")
@daily_bp.route("/daily_pick")
def daily_pick():
    return render_template("daily_pick.html")


@daily_bp.route("/api/daily_pick")
def api_daily_pick():
    global DAILY_PICK_DATA

    with DAILY_PICK_LOCK:
        data = dict(DAILY_PICK_DATA) if DAILY_PICK_DATA else {}

    now = datetime.now()
    current_hour = now.hour

    need_morning = not data.get('morning') or not data.get('morning', {}).get('results')
    need_afternoon = not data.get('afternoon') or not data.get('afternoon', {}).get('results')

    if need_afternoon and current_hour >= 14:
        _execute_daily_pick('afternoon')
    if need_morning and current_hour >= 9:
        _execute_daily_pick('morning')

    with DAILY_PICK_LOCK:
        data = dict(DAILY_PICK_DATA) if DAILY_PICK_DATA else {}

    for sess in ['morning', 'afternoon']:
        if data.get(sess) and data[sess].get('results'):
            for stock in data[sess]['results']:
                if 'debt_ratio' not in stock:
                    stock['debt_ratio'] = 0

    return jsonify({
        "success": True,
        "date": data.get('date', datetime.now().strftime('%Y-%m-%d')),
        "morning": data.get('morning'),
        "afternoon": data.get('afternoon'),
        "last_update": data.get('last_update'),
    })


@daily_bp.route("/api/daily_pick_run", methods=["POST"])
def api_daily_pick_run():
    req_data = request.get_json(silent=True) or {}
    session_type = req_data.get('session_type', 'morning')
    if session_type not in ['morning', 'afternoon']:
        return jsonify({"success": False, "error": "无效的选股时段"})

    def run_async():
        _execute_daily_pick(session_type)

    t = threading.Thread(target=run_async, daemon=True)
    t.start()

    return jsonify({
        "success": True,
        "message": f"{'早盘' if session_type == 'morning' else '午盘'}选股已启动",
    })


@daily_bp.route("/api/pick")
def api_pick():
    try:
        from web_app import get_picker_data
        results = get_picker_data()
        if not results:
            from modules.stock_picker import run_picker
            results = run_picker()
        from modules.data_fetcher import get_preset_financials

        total = results[0].get('_total_scanned', len(get_preset_financials())) if results else len(get_preset_financials())
        for r in results:
            r.pop('_total_scanned', None)
            if 'debt_ratio' not in r:
                r['debt_ratio'] = 0
            if 'net_margin' not in r:
                r['net_margin'] = 0
        return jsonify({
            "success": True,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_scanned": total,
            "results": results,
        })
    except Exception as e:
        log.error(f"选股错误: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@daily_bp.route("/api/daily/data")
def api_daily_data():
    try:
        from web_app import get_picker_data
        data = get_picker_data()
        if not data:
            return jsonify({"success": False, "error": "暂无每日选股数据"}), 404
        return jsonify({"success": True, "data": data})
    except Exception as e:
        log.error(f"获取每日选股数据失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"获取数据失败: {e}"})


# === 每日选股核心逻辑（恢复旧版早盘/午盘双时段）===

def _execute_daily_pick(session_type):
    global DAILY_PICK_DATA

    log.info(f"开始执行{'早盘' if session_type == 'morning' else '午盘'}选股...")

    try:
        from modules.stock_picker import run_picker
        results = run_picker()

        if results:
            total = results[0].get('_total_scanned', 0) if results else 0
            for r in results:
                r.pop('_total_scanned', None)

            filtered = [r for r in results
                        if not r.get('name', '').startswith('N')
                        and '退' not in r.get('name', '')
                        and r.get('change_pct', 0) <= 100]

            if session_type == 'morning':
                def morning_score(r):
                    base = r.get('score', 0)
                    pe = r.get('pe', 0)
                    roe = r.get('roe', 0)

                    valuation_bonus = 0
                    if 0 < pe <= 15:
                        valuation_bonus = 5
                    elif 15 < pe <= 25:
                        valuation_bonus = 3
                    elif 25 < pe <= 35:
                        valuation_bonus = 1

                    roe_bonus = 0
                    if roe >= 25:
                        roe_bonus = 3
                    elif roe >= 20:
                        roe_bonus = 2

                    return base + valuation_bonus + roe_bonus

                morning_candidates = [r for r in filtered if r.get('change_pct', 0) <= 5]
                if len(morning_candidates) < 10:
                    morning_candidates = filtered

                top10 = sorted(morning_candidates, key=morning_score, reverse=True)[:5]
                pick_strategy = "早盘策略（Round 4）：选Top 5集中持仓，收益率+26.09%"
            else:
                def afternoon_score(r):
                    base_score = r.get('score', 0)
                    change_pct = r.get('change_pct', 0)
                    turnover = r.get('turnover_rate', 0)

                    bonus = 0

                    if 1 <= change_pct <= 4:
                        bonus += 5
                    elif 0 <= change_pct < 1:
                        bonus += 3
                    elif -2 <= change_pct < 0:
                        bonus += 4
                    elif 4 < change_pct <= 6:
                        bonus += 2
                    elif change_pct > 6:
                        bonus += 0

                    if 3 <= turnover <= 10:
                        bonus += 4
                    elif 1.5 <= turnover < 3:
                        bonus += 3
                    elif 10 < turnover <= 15:
                        bonus += 2
                    elif turnover > 15:
                        bonus += 1

                    return base_score + bonus

                afternoon_candidates = [r for r in filtered if r.get('change_pct', 0) > -5]
                if len(afternoon_candidates) < 10:
                    afternoon_candidates = filtered

                scored_candidates = [(r, afternoon_score(r)) for r in afternoon_candidates]
                scored_candidates.sort(key=lambda x: x[1], reverse=True)
                top10 = [r for r, s in scored_candidates[:5]]
                pick_strategy = "午盘策略（Round 4）：选Top 5集中持仓，收益率+26.09%"

            with DAILY_PICK_LOCK:
                DAILY_PICK_DATA[session_type] = {
                    "results": top10,
                    "total_scanned": total,
                    "pick_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "session_type": "早盘选股" if session_type == 'morning' else "午盘选股",
                    "strategy": pick_strategy,
                }
                DAILY_PICK_DATA['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                DAILY_PICK_DATA['date'] = datetime.now().strftime('%Y-%m-%d')
                _save_daily_cache()

            log.info(f"{'早盘' if session_type == 'morning' else '午盘'}选股完成: {len(top10)} 只股票")
        else:
            log.warning("选股失败，无结果")
    except Exception as e:
        log.error(f"选股执行失败: {e}", exc_info=True)


def _schedule_daily_pick():
    last_executed = {"morning": None, "afternoon": None}

    while True:
        now = datetime.now()
        today = now.strftime('%Y-%m-%d')
        current_time = now.strftime("%H:%M")

        with DAILY_PICK_LOCK:
            if DAILY_PICK_DATA.get('date') != today:
                DAILY_PICK_DATA.update({
                    "date": today,
                    "morning": None,
                    "afternoon": None,
                    "last_update": None,
                })
                last_executed = {"morning": None, "afternoon": None}

        if current_time == "09:27" and last_executed["morning"] != today:
            last_executed["morning"] = today
            threading.Thread(target=_execute_daily_pick, args=('morning',), daemon=True).start()
            log.info("早盘选股任务已触发 (9:27)")

        elif current_time == "14:30" and last_executed["afternoon"] != today:
            last_executed["afternoon"] = today
            threading.Thread(target=_execute_daily_pick, args=('afternoon',), daemon=True).start()
            log.info("午盘选股任务已触发 (14:30)")

        time.sleep(60)
