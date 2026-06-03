"""E2E: index page renders successfully.

Verifies the SPA entry point returns 200 and that the HTML contains
the project's brand keywords (so we know the right template was rendered).
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.e2e


def test_index_returns_200(http):
    r = http.get("/")
    assert r.status_code == 200, f"index returned {r.status_code}"


def test_index_renders_chinese_brand(http):
    r = http.get("/")
    body = r.text
    # The site is in Chinese; at least one brand keyword must appear
    assert any(
        kw in body
        for kw in ("价值投资", "选股", "智能", "jztz")
    ), f"index page did not contain any brand keyword. body[:300]={body[:300]!r}"


def test_index_content_type_is_html(http):
    r = http.get("/")
    ct = r.headers.get("Content-Type", "")
    assert "text/html" in ct, f"unexpected Content-Type: {ct!r}"


def test_index_daily_pick_alias(http):
    r = http.get("/daily_pick")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("Content-Type", "")
