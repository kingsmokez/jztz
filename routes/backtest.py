"""HTTP routes for the backtest engine.

Endpoints
---------
* ``POST /api/backtest/run``            — execute a backtest
* ``GET  /api/backtest/results``        — list saved results
* ``GET  /api/backtest/result/<id>``    — fetch one result
* ``DELETE /api/backtest/result/<id>``  — remove one
"""
from __future__ import annotations

import os
import re
import threading
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request

from modules.backtest import (
    BacktestConfig,
    BacktestError,
    BacktestInput,
    ConfigError,
    DataError,
    load_result,
    run,
    save_result,
)
from modules.http_client import get_session as _get_http_session
from modules.logger import log
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import time


backtest_bp = Blueprint("backtest", __name__, url_prefix="/api/backtest")

# Results live in <project>/backtest_results/ — gitignored.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(_HERE, "backtest_results")
os.makedirs(RESULTS_DIR, exist_ok=True)

_RESULT_ID_RE = re.compile(r"^bt_\d+_[A-Za-z0-9]{1,16}$")
_FILE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------
def _parse_config(d: Dict[str, Any]) -> BacktestConfig:
    """Build a BacktestConfig from a JSON dict, ignoring unknown keys."""
    known = {f for f in BacktestConfig.__dataclass_fields__}
    return BacktestConfig(
        **{k: v for k, v in d.items() if k in known}
    )


