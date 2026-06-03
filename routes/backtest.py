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
from modules.logger import log


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
