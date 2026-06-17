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
DAILY_PICK_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'daily_pick_cache.json')


def _load_daily_cache():
    global DAILY_PICK_DATA
    try:
        if os.path.exists(DAILY_PICK_FILE):
            with open(DAILY_PICK_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 兼容两种缓存格式：
                # 1. 字典格式: {"date": ..., "morning": ..., "afternoon": ...} (daily.py保存)
                # 2. 列表格式: [{stock1}, {stock2}, ...] (web_app.py保存)
                if isinstance(data, list):
                    # 列表格式 -> 转换为 morning/afternoon 结构
                    today = datetime.now().strftime('%Y-%m-%d')
                    # V5.5: 过滤ROE<0的亏损股（旧缓存可能包含亏损股）
                    data = [r for r in data
                            if not (isinstance(r.get('roe'), (int, float)) and r.get('roe') < 0)]
                    filtered = [r for r in data
                                if not r.get('name', '').startswith('N')
                                and '退' not in r.get('name', '')
                                and r.get('change_pct', 0) <= 100]
                    # === V5.7: 早盘/午盘差异化评分 (从缓存加载时也需差异化) ===

                    # --- 早盘候选池 & 评分 (价值型) ---
                    morning_candidates = [r for r in filtered
                                          if r.get('change_pct', 0) <= 5
                                          and (r.get('pe', 0) <= 60 or r.get('pe', 0) <= 0)]
                    if len(morning_candidates) < 12:
                        morning_candidates = [r for r in filtered if r.get('change_pct', 0) <= 5]
                    if len(morning_candidates) < 12:
                        morning_candidates = filtered

                    def _cache_morning_score(r):
                        """缓存加载时早盘评分: 价值型"""
                        base = r.get('v5_score', r.get('score', 0))
                        pe = r.get('pe', 0); roe = r.get('roe', 0); pb = r.get('pb', 0)
                        debt = r.get('debt_ratio', 0); gm = r.get('gross_margin', 0)
                        chg = r.get('change_pct', 0); rg = r.get('rev_growth', 0)
                        pg = r.get('profit_growth', 0)
                        v5v = r.get('v5_factors', {}).get('value', 50)
                        v5q = r.get('v5_factors', {}).get('quality', 50)
                        # 估值 (最多30分)
                        vb = 0
                        if 0 < pe <= 8: vb = 20
                        elif 8 < pe <= 12: vb = 17
                        elif 12 < pe <= 18: vb = 13
                        elif 18 < pe <= 25: vb = 8
                        elif 25 < pe <= 35: vb = 4
                        elif 35 < pe <= 50: vb = 1
                        if 0 < pb <= 1.2: vb += 6
                        elif 1.2 < pb <= 2: vb += 4
                        elif 2 < pb <= 3: vb += 2
                        if v5v >= 85: vb += 4
                        elif v5v >= 70: vb += 2
                        # 质量 (最多25分)
                        qb = 0
                        if roe >= 25: qb = 15
                        elif roe >= 20: qb = 12
                        elif roe >= 15: qb = 9
                        elif roe >= 10: qb = 5
                        elif roe >= 5: qb = 2
                        if gm >= 60: qb += 5
                        elif gm >= 40: qb += 3
                        elif gm >= 25: qb += 1
                        if v5q >= 80: qb += 5
                        elif v5q >= 65: qb += 3
                        # 成长 (最多15分)
                        gb = 0
                        if 10 <= rg <= 30: gb = 8
                        elif 30 < rg <= 50: gb = 6
                        elif 5 <= rg < 10: gb = 5
                        elif rg > 50: gb = 4
                        if 10 <= pg <= 30: gb += 7
                        elif 30 < pg <= 50: gb += 5
                        elif 5 <= pg < 10: gb += 3
                        elif pg > 50: gb += 2
                        # 安全 (最多15分)
                        sb = 0
                        if 0 < debt <= 20: sb = 10
                        elif 20 < debt <= 35: sb = 7
                        elif 35 < debt <= 50: sb = 4
                        elif 50 < debt <= 65: sb = 1
                        elif debt > 70: sb = -3
                        if -3 <= chg <= 1: sb += 5
                        elif -5 <= chg < -3: sb += 2
                        elif 1 < chg <= 3: sb += 3
                        elif chg > 5: sb -= 3
                        # 行情 (最多5分)
                        mb = 0
                        tr = r.get('turnover_rate', 0)
                        if 1 <= tr <= 5: mb = 5
                        elif 0.5 <= tr < 1: mb = 3
                        elif 5 < tr <= 10: mb = 2
                        return base * 0.1 + vb + qb + gb + sb + mb

                    morning_top8 = sorted(morning_candidates, key=_cache_morning_score, reverse=True)[:8]

                    # --- 午盘候选池 & 评分 (动量型) ---
                    afternoon_candidates = [r for r in filtered if r.get('change_pct', 0) > -5]
                    if len(afternoon_candidates) < 12:
                        afternoon_candidates = filtered

                    def _cache_afternoon_score(r):
                        """缓存加载时午盘评分: 动量型"""
                        base = r.get('v5_score', r.get('score', 0))
                        chg = r.get('change_pct', 0); tr = r.get('turnover_rate', 0)
                        roe = r.get('roe', 0); rg = r.get('rev_growth', 0)
                        pg = r.get('profit_growth', 0); gm = r.get('gross_margin', 0)
                        pb = r.get('pb', 0); pe = r.get('pe', 0)
                        v5m = r.get('v5_factors', {}).get('momentum', 50)
                        v5s = r.get('v5_factors', {}).get('sentiment', 50)
                        v5g = r.get('v5_factors', {}).get('growth', 50)
                        # 动量 (最多25分)
                        mob = 0
                        if v5m >= 80: mob = 15
                        elif v5m >= 65: mob = 12
                        elif v5m >= 50: mob = 8
                        elif v5m >= 35: mob = 4
                        if 2 <= chg <= 5: mob += 10
                        elif 1 <= chg < 2: mob += 7
                        elif 5 < chg <= 7: mob += 5
                        elif 0 <= chg < 1: mob += 4
                        elif chg > 7: mob += 1
                        # 情绪 (最多20分)
                        seb = 0
                        if v5s >= 75: seb = 10
                        elif v5s >= 60: seb = 7
                        elif v5s >= 45: seb = 4
                        elif v5s >= 30: seb = 2
                        if 3 <= tr <= 8: seb += 10
                        elif 1.5 <= tr < 3: seb += 6
                        elif 8 < tr <= 15: seb += 5
                        elif 0.8 <= tr < 1.5: seb += 3
                        elif tr > 15: seb += 2
                        # 成长 (最多20分)
                        gb = 0
                        if v5g >= 75: gb = 10
                        elif v5g >= 60: gb = 7
                        elif v5g >= 45: gb = 4
                        elif v5g >= 30: gb = 2
                        if rg >= 30: gb += 5
                        elif rg >= 15: gb += 3
                        elif rg >= 5: gb += 1
                        if pg >= 30: gb += 5
                        elif pg >= 15: gb += 3
                        elif pg >= 5: gb += 1
                        # 行情 (最多15分)
                        mkb = 0
                        if 1 <= chg <= 4: mkb = 8
                        elif 4 < chg <= 7: mkb = 6
                        elif 0 < chg < 1: mkb = 4
                        elif chg > 7: mkb = 2
                        elif -1 <= chg <= 0: mkb = 2
                        if pb > 0 and pe > 0:
                            if pb <= 3 and pe <= 20: mkb += 7
                            elif pb <= 5 and pe <= 30: mkb += 5
                            elif pb <= 8 and pe <= 40: mkb += 3
                            else: mkb += 1
                        # 质量 (最多10分)
                        qb = 0
                        if roe >= 15: qb = 10
                        elif roe >= 10: qb = 7
                        elif roe >= 5: qb = 4
                        elif roe >= 0: qb = 1
                        if gm < 15: qb -= 2
                        return base * 0.1 + mob + seb + gb + mkb + qb

                    afternoon_top8 = sorted(afternoon_candidates, key=_cache_afternoon_score, reverse=True)[:8]

                    DAILY_PICK_DATA = {
                        "date": today,
                        "morning": {"results": morning_top8, "total_scanned": len(data), "session_type": "早盘选股", "strategy": "早盘策略(V5.7): 估值30+质量25+成长15+安全15, 低吸价值股"},
                        "afternoon": {"results": afternoon_top8, "total_scanned": len(data), "session_type": "午盘选股", "strategy": "午盘策略(V5.7): 动量25+情绪20+成长20+行情15+质量10, 资金确认跟进"},
                        "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    return
                elif isinstance(data, dict) and data.get('date') == datetime.now().strftime('%Y-%m-%d'):
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


# === 每日选股核心逻辑（V5.7: 早盘/午盘真正差异化）===

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
                        and r.get('change_pct', 0) <= 100
                        and not (isinstance(r.get('roe'), (int, float)) and r.get('roe') < 0)]

            # ============================================================
            # V5.7: 早盘/午盘真正差异化选股策略
            # 核心改进: base score仅占10%，差异化评分占90%
            # 早盘: 价值型 — 低PE+高ROE+稳健成长+低负债+高分红潜力
            # 午盘: 动量型 — 高动量+强情绪+高成长+资金关注+技术强势
            # ============================================================

            if session_type == 'morning':
                def morning_score(r):
                    """早盘评分: 价值型选股 — 安全边际第一
                    评分结构: base(10%) + 估值(30分) + 盈利质量(25分) + 成长(15分) + 安全(15分) + 行情(5分)
                    早盘核心: 开盘前/初行情数据不足，侧重基本面安全边际。
                    """
                    base = r.get('v5_score', r.get('score', 0))
                    pe = r.get('pe', 0)
                    roe = r.get('roe', 0)
                    pb = r.get('pb', 0)
                    debt = r.get('debt_ratio', 0)
                    gross_margin = r.get('gross_margin', 0)
                    change_pct = r.get('change_pct', 0)
                    rev_growth = r.get('rev_growth', 0)
                    profit_growth = r.get('profit_growth', 0)
                    v5_value = r.get('v5_factors', {}).get('value', 50)
                    v5_quality = r.get('v5_factors', {}).get('quality', 50)

                    # === 估值加分 (最多30分) — 早盘核心 ===
                    valuation_bonus = 0
                    # PE评分 (最多20分)
                    if 0 < pe <= 8:
                        valuation_bonus = 20
                    elif 8 < pe <= 12:
                        valuation_bonus = 17
                    elif 12 < pe <= 18:
                        valuation_bonus = 13
                    elif 18 < pe <= 25:
                        valuation_bonus = 8
                    elif 25 < pe <= 35:
                        valuation_bonus = 4
                    elif 35 < pe <= 50:
                        valuation_bonus = 1
                    # PB加分 (最多6分)
                    if 0 < pb <= 1.2:
                        valuation_bonus += 6
                    elif 1.2 < pb <= 2:
                        valuation_bonus += 4
                    elif 2 < pb <= 3:
                        valuation_bonus += 2
                    # V5价值因子加分 (最多4分)
                    if v5_value >= 85:
                        valuation_bonus += 4
                    elif v5_value >= 70:
                        valuation_bonus += 2

                    # === 盈利质量加分 (最多25分) — 高ROE+高毛利 ===
                    quality_bonus = 0
                    # ROE评分 (最多15分)
                    if roe >= 25:
                        quality_bonus = 15
                    elif roe >= 20:
                        quality_bonus = 12
                    elif roe >= 15:
                        quality_bonus = 9
                    elif roe >= 10:
                        quality_bonus = 5
                    elif roe >= 5:
                        quality_bonus = 2
                    # 毛利率加分 (最多5分)
                    if gross_margin >= 60:
                        quality_bonus += 5
                    elif gross_margin >= 40:
                        quality_bonus += 3
                    elif gross_margin >= 25:
                        quality_bonus += 1
                    # V5质量因子加分 (最多5分)
                    if v5_quality >= 80:
                        quality_bonus += 5
                    elif v5_quality >= 65:
                        quality_bonus += 3

                    # === 成长性加分 (最多15分) — 稳健成长 ===
                    growth_bonus = 0
                    # 营收增长 (最多8分) — 早盘偏好10-30%稳健增长
                    if 10 <= rev_growth <= 30:
                        growth_bonus = 8
                    elif 30 < rev_growth <= 50:
                        growth_bonus = 6
                    elif 5 <= rev_growth < 10:
                        growth_bonus = 5
                    elif rev_growth > 50:
                        growth_bonus = 4  # 过高可能不可持续
                    # 利润增长 (最多7分)
                    if 10 <= profit_growth <= 30:
                        growth_bonus += 7
                    elif 30 < profit_growth <= 50:
                        growth_bonus += 5
                    elif 5 <= profit_growth < 10:
                        growth_bonus += 3
                    elif profit_growth > 50:
                        growth_bonus += 2

                    # === 安全加分 (最多15分) — 低负债+小波动 ===
                    safety_bonus = 0
                    # 负债率 (最多10分) — 早盘偏好低负债
                    if 0 < debt <= 20:
                        safety_bonus = 10
                    elif 20 < debt <= 35:
                        safety_bonus = 7
                    elif 35 < debt <= 50:
                        safety_bonus = 4
                    elif 50 < debt <= 65:
                        safety_bonus = 1
                    elif debt > 70:
                        safety_bonus = -3
                    # 涨跌幅安全 (最多5分) — 早盘偏好低吸区间
                    if -3 <= change_pct <= 1:
                        safety_bonus += 5
                    elif -5 <= change_pct < -3:
                        safety_bonus += 2
                    elif 1 < change_pct <= 3:
                        safety_bonus += 3
                    elif change_pct > 5:
                        safety_bonus -= 3  # 追高风险

                    # === 行情辅助 (最多5分) ===
                    market_bonus = 0
                    turnover = r.get('turnover_rate', 0)
                    if 1 <= turnover <= 5:
                        market_bonus = 5
                    elif 0.5 <= turnover < 1:
                        market_bonus = 3
                    elif 5 < turnover <= 10:
                        market_bonus = 2

                    return base * 0.1 + valuation_bonus + quality_bonus + growth_bonus + safety_bonus + market_bonus

                # 早盘候选池: 排除涨幅>5%的追高风险 + 排除PE>60的高估值
                morning_candidates = [r for r in filtered
                                      if r.get('change_pct', 0) <= 5
                                      and (r.get('pe', 0) <= 60 or r.get('pe', 0) <= 0)]
                if len(morning_candidates) < 12:
                    morning_candidates = [r for r in filtered if r.get('change_pct', 0) <= 5]
                if len(morning_candidates) < 12:
                    morning_candidates = filtered

                top10 = sorted(morning_candidates, key=morning_score, reverse=True)[:8]
                pick_strategy = "早盘策略(V5.7): 估值30+质量25+成长15+安全15, 低吸价值股"

            else:
                def afternoon_score(r):
                    """午盘评分: 动量型选股 — 资金确认第一
                    评分结构: base(10%) + 动量(25分) + 情绪(20分) + 成长(20分) + 行情(15分) + 质量(10分)
                    午盘核心: 半天交易后，侧重资金动向+技术强势+成长动力。
                    """
                    base = r.get('v5_score', r.get('score', 0))
                    change_pct = r.get('change_pct', 0)
                    turnover = r.get('turnover_rate', 0)
                    roe = r.get('roe', 0)
                    rev_growth = r.get('rev_growth', 0)
                    profit_growth = r.get('profit_growth', 0)
                    v5_momentum = r.get('v5_factors', {}).get('momentum', 50)
                    v5_sentiment = r.get('v5_factors', {}).get('sentiment', 50)
                    v5_growth = r.get('v5_factors', {}).get('growth', 50)
                    gross_margin = r.get('gross_margin', 0)
                    pb = r.get('pb', 0)
                    pe = r.get('pe', 0)

                    # === 动量加分 (最多25分) — 午盘核心: 技术强势 ===
                    momentum_bonus = 0
                    # V5动量因子 (最多15分)
                    if v5_momentum >= 80:
                        momentum_bonus = 15
                    elif v5_momentum >= 65:
                        momentum_bonus = 12
                    elif v5_momentum >= 50:
                        momentum_bonus = 8
                    elif v5_momentum >= 35:
                        momentum_bonus = 4
                    # 当日涨幅动量 (最多10分) — 午盘偏好1-5%确认上涨
                    if 2 <= change_pct <= 5:
                        momentum_bonus += 10  # 最佳: 温和放量上涨
                    elif 1 <= change_pct < 2:
                        momentum_bonus += 7
                    elif 5 < change_pct <= 7:
                        momentum_bonus += 5  # 偏强但有追高风险
                    elif 0 <= change_pct < 1:
                        momentum_bonus += 4
                    elif change_pct > 7:
                        momentum_bonus += 1  # 追高危险

                    # === 情绪加分 (最多20分) — 资金关注度 ===
                    sentiment_bonus = 0
                    # V5情绪因子 (最多10分)
                    if v5_sentiment >= 75:
                        sentiment_bonus = 10
                    elif v5_sentiment >= 60:
                        sentiment_bonus = 7
                    elif v5_sentiment >= 45:
                        sentiment_bonus = 4
                    elif v5_sentiment >= 30:
                        sentiment_bonus = 2
                    # 换手率 (最多10分) — 午盘偏好活跃交易
                    if 3 <= turnover <= 8:
                        sentiment_bonus += 10  # 适度活跃
                    elif 1.5 <= turnover < 3:
                        sentiment_bonus += 6
                    elif 8 < turnover <= 15:
                        sentiment_bonus += 5  # 偏活跃
                    elif 0.8 <= turnover < 1.5:
                        sentiment_bonus += 3
                    elif turnover > 15:
                        sentiment_bonus += 2  # 过度活跃

                    # === 成长加分 (最多20分) — 午盘偏好高成长 ===
                    growth_bonus = 0
                    # V5成长因子 (最多10分)
                    if v5_growth >= 75:
                        growth_bonus = 10
                    elif v5_growth >= 60:
                        growth_bonus = 7
                    elif v5_growth >= 45:
                        growth_bonus = 4
                    elif v5_growth >= 30:
                        growth_bonus = 2
                    # 营收+利润增长 (最多10分) — 午盘偏好30%+高增长
                    if rev_growth >= 30:
                        growth_bonus += 5
                    elif rev_growth >= 15:
                        growth_bonus += 3
                    elif rev_growth >= 5:
                        growth_bonus += 1
                    if profit_growth >= 30:
                        growth_bonus += 5
                    elif profit_growth >= 15:
                        growth_bonus += 3
                    elif profit_growth >= 5:
                        growth_bonus += 1

                    # === 行情加分 (最多15分) — 当日表现 ===
                    market_bonus = 0
                    # 当日涨幅 (最多8分)
                    if 1 <= change_pct <= 4:
                        market_bonus = 8
                    elif 4 < change_pct <= 7:
                        market_bonus = 6
                    elif 0 < change_pct < 1:
                        market_bonus = 4
                    elif change_pct > 7:
                        market_bonus = 2
                    elif -1 <= change_pct <= 0:
                        market_bonus = 2
                    # 涨幅 vs PB (成长股允许高PB) (最多7分)
                    if pb > 0 and pe > 0:
                        # 高PB但低PE = 价值成长股
                        if pb <= 3 and pe <= 20:
                            market_bonus += 7  # 价值+成长
                        elif pb <= 5 and pe <= 30:
                            market_bonus += 5  # 合理成长
                        elif pb <= 8 and pe <= 40:
                            market_bonus += 3
                        else:
                            market_bonus += 1  # 贵但有动量

                    # === 质量辅助 (最多10分) — 午盘质量门槛低 ===
                    quality_bonus = 0
                    if roe >= 15:
                        quality_bonus = 10
                    elif roe >= 10:
                        quality_bonus = 7
                    elif roe >= 5:
                        quality_bonus = 4
                    elif roe >= 0:
                        quality_bonus = 1
                    # 毛利率辅助
                    if gross_margin >= 40:
                        quality_bonus += 0  # 午盘不额外加
                    elif gross_margin < 15:
                        quality_bonus -= 2  # 太低扣分

                    return base * 0.1 + momentum_bonus + sentiment_bonus + growth_bonus + market_bonus + quality_bonus

                # 午盘候选池: 允许涨幅较大的动量股，排除跌幅>5%的弱势股
                afternoon_candidates = [r for r in filtered if r.get('change_pct', 0) > -5]
                if len(afternoon_candidates) < 12:
                    afternoon_candidates = filtered

                scored_candidates = [(r, afternoon_score(r)) for r in afternoon_candidates]
                scored_candidates.sort(key=lambda x: x[1], reverse=True)
                top10 = [r for r, s in scored_candidates[:8]]
                pick_strategy = "午盘策略(V5.7): 动量25+情绪20+成长20+行情15+质量10, 资金确认跟进"

            with DAILY_PICK_LOCK:
                DAILY_PICK_DATA[session_type] = {
                    "results": top10,
                    "total_scanned": total,
                    "pick_time": datetime.now().strftime("%H:%M:%S"),
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