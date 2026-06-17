"""Flask Blueprint - 强势选股路由"""

from __future__ import annotations

from flask import Blueprint, render_template, jsonify

from modules.logger import log

strong_bp = Blueprint("strong", __name__)


@strong_bp.route("/strong_pick")
def strong_pick():
    return render_template("strong_pick.html")


@strong_bp.route("/api/strong_pick")
def api_strong_pick():
    try:
        from web_app import get_strong_data
        data = get_strong_data()
        stocks = _format_stocks(data)
        return jsonify({"success": True, "stocks": stocks})
    except Exception as e:
        log.error(f"获取强势选股数据失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"获取数据失败: {e}"})


@strong_bp.route("/api/strong_pick_execute")
def api_strong_pick_execute():
    try:
        from web_app import run_strong_picker
        result = run_strong_picker()
        stocks = _format_stocks(result)
        return jsonify({"success": True, "stocks": stocks})
    except Exception as e:
        log.error(f"强势选股执行失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"选股失败: {e}"})


@strong_bp.route("/api/strong_pick_clear")
def api_strong_pick_clear():
    try:
        from web_app import clear_strong_data
        clear_strong_data()
        return jsonify({"success": True, "message": "缓存已清除"})
    except Exception as e:
        log.error(f"清除强势选股缓存失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"清除失败: {e}"})


def _format_stocks(data) -> list[dict]:
    if not data:
        return []

    stocks = []
    for item in data:
        if not isinstance(item, dict):
            continue

        stock = {
            "code": item.get("code", ""),
            "name": item.get("name", ""),
            "price": item.get("price", 0),
            "change_pct": item.get("change_pct", 0),
            "pe": item.get("pe", 0),
            "pb": item.get("pb", 0),
            "roe": item.get("roe", 0),
            "market_cap": item.get("market_cap", 0),
            "score": item.get("score", item.get("total_score", 0)),
            "rank": item.get("rank", 0),
            "change_5d": item.get("change_5d", 0),
            "breakthrough_pct": item.get("breakthrough_pct", -1),
            "position_pct": item.get("position_pct", 0),
            "volume_ratio": item.get("volume_ratio", 1.0),
            "turnover_rate": item.get("turnover_rate", item.get("turnover", 0)),
            "rsi": item.get("rsi", None),
            "golden_cross": item.get("golden_cross", False),
            "pullback_stable": item.get("pullback_stable", False),
            "has_limit_up": item.get("has_limit_up", False),
            "gentle_volume": item.get("gentle_volume", False),
            "moderate_volume": item.get("moderate_volume", False),
            "extreme_volume": item.get("extreme_volume", False),
            "industry": item.get("industry", ""),
            "sector": item.get("sector", ""),
            "buy_sell": item.get("buy_sell", None),
            "v5_score": item.get("v5_score", None),
            "v5_factors": item.get("v5_factors", None),
            "v5_reasons": item.get("v5_reasons", None),
            "v5_recommendation": item.get("v5_recommendation", None),
            "reasons": item.get("reasons", None),
        }
        stocks.append(stock)
    return stocks

