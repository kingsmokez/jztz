"""Portfolio tracking — file-backed JSON store with P&L math.

Schema (data/portfolio.json)
-----------------------------
::

    {
        "version": 1,
        "positions": [
            {
                "id": "p_1717344000_abcd1234",
                "code": "000001",
                "name": "平安银行",
                "shares": 1000,
                "cost": 10.50,
                "buy_date": "2026-05-01",
                "notes": "value pick — financial sector"
            },
            ...
        ]
    }

Each position is keyed by a short, sortable, URL-safe ID so the
HTTP layer can address items by ID instead of composite (code+date).

Concurrency
-----------
* All public mutators acquire ``_lock`` before reading or writing.
* Writes go to ``<file>.tmp`` then ``os.replace()`` so a crash mid-write
  leaves the previous valid file in place.

P&L
---
``compute_pnl(position)`` looks up the latest live-quote snapshot
from the cache. When no snapshot exists for a code, ``current_price``
is ``None`` and P&L fields are not populated (the position is still
listed — just without today's valuation).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Storage layout
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_HERE, "data")
DEFAULT_FILE = os.path.join(DATA_DIR, "portfolio.json")
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class PortfolioError(Exception):
    """Domain error from the portfolio store."""


class ValidationError(PortfolioError):
    """Raised when a caller-supplied field is malformed."""


class NotFoundError(PortfolioError):
    """Raised when a position ID does not exist."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_CODE_RE = re.compile(r"^\d{6}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _new_id() -> str:
    """Time-sortable + short random suffix."""
    return f"p_{int(time.time())}_{secrets.token_hex(4)}"


