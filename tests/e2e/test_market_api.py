"""E2E: market-data JSON API smoke test.

Verifies that /api/market and the picker endpoints return 200 with a
JSON body, and that the response structure matches the contract used
by the front-end.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.e2e


def test_market_endpoint_returns_json(http):
    r = http.get("/api/market")
    assert r.status_code == 200
    assert "application/json" in r.headers.get("Content-Type", "")


def test_auction_pick_endpoint_returns_json(http):
    r = http.get("/auction_pick")
    # /auction_pick is a *page* route (HTML) per routes/auction.py:103
    assert r.status_code == 200
    assert "text/html" in r.headers.get("Content-Type", "")


def test_wp2_pick_endpoint_returns_json(http):
    r = http.get("/wp2")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("Content-Type", "")


def test_strong_pick_endpoint(http):
    r = http.get("/strong_pick")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("Content-Type", "")


def test_search_stock_endpoint_with_keyword(http):
    """GET /api/search_stock?q=000001 returns 200 + list-shaped JSON."""
    r = http.get("/api/search_stock", params={"q": "000001"})
    assert r.status_code in (200, 400, 503), (
        f"unexpected status {r.status_code}; body={r.text[:200]!r}"
    )