def _parse_input(data: Dict[str, Any]) -> BacktestInput:
    if "price_history" not in data or not isinstance(
        data["price_history"], dict
    ):
        raise ConfigError("`price_history` must be an object {date: {code: price}}")
    if "picks_by_date" not in data or not isinstance(
        data["picks_by_date"], dict
    ):
        raise ConfigError("`picks_by_date` must be an object {date: [code, ...]}")
    cfg = _parse_config(data.get("config", {}))
    return BacktestInput(
        price_history=data["price_history"],
        picks_by_date=data["picks_by_date"],
        config=cfg,
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
@backtest_bp.post("/run")
def run_backtest():
    """Execute a backtest. Body::

        {
          "price_history": {
            "2026-01-02": {"000001": 10.0, "600519": 1500.0},
            ...
          },
          "picks_by_date": {
            "2026-01-02": ["000001", "600519", "300750"]
          },
          "config": {
            "initial_capital": 100000,
            "top_n": 3,
            "rebalance_every": 5,
            "name": "demo"
          }
        }
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({
            "ok": False,
            "error": "request body must be a JSON object",
        }), 400

    try:
        inp = _parse_input(payload)
        result = run(inp)
    except (ConfigError, DataError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except BacktestError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        log.error(f"backtest run failed: {e}", exc_info=True)
        return jsonify({"ok": False, "error": f"internal: {e}"}), 500

    # Persist
    try:
        with _FILE_LOCK:
            path = save_result(result, RESULTS_DIR)
    except Exception as e:
        log.error(f"failed to save backtest result: {e}", exc_info=True)
        # Still return the result even if save failed.
        return jsonify({
            "ok": True,
            "result": result.to_dict(),
            "persisted": False,
            "save_error": str(e),
        })

    return jsonify({
        "ok": True,
        "result": result.to_dict(),
        "persisted": True,
        "path": path,
    })


# ---------------------------------------------------------------------------
# List / get / delete
# ---------------------------------------------------------------------------
@backtest_bp.get("/results")
def list_results():
    """List saved backtest results, newest first."""
    files: List[Dict[str, Any]] = []
    try:
        names = sorted(os.listdir(RESULTS_DIR), reverse=True)
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(RESULTS_DIR, name)
        try:
            data = load_result(path)
        except Exception as e:
            log.warning(f"skipping unreadable result {name}: {e}")
            continue
        # Return a small summary, not the full equity curve
        files.append({
            "id": data.get("id", name[:-5]),
            "name": data.get("name", ""),
            "started_at": data.get("started_at", ""),
            "ended_at": data.get("ended_at", ""),
            "metrics": data.get("metrics", {}),
            "size_bytes": os.path.getsize(path),
        })
    return jsonify({"ok": True, "count": len(files), "results": files})


@backtest_bp.get("/result/<result_id>")
def get_result(result_id: str):
    if not _RESULT_ID_RE.match(result_id):
        return jsonify({"ok": False, "error": "invalid id"}), 400
    path = os.path.join(RESULTS_DIR, f"{result_id}.json")
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "not found"}), 404
    try:
        return jsonify({"ok": True, "result": load_result(path)})
    except Exception as e:
        log.error(f"failed to load {path}: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@backtest_bp.delete("/result/<result_id>")
def delete_result(result_id: str):
    if not _RESULT_ID_RE.match(result_id):
        return jsonify({"ok": False, "error": "invalid id"}), 400
    path = os.path.join(RESULTS_DIR, f"{result_id}.json")
    if not os.path.exists(path):
        return jsonify({"ok": False, "error": "not found"}), 404
    try:
        with _FILE_LOCK:
            os.remove(path)
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "deleted": result_id})


# ---------------------------------------------------------------------------
# Real data backtest — fetches K-line from Tencent API
# ---------------------------------------------------------------------------

KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

# Representative A-share pool (100 stocks across major sectors)
REPRESENTATIVE_STOCKS = [
    # 金融
    "000001", "002142", "600000", "600016", "600036", "601318", "601166",
    "601328", "601398", "601939", "601288", "600030", "000776",
    # 白酒/食品
    "600519", "000858", "002304", "000568", "600887", "603288", "600809",
    # 医药
    "600276", "000538", "300015", "300122", "300347", "002007", "600196",
    "300760", "000661",
    # 新能源/电池
    "300750", "002594", "601012", "002460", "002466", "300014", "600438",
    "300274", "688981",
    # 科技/TMT
    "002415", "002230", "300059", "000725", "002475", "300124", "601138",
    "603501", "002049", "300782",
    # 家电
    "000651", "002032", "000333", "600690",
    # 汽车
    "600104", "000625", "002625", "601633",
    # 地产/基建
    "000002", "600048", "001979", "600585", "601668", "601800",
    # 有色/化工
    "600111", "000792", "603799", "002709", "000426",
    # 煤炭/能源
    "601088", "600028", "601857", "600188",
    # 电子/半导体
    "002371", "603986", "300408", "300433",
    # 交通运输
    "601111", "600029", "600009", "601006",
    # 军工
    "600760", "000768", "600893", "002013",
    # 其他大市值
    "601899", "600900", "601628", "600050", "002120", "300498",
    "000063", "002352", "601888", "600570", "600309",
]


def _code_to_param(code: str) -> str:
    """Convert stock code to Tencent API param prefix."""
    if code.startswith(("6", "9")):
        return f"sh{code}"
    else:
        return f"sz{code}"


def _fetch_klines_concurrent(
    codes: list[str], lookback: int = 60
) -> dict[str, dict[str, float]]:
    """Concurrently fetch daily K-line close prices, multi-source fallback.

    Returns: {date_str: {code: close_price}}
    """
    from modules.kline_fetcher import kline_fetcher

    log.info(f"真实回测: 获取K线数据 ({len(codes)} 只, {lookback} 日) ...")
    price_history: dict[str, dict[str, float]] = defaultdict(dict)
    success_count = 0

    def fetch_one(code: str) -> tuple[int, dict[str, float]]:
        local_prices: dict[str, float] = {}
        ok = 0
        try:
            raw = kline_fetcher.get_kline(code, lookback + 10)
            if raw:
                for d in raw:
                    date_str = d.get("date", "")
                    close = d.get("close", 0)
                    if date_str and close > 0:
                        local_prices[date_str] = close
                ok = 1
        except Exception:
            pass
        return (ok, local_prices)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_one, c): c for c in codes}
        for future in as_completed(futures):
            try:
                ok, local_prices = future.result(timeout=30)
                if ok and local_prices:
                    success_count += ok
                    for date_str, close in local_prices.items():
                        price_history[date_str][futures[future]] = close
            except Exception:
                pass

    # Trim to last lookback dates
    sorted_dates = sorted(price_history.keys())[-lookback:]
    result = {d: dict(price_history[d]) for d in sorted_dates}

    log.info(f"真实回测: K线获取完成, {len(result)} 个交易日, {success_count}/{len(codes)} 只成功, 源={kline_fetcher.health_status()}")
    return result


def _fetch_benchmark_kline(lookback: int = 60) -> dict[str, dict[str, float]]:
    """Fetch CSI300 index K-line for benchmark."""
    from modules.kline_fetcher import kline_fetcher

    price_history: dict[str, dict[str, float]] = defaultdict(dict)
    try:
        # 399300 是沪深300指数
        raw = kline_fetcher.get_kline("399300", lookback + 10)
        if raw:
            for d in raw:
                date_str = d.get("date", "")
                close = d.get("close", 0)
                if date_str and close > 0:
                    price_history[date_str]["CSI300"] = close
    except Exception as e:
        log.warning(f"真实回测: 获取沪深300基准失败: {e}")

    sorted_dates = sorted(price_history.keys())[-lookback:]
    return {d: dict(price_history[d]) for d in sorted_dates}


# --- Strategy proxy pickers for real-data backtest ---

def _rank_by_momentum(ph: dict, dates: list, top_n: int) -> list[str]:
    """Momentum ranking — picks stocks with highest 5-day gain (≈ strong stock picker)."""
    if len(dates) < 5:
        return []
    today = dates[-1]
    start = dates[-5]
    codes_set = set()
    for d in dates[-5:]:
        codes_set.update(ph.get(d, {}).keys())
    scores = []
    for code in codes_set:
        p_t = ph.get(today, {}).get(code, 0)
        p_s = ph.get(start, {}).get(code, 0)
        if p_t > 0 and p_s > 0:
            scores.append((code, (p_t - p_s) / p_s))
    scores.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scores[:top_n]]


def _rank_by_breakout(ph: dict, dates: list, top_n: int) -> list[str]:
    """Breakout ranking — picks stocks near 20-day highs (≈ WP2 picker)."""
    if len(dates) < 20:
        return _rank_by_momentum(ph, dates, top_n)
    today = dates[-1]
    codes_set = set()
    for d in dates[-20:]:
        codes_set.update(ph.get(d, {}).keys())
    scores = []
    for code in codes_set:
        p_t = ph.get(today, {}).get(code, 0)
        if p_t <= 0:
            continue
        max_past = max(
            (ph.get(d, {}).get(code, 0) for d in dates[-20:-1]),
            default=0,
        )
        if max_past > 0:
            scores.append((code, p_t / max_past))
    scores.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scores[:top_n]]


def _rank_by_multi_factor(ph: dict, dates: list, top_n: int) -> list[str]:
    """Multi-factor ranking — momentum + breakout (≈ daily picker)."""
    mom = _rank_by_momentum(ph, dates, top_n * 2)
    brk = _rank_by_breakout(ph, dates, top_n * 2)
    mom_set = {c: i for i, c in enumerate(mom)}
    brk_set = {c: i for i, c in enumerate(brk)}
    all_codes = set(mom_set) | set(brk_set)
    combined = []
    for code in all_codes:
        m_score = (top_n * 2 - mom_set.get(code, top_n * 2)) / (top_n * 2)
        b_score = (top_n * 2 - brk_set.get(code, top_n * 2)) / (top_n * 2)
        combined.append((code, m_score * 0.6 + b_score * 0.4))
    combined.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in combined[:top_n]]


def _rank_by_trend(ph: dict, dates: list, top_n: int) -> list[str]:
    """Trend ranking — sustained uptrend + acceleration (≈ auction picker)."""
    if len(dates) < 10:
        return _rank_by_momentum(ph, dates, top_n)
    today = dates[-1]
    codes_set = set()
    for d in dates[-10:]:
        codes_set.update(ph.get(d, {}).keys())
    scores = []
    for code in codes_set:
        p_t = ph.get(today, {}).get(code, 0)
        if p_t <= 0:
            continue
        p_3 = ph.get(dates[-3], {}).get(code, 0)
        p_10 = ph.get(dates[-10], {}).get(code, 0)
        if p_3 > 0 and p_10 > 0:
            mom3 = (p_t - p_3) / p_3
            mom10 = (p_t - p_10) / p_10
            accel = mom3 - mom10
            score = mom3 * 0.5 + mom10 * 0.3 + max(0, accel) * 0.2
            scores.append((code, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scores[:top_n]]


STRATEGY_RANKERS = {
    "daily": _rank_by_multi_factor,
    "strong": _rank_by_momentum,
    "auction": _rank_by_trend,
    "wp2": _rank_by_breakout,
}


@backtest_bp.post("/run_real")
def run_real_backtest():
    """Run backtest with real market data from Tencent API.

    Request body (optional overrides):
        {
          "strategy": "daily",          // daily/strong/auction/wp2/all
          "lookback_days": 60,          // trading days to fetch
          "top_n": 10,                  // stocks per rebalance
          "rebalance_every": 5,         // rebalance cadence
          "initial_capital": 100000,
          "include_benchmark": true,
          "custom_codes": ["000001"]    // optional custom stock pool
        }

    Returns backtest result with real prices + metrics.
    """
    payload = request.get_json(silent=True) or {}

    strategy = payload.get("strategy", "all")
    lookback = min(max(payload.get("lookback_days", 60), 20), 120)
    top_n = min(max(payload.get("top_n", 10), 1), 30)
    rebalance_every = min(max(payload.get("rebalance_every", 5), 1), 20)
    initial_capital = payload.get("initial_capital", 100_000.0)
    include_benchmark = payload.get("include_benchmark", True)
    custom_codes = payload.get("custom_codes")

    # Validate strategy
    valid_strategies = set(STRATEGY_RANKERS.keys()) | {"all"}
    if strategy not in valid_strategies:
        return jsonify({
            "ok": False,
            "error": f"strategy must be one of: {sorted(valid_strategies)}",
        }), 400

    # Determine stock pool
    codes = custom_codes if custom_codes else REPRESENTATIVE_STOCKS

    # Fetch real K-line data
    try:
        price_history = _fetch_klines_concurrent(codes, lookback)
    except Exception as e:
        log.error(f"真实回测: K线获取失败: {e}", exc_info=True)
        return jsonify({"ok": False, "error": f"K线数据获取失败: {e}"}), 500

    if len(price_history) < 10:
        return jsonify({
            "ok": False,
            "error": f"获取的交易数据不足({len(price_history)}天)，请稍后重试或增加lookback",
        }), 500

    dates = sorted(price_history.keys())

    # Fetch benchmark if requested
    benchmark_kline = {}
    if include_benchmark:
        try:
            benchmark_kline = _fetch_benchmark_kline(lookback)
        except Exception:
            log.warning("真实回测: 基准数据获取失败，跳过")

    # Run strategy backtests
    strategies_to_run = (
        list(STRATEGY_RANKERS.keys()) if strategy == "all" else [strategy]
    )
    results: dict[str, dict] = {}

    for strat in strategies_to_run:
        rank_fn = STRATEGY_RANKERS[strat]
        picks_by_date: dict[str, list[str]] = {}
        for i in range(0, len(dates), rebalance_every):
            period_dates = dates[: i + 1]
            picked = rank_fn(price_history, period_dates, top_n)
            if picked:
                picks_by_date[dates[i]] = picked

        if not picks_by_date:
            continue

        cfg = BacktestConfig(
            initial_capital=initial_capital,
            top_n=top_n,
            rebalance_every=rebalance_every,
            name=strat,
        )
        inp = BacktestInput(
            price_history=price_history,
            picks_by_date=picks_by_date,
            config=cfg,
        )
        try:
            result = run(inp)
            results[strat] = result.to_dict()
            # Persist
            with _FILE_LOCK:
                save_result(result, RESULTS_DIR)
        except BacktestError as e:
            results[strat] = {"error": str(e)}

    # Run benchmark
    if include_benchmark and benchmark_kline:
        combined = {}
        for d in dates:
            combined[d] = {}
            if d in price_history:
                combined[d].update(price_history[d])
            if d in benchmark_kline:
                combined[d].update(benchmark_kline[d])

        bm_picks = {}
        for i in range(0, len(dates), rebalance_every):
            if dates[i] in benchmark_kline:
                bm_picks[dates[i]] = ["CSI300"]

        if bm_picks:
            cfg = BacktestConfig(
                initial_capital=initial_capital,
                top_n=1,
                rebalance_every=rebalance_every,
                name="CSI300基准",
            )
            inp = BacktestInput(
                price_history=combined,
                picks_by_date=bm_picks,
                config=cfg,
            )
            try:
                bm_result = run(inp)
                results["CSI300基准"] = bm_result.to_dict()
                with _FILE_LOCK:
                    save_result(bm_result, RESULTS_DIR)
            except BacktestError as e:
                results["CSI300基准"] = {"error": str(e)}

    # Summary
    summary: dict[str, dict] = {}
    for sid, r in results.items():
        if "error" in r:
            summary[sid] = {"error": r["error"]}
            continue
        m = r.get("metrics", {})
        summary[sid] = {
            "total_return_pct": m.get("total_return_pct", 0),
            "annualized_return_pct": m.get("annualized_return_pct", 0),
            "sharpe": m.get("sharpe", 0),
            "max_drawdown_pct": m.get("max_drawdown_pct", 0),
            "win_rate_pct": m.get("win_rate_pct", 0),
            "trading_days": m.get("trading_days", 0),
            "trades": m.get("trades", 0),
            "total_cost_pct": m.get("total_cost_pct", 0),
            "total_cost_amount": m.get("total_cost_amount", 0),
            "started_at": r.get("started_at", ""),
            "ended_at": r.get("ended_at", ""),
            "id": r.get("id", ""),
        }

    return jsonify({
        "ok": True,
        "strategy": strategy,
        "lookback_days": lookback,
        "top_n": top_n,
        "rebalance_every": rebalance_every,
        "initial_capital": initial_capital,
        "codes_used": len(codes),
        "dates_range": f"{dates[0]} ~ {dates[-1]}",
        "trading_days": len(dates),
        "results": results,
        "summary": summary,
    })


# ---------------------------------------------------------------------------
# Sample / canned input (for first-time users)
# ---------------------------------------------------------------------------
@backtest_bp.get("/sample")
def sample_input():
    """Return a tiny canned backtest input so the front-end can show a demo."""
    # 30 trading days, 3 codes, 3 rebalances
    days = [
        "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08",
        "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14", "2026-05-15",
        "2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22",
        "2026-05-25", "2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29",
        "2026-06-01", "2026-06-02",
    ]
    # Synthetic price walk with mild drift.
    closes = {
        "000001": [10.0, 10.1, 10.2, 10.15, 10.25, 10.3, 10.4, 10.35, 10.5, 10.55,
                   10.6, 10.7, 10.65, 10.8, 10.9, 11.0, 10.95, 11.1, 11.2, 11.3,
                   11.4, 11.5],
        "600519": [1500.0, 1510.0, 1505.0, 1520.0, 1530.0, 1525.0, 1535.0,
                   1540.0, 1535.0, 1545.0, 1550.0, 1545.0, 1560.0, 1570.0,
                   1565.0, 1580.0, 1590.0, 1585.0, 1600.0, 1605.0, 1610.0, 1620.0],
        "300750": [200.0, 202.0, 205.0, 203.0, 207.0, 210.0, 208.0, 212.0,
                   215.0, 213.0, 218.0, 220.0, 218.0, 222.0, 225.0, 223.0,
                   227.0, 230.0, 228.0, 232.0, 235.0, 233.0],
    }
    price_history: Dict[str, Dict[str, float]] = {}
    for i, d in enumerate(days):
        price_history[d] = {c: closes[c][i] for c in closes}

    # Rebalance on day 0, 5, 10, 15, 20
    rebal_days = [0, 5, 10, 15, 20]
    picks = {days[i]: ["000001", "600519", "300750"] for i in rebal_days}

    return jsonify({
        "ok": True,
        "sample": {
            "price_history": price_history,
            "picks_by_date": picks,
            "config": {
                "initial_capital": 100000,
                "top_n": 3,
                "rebalance_every": 5,
                "name": "demo-canned",
            },
        },
    })