def _validate_position(d: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
    """Return a cleaned position dict.

    When ``partial`` is True, missing required fields are tolerated
    (used by PUT to allow updates with only changed fields).  Raises
    :class:`ValidationError` on bad input.
    """
    required = ["code", "shares", "cost"]
    if not partial:
        for k in required:
            if k not in d:
                raise ValidationError(f"missing required field {k!r}")

    if "code" in d:
        code = str(d["code"]).strip()
        if not _CODE_RE.match(code):
            raise ValidationError(
                f"invalid code {d['code']!r}: must be 6 digits"
            )
        d["code"] = code

    if "shares" in d:
        try:
            shares = int(d["shares"])
        except (TypeError, ValueError) as e:
            raise ValidationError(f"shares must be int, got {d['shares']!r}") from e
        if shares <= 0:
            raise ValidationError("shares must be > 0")
        d["shares"] = shares

    if "cost" in d:
        try:
            cost = float(d["cost"])
        except (TypeError, ValueError) as e:
            raise ValidationError(f"cost must be number, got {d['cost']!r}") from e
        if cost < 0:
            raise ValidationError("cost must be >= 0")
        d["cost"] = round(cost, 4)

    if "buy_date" in d and d["buy_date"] not in (None, ""):
        buy_date = str(d["buy_date"]).strip()
        if not _DATE_RE.match(buy_date):
            raise ValidationError(
                f"buy_date must be YYYY-MM-DD, got {d['buy_date']!r}"
            )
        # The regex matches shape only; verify month/day are real
        # (e.g. rejects "2026-13-40" or "2026-02-30").
        try:
            date.fromisoformat(buy_date)
        except ValueError as e:
            raise ValidationError(
                f"buy_date not a real date: {d['buy_date']!r}"
            ) from e
        d["buy_date"] = buy_date
    elif "buy_date" in d:
        # Explicit null is allowed (caller can unset).
        pass

    if "name" in d and d["name"] is not None:
        d["name"] = str(d["name"]).strip()[:64]

    if "notes" in d and d["notes"] is not None:
        d["notes"] = str(d["notes"]).strip()[:512]

    return d


# ---------------------------------------------------------------------------
# File-backed store
# ---------------------------------------------------------------------------
class PortfolioStore:
    """Thread-safe JSON store for portfolio positions.

    A single instance is created by :func:`get_default_store` and
    shared between the HTTP layer and the library API.
    """

    def __init__(self, path: str = DEFAULT_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            self._write_atomic(self._empty())

    @staticmethod
    def _empty() -> Dict[str, Any]:
        return {"version": SCHEMA_VERSION, "positions": []}

    def _read(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return self._empty()
        except (json.JSONDecodeError, OSError):
            # Corrupt file — start fresh; the caller can recover.
            return self._empty()
        if "positions" not in data or not isinstance(data["positions"], list):
            return self._empty()
        return data

    def _write_atomic(self, data: Dict[str, Any]) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---- queries -------------------------------------------------------
    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._read()["positions"])

    def get(self, position_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for p in self._read()["positions"]:
                if p.get("id") == position_id:
                    return dict(p)
        return None

    # ---- mutations -----------------------------------------------------
    def add(self, position: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = _validate_position(dict(position))
        cleaned.setdefault("id", _new_id())
        cleaned.setdefault("name", "")
        cleaned.setdefault("notes", "")
        cleaned.setdefault("buy_date", "")
        with self._lock:
            data = self._read()
            # de-dupe by (code, buy_date) — same stock same day means update
            for existing in data["positions"]:
                if (
                    existing.get("code") == cleaned["code"]
                    and existing.get("buy_date") == cleaned.get("buy_date", "")
                    and existing.get("buy_date")  # only if explicit date
                ):
                    # Merge into the existing position.
                    existing["shares"] = int(existing.get("shares", 0)) + int(
                        cleaned["shares"]
                    )
                    # Recompute weighted-average cost.
                    prev_shares = int(existing["shares"]) - int(cleaned["shares"])
                    if prev_shares > 0:
                        new_cost = (
                            float(existing.get("cost", 0)) * prev_shares
                            + float(cleaned["cost"]) * int(cleaned["shares"])
                        ) / int(existing["shares"])
                        existing["cost"] = round(new_cost, 4)
                    if cleaned.get("notes") and not existing.get("notes"):
                        existing["notes"] = cleaned["notes"]
                    self._write_atomic(data)
                    return dict(existing)
            data["positions"].append(cleaned)
            self._write_atomic(data)
        return cleaned

    def update(self, position_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        if not patch:
            raise ValidationError("empty patch")
        cleaned = _validate_position(dict(patch), partial=True)
        with self._lock:
            data = self._read()
            for existing in data["positions"]:
                if existing.get("id") == position_id:
                    existing.update(cleaned)
                    self._write_atomic(data)
                    return dict(existing)
        raise NotFoundError(f"no such position: {position_id}")

    def delete(self, position_id: str) -> bool:
        with self._lock:
            data = self._read()
            before = len(data["positions"])
            data["positions"] = [
                p for p in data["positions"] if p.get("id") != position_id
            ]
            if len(data["positions"]) == before:
                return False
            self._write_atomic(data)
        return True

    def clear(self) -> int:
        """Remove all positions. Returns the number removed. Test/admin only."""
        with self._lock:
            data = self._read()
            n = len(data["positions"])
            self._write_atomic(self._empty())
        return n

    # ---- diagnostics ---------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        with self._lock:
            positions = self.list()
        total_cost = sum(p["shares"] * p["cost"] for p in positions)
        return {
            "position_count": len(positions),
            "total_shares": sum(p["shares"] for p in positions),
            "total_cost": round(total_cost, 2),
        }


# ---------------------------------------------------------------------------
# P&L computation
# ---------------------------------------------------------------------------
def _latest_price_for(code: str) -> Optional[float]:
    """Look up the most recent live-quote price for ``code``.

    Primary: cache snapshot (fast, in-process).
    Fallback: Tencent real-time API (1 request, ~100ms).
    Returns ``None`` when both sources fail.
    """
    if not code:
        return None

    # --- Primary: cache snapshot ---
    price = _price_from_cache(code)
    if price is not None:
        return price

    # --- Fallback: Tencent real-time API ---
    if not _has_live_quote_snapshot():
        return None
    price = _price_from_api(code)
    return price


def _has_live_quote_snapshot() -> bool:
    """Return True when the app has a live quote snapshot to fall back from."""
    try:
        from modules.cache_manager import cache
        return bool(cache.get("live_quotes_snapshot"))
    except Exception:
        return False


def _price_from_cache(code: str) -> Optional[float]:
    """Try to get price from the in-process cache snapshot."""
    try:
        from modules.cache_manager import cache
        snap = cache.get("live_quotes_snapshot")
    except Exception:
        return None
    if not snap:
        return None
    if isinstance(snap, list):
        rows = snap
    elif isinstance(snap, dict) and "data" in snap and isinstance(snap["data"], list):
        rows = snap["data"]
    elif isinstance(snap, dict):
        rows = []
    else:
        return None

    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("code", "")) != code:
            continue
        for key in ("price", "close", "now", "last", "current_price"):
            v = row.get(key)
            if v is None or v == "":
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _price_from_api(code: str) -> Optional[float]:
    """Fallback: fetch price from Tencent real-time API (single stock)."""
    try:
        import requests
        prefix = "sh" if code.startswith("6") else "sz"
        url = f"https://qt.gtimg.cn/q={prefix}{code}"
        resp = requests.get(url, timeout=3)
        text = resp.content.decode("gbk", errors="replace")
        parts = text.split("~")
        if len(parts) > 3:
            price_str = parts[3]
            price = float(price_str)
            if price > 0:
                return price
    except Exception:
        pass
    return None


def compute_pnl(position: Dict[str, Any]) -> Dict[str, Any]:
    """Augment a position with floating P&L fields.

    Returns a new dict; the original is untouched. When the live
    price isn't available, ``current_price`` is ``None`` and the
    P&L fields are not included.
    """
    out = dict(position)
    code = out.get("code", "")
    cost = float(out.get("cost", 0) or 0)
    shares = int(out.get("shares", 0) or 0)
    out["cost_value"] = round(cost * shares, 2)
    out["current_price"] = _latest_price_for(code)
    if out["current_price"] is not None and cost > 0:
        cur = float(out["current_price"])
        out["market_value"] = round(cur * shares, 2)
        out["profit"] = round(out["market_value"] - out["cost_value"], 2)
        out["profit_pct"] = round((cur - cost) / cost * 100.0, 2)
    return out


def compute_portfolio_pnl(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return per-position P&L plus aggregate totals."""
    enriched = [compute_pnl(p) for p in positions]
    total_cost = sum(p.get("cost_value", 0.0) for p in enriched)
    total_market = sum(p.get("market_value", 0.0) for p in enriched if "market_value" in p)
    if total_market:
        total_profit = round(total_market - total_cost, 2)
        total_profit_pct = round(
            (total_market - total_cost) / total_cost * 100.0, 2
        ) if total_cost > 0 else 0.0
    else:
        total_profit = 0.0
        total_profit_pct = 0.0
    return {
        "positions": enriched,
        "totals": {
            "cost": round(total_cost, 2),
            "market": round(total_market, 2) if total_market else None,
            "profit": total_profit,
            "profit_pct": total_profit_pct,
            "valued": sum(1 for p in enriched if "market_value" in p),
        },
    }


# ---------------------------------------------------------------------------
# Module-level default store
# ---------------------------------------------------------------------------
_default_lock = threading.Lock()
_default: Optional[PortfolioStore] = None


def get_default_store() -> PortfolioStore:
    """Return the module-level store, creating it on first call."""
    global _default
    with _default_lock:
        if _default is None:
            _default = PortfolioStore()
    return _default


def reset_default_store() -> None:
    """Drop the cached default. Test-only."""
    global _default
    with _default_lock:
        _default = None


__all__ = [
    "PortfolioError",
    "ValidationError",
    "NotFoundError",
    "PortfolioStore",
    "compute_pnl",
    "compute_portfolio_pnl",
    "get_default_store",
    "reset_default_store",
    "DEFAULT_FILE",
]
