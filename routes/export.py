"""Export endpoints — download picks/quotes as CSV or Excel.

Endpoints
---------
* ``GET /api/export?type=<t>&format=<f>`` — single file download

Types
-----
* ``daily_quotes``  — daily-pick results (morning + afternoon)
* ``live_quotes``   — last live quote snapshot from cache
* ``auction_quotes`` — auction-pick results from cache

Formats
-------
* ``csv``  — text/csv; charset=utf-8 (BOM-prefixed for Excel)
* ``xlsx`` — application/vnd.openxmlformats

The ``session`` query parameter can narrow ``daily_quotes`` to
``morning`` or ``afternoon``.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, Response, jsonify, request

from modules.exporter import (
    ExporterError,
    collect_auction_quotes,
    collect_daily_quotes,
    collect_live_quotes,
    to_csv,
    to_xlsx,
)
from modules.logger import log


export_bp = Blueprint("export", __name__, url_prefix="/api")


_TYPES: Dict[str, Callable[[Optional[str]], List[Dict[str, Any]]]] = {
    "daily_quotes": lambda session=None: collect_daily_quotes(session),
    "live_quotes": lambda _session=None: collect_live_quotes(),
    "auction_quotes": lambda _session=None: collect_auction_quotes(),
}


_TYPE_LABELS = {
    "daily_quotes": "每日选股",
    "live_quotes": "实时行情",
    "auction_quotes": "拍卖选股",
}


# Columns exported first, in order. Other columns are appended
# automatically. Tailored to A-share stock dict shape.
_DAILY_QUOTES_PREFER = [
    "session_label", "rank", "code", "name",
    "score", "price", "change_pct",
    "pe", "pb", "roe", "debt_ratio", "net_margin",
    "turnover_rate", "amount", "industry", "pick_time",
]

_LIVE_QUOTES_PREFER = [
    "code", "name", "price", "change", "change_pct",
    "volume", "amount", "pe", "pb", "market_cap",
]

_AUCTION_QUOTES_PREFER = [
    "code", "name", "score", "price", "change_pct",
    "pe", "pb", "roe", "turnover_rate", "industry",
]


def _validate_type(t: str) -> Optional[str]:
    """Return an error message, or None if valid."""
    if not t:
        return "missing `type` query parameter"
    if t not in _TYPES:
        return (
            f"unknown type {t!r}; "
            f"supported: {', '.join(sorted(_TYPES))}"
        )
    return None


def _validate_format(f: str) -> Optional[str]:
    if not f:
        return "missing `format` query parameter"
    f = f.lower()
    if f not in {"csv", "xlsx"}:
        return f"unknown format {f!r}; supported: csv, xlsx"
    return None


def _preferred_columns(type_name: str) -> List[str]:
    if type_name == "daily_quotes":
        return _DAILY_QUOTES_PREFER
    if type_name == "live_quotes":
        return _LIVE_QUOTES_PREFER
    if type_name == "auction_quotes":
        return _AUCTION_QUOTES_PREFER
    return []


def _timestamp_str() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _ascii_filename(label: str) -> str:
    """Return a filename safe for both Chinese browsers and HTTP headers."""
    ascii_part = re.sub(r"[^A-Za-z0-9_-]+", "_", label)[:32] or "export"
    return f"{ascii_part}_{_timestamp_str()}"


def _filename_header(display_name: str, ext: str) -> str:
    """RFC 5987 Content-Disposition that supports UTF-8 names."""
    return (
        f"attachment; "
        f'filename="{_ascii_filename(display_name)}.{ext}"; '
        f"filename*=UTF-8''{display_name}_{_timestamp_str()}.{ext}"
    )


@export_bp.get("/export")
def export():
    """Download a slice of the system state as CSV/XLSX."""
    type_name = request.args.get("type", "").strip()
    # Treat absent / blank `format` as missing — don't silently fall back to csv
    # because that hides a client bug.
    fmt_raw = request.args.get("format", "")
    fmt = fmt_raw.strip().lower() if fmt_raw is not None else ""
    session = request.args.get("session", "").strip() or None

    err = _validate_type(type_name)
    if err:
        return jsonify({"success": False, "error": err}), 400
    err = _validate_format(fmt)
    if err:
        return jsonify({"success": False, "error": err}), 400

    if session and session not in {"morning", "afternoon"}:
        return jsonify({
            "success": False,
            "error": "session must be 'morning' or 'afternoon'",
        }), 400

    try:
        rows = _TYPES[type_name](session)
    except Exception as e:
        log.error(f"export collect {type_name} failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"collection failed: {e}"}), 500

    if not rows:
        return jsonify({
            "success": False,
            "error": f"no data available for type={type_name!r}",
            "rows": 0,
        }), 404

    columns = _preferred_columns(type_name)
    sheet_name = _TYPE_LABELS.get(type_name, type_name)
    display_name = (
        f"{sheet_name}-{session}" if session else sheet_name
    )

    try:
        if fmt == "csv":
            body = to_csv(rows, columns=columns, sheet_name=sheet_name)
            mime = "text/csv; charset=utf-8"
        else:
            body = to_xlsx(
                rows,
                columns=columns,
                sheet_name=sheet_name,
                title=f"{display_name} (共 {len(rows)} 条)",
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
            "Content-Disposition": _filename_header(display_name, fmt),
            "X-Row-Count": str(len(rows)),
        },
    )


@export_bp.get("/export/types")
def export_types():
    """Lightweight metadata — what types/formats are supported."""
    return jsonify({
        "success": True,
        "types": [
            {
                "id": tid,
                "label": _TYPE_LABELS.get(tid, tid),
                "formats": ["csv", "xlsx"],
                "session_param": tid == "daily_quotes",
            }
            for tid in sorted(_TYPES)
        ],
        "default_format": "csv",
    })
