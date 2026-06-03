"""E2E: login page is reachable.

Full auth flow is a Phase 5 deliverable. For now we just verify the
page renders without 5xx.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.e2e


def test_login_page_renders(http):
    r = http.get("/login")
    assert r.status_code == 200, f"/login returned {r.status_code}"
    assert "text/html" in r.headers.get("Content-Type", "")


def test_login_page_no_5xx(http):
    """Sanity: no broken templates, no missing imports at import time."""
    for path in ("/login",):
        r = http.get(path)
        assert r.status_code < 500, f"{path} returned 5xx: {r.status_code}"
