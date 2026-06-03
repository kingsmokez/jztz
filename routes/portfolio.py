"""HTTP routes for the portfolio store.

Endpoints
---------
* ``GET    /api/portfolio``        — list positions (+ optional P&L)
* ``POST   /api/portfolio``        — add a new position
* ``GET    /api/portfolio/<id>``   — fetch a single position
* ``PUT    /api/portfolio/<id>``   — update fields on a position
* ``DELETE /api/portfolio/<id>``   — remove a position
* ``GET    /api/portfolio/summary`` — aggregate stats (positions, totals, P&L)
* ``GET    /api/portfolio/export?format=csv|xlsx`` — quick export shortcut
"""
from __future__ import annotations

import os
import re
import tempfile
import uuid
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, jsonify, request

from modules.exporter import ExporterError, to_csv, to_xlsx
from modules.logger import log
from modules.portfolio import (
    NotFoundError,
    PortfolioStore,
    ValidationError,
    compute_portfolio_pnl,
    get_default_store,
)


portfolio_bp = Blueprint("portfolio", __name__, url_prefix="/api/portfolio")


# Position ID is a short, URL-safe slug — p_<ts>_<hex>. Reject anything
# else so a misbehaving client can't traverse to a system path.
_ID_RE = re.compile(r"^p_[A-Za-z0-9_]{1,64}$")


def _store() -> PortfolioStore:
    """Inject a hook here for tests to swap stores."""
    return get_default_store()


def _parse_bool(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _clean_payload(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if data is None:
        raise ValidationError("missing JSON body")
    if not isinstance(data, dict):
        raise ValidationError("body must be a JSON object")
    return data


def _err_response(exc: Exception) -> Response:
    if isinstance(exc, ValidationError):
        return jsonify({"success": False, "error": str(exc)}), 400
    if isinstance(exc, NotFoundError):
        return jsonify({"success": False, "error": str(exc)}), 404
    log.error(f"portfolio error: {exc}", exc_info=True)
    return jsonify({"success": False, "error": f"internal: {exc}"}), 500


# ---------------------------------------------------------------------------
# List / add
# ---------------------------------------------------------------------------
@portfolio_bp.get("")
def list_positions():
    """List all positions.

    Query params:
      * ``pnl=true`` — augment each position with current-price
        P&L fields (looks up the live-quote cache).
      * ``pnl=true`` (default false) — see :func:`compute_portfolio_pnl`.
    """
    store = _store()
    try:
        positions = store.list()
        if _parse_bool(request.args.get("pnl")):
            payload = compute_portfolio_pnl(positions)
            return jsonify({
                "success": True,
                "count": len(positions),
                **payload,
            })
        return jsonify({
            "success": True,
            "count": len(positions),
            "positions": positions,
        })
    except Exception as e:
        return _err_response(e)


@portfolio_bp.post("")
def add_position():
    store = _store()
    try:
        data = _clean_payload(request.get_json(silent=True))
        position = store.add(data)
    except Exception as e:
        return _err_response(e)
    return jsonify({"success": True, "position": position}), 201


# ---------------------------------------------------------------------------
# Single-position GET / PUT / DELETE
# ---------------------------------------------------------------------------
@portfolio_bp.get("/<position_id>")
def get_position(position_id: str):
    if not _ID_RE.match(position_id):
        return jsonify({"success": False, "error": "invalid id"}), 400
    store = _store()
    try:
        position = store.get(position_id)
        if position is None:
            return jsonify({"success": False, "error": "not found"}), 404
        if _parse_bool(request.args.get("pnl")):
            position = compute_portfolio_pnl([position])["positions"][0]
        return jsonify({"success": True, "position": position})
    except Exception as e:
        return _err_response(e)


@portfolio_bp.put("/<position_id>")
def update_position(position_id: str):
    if not _ID_RE.match(position_id):
        return jsonify({"success": False, "error": "invalid id"}), 400
    store = _store()
    try:
        patch = _clean_payload(request.get_json(silent=True))
        position = store.update(position_id, patch)
    except Exception as e:
        return _err_response(e)
    return jsonify({"success": True, "position": position})


@portfolio_bp.delete("/<position_id>")
def delete_position(position_id: str):
    if not _ID_RE.match(position_id):
        return jsonify({"success": False, "error": "invalid id"}), 400
    store = _store()
    try:
        ok = store.delete(position_id)
    except Exception as e:
        return _err_response(e)
    if not ok:
        return jsonify({"success": False, "error": "not found"}), 404
    return jsonify({"success": True, "deleted": position_id})


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
@portfolio_bp.get("/summary")
def summary():
    store = _store()
    try:
        positions = store.list()
        base = store.summary()
        enriched = compute_portfolio_pnl(positions)
        return jsonify({
            "success": True,
            **base,
            **enriched["totals"],
            "valued_positions": enriched["totals"]["valued"],
        })
    except Exception as e:
        return _err_response(e)


# ---------------------------------------------------------------------------
# Export shortcut
# ---------------------------------------------------------------------------
@portfolio_bp.get("/export")
def export_positions():
    """Download positions as CSV / XLSX.

    Reuses the same encoder as ``/api/export`` but pre-binds to the
    portfolio data source so the front-end can link straight here.
    """
    fmt = (request.args.get("format", "csv") or "csv").strip().lower()
    include_pnl = _parse_bool(request.args.get("pnl"))
    if fmt not in {"csv", "xlsx"}:
        return jsonify({"success": False, "error": "format must be csv or xlsx"}), 400

    store = _store()
    try:
        positions = store.list()
        if include_pnl:
            positions = compute_portfolio_pnl(positions)["positions"]
    except Exception as e:
        return _err_response(e)

    if not positions:
        return jsonify({"success": False, "error": "no positions to export"}), 404

    prefer = [
        "id", "code", "name", "shares", "cost", "cost_value",
        "current_price", "market_value", "profit", "profit_pct",
        "buy_date", "notes",
    ]
    try:
        if fmt == "csv":
            body = to_csv(positions, columns=prefer, sheet_name="portfolio")
            mime = "text/csv; charset=utf-8"
        else:
            body = to_xlsx(
                positions, columns=prefer,
                sheet_name="portfolio",
                title=f"投资组合 (共 {len(positions)} 条)",
            )
            mime = (
                "application/vnd.openxmlformats-"
                "officedocument.spreadsheetml.sheet"
            )
    except ExporterError as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return Response(
        body,
        status=200,
        mimetype=mime,
        headers={
            "Content-Disposition": (
                f'attachment; filename="portfolio.{fmt}"; '
                f"filename*=UTF-8''\xe6\x8a\x95\xe8\xb5\x84\xe7\xbb\x84\xe5\x90\x88.{fmt}"
            ),
            "X-Row-Count": str(len(positions)),
        },
    )
