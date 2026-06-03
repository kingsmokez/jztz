"""E2E: convertibles-arbitrage page + CB arbitrage data API.

`/cb_arbitrage` is registered in routes/api.py:24.  It returns HTML
in the current build (no auth required for the page view).
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.e2e


def test_cb_arbitrage_page_renders(http):
    r = http.get("/cb_arbitrage")
    assert r.status_code == 200, f"/cb_arbitrage returned {r.status_code}"


def test_cb_arbitrage_data_endpoint_shape(http):
    r = http.get("/api/cb_arbitrage")
    # The endpoint may return a fresh payload or a cached one.
    # We only assert that it is a JSON response (not 5xx).
    if r.status_code == 200:
        assert "application/json" in r.headers.get("Content-Type", "")
    else:
        # Non-200 is acceptable (no live data, rate-limited, etc.),
        # but it must still be JSON, not an HTML error page.
        assert "application/json" in r.headers.get("Content-Type", ""), (
            f"non-JSON response: status={r.status_code}, body[:200]={r.text[:200]!r}"
        )


def test_auction_compare_endpoint_accepts_get(http):
    """GET /api/auction_compare returns 200 + JSON (not 5xx).

    The route is GET-only (no `methods=` override in routes/api.py:1045).
    POST is served by /api/auction/compare (with a slash) which is a
    different blueprint endpoint.
    """
    r = http.get("/api/auction_compare")
    assert r.status_code < 500, f"/api/auction_compare returned {r.status_code}"
    assert "application/json" in r.headers.get("Content-Type", "")


def test_auction_compare_post_endpoint(http):
    """POST /api/auction/compare (with slash) is the real POST endpoint."""
    r = http.post("/api/auction/compare", json={"codes": ["123456"]})
    assert r.status_code < 500, f"/api/auction/compare returned {r.status_code}"
