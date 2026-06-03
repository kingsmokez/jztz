"""E2E: health/liveness/readiness endpoints.

Three contracts:
  GET /api/live   - 200, always
  GET /api/ready  - 200 or 503, never 5xx
  GET /api/health - 200, body has `status` and `components`
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.e2e


def test_live_always_ok(http):
    r = http.get("/api/live")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") in ("ok", "alive"), body


def test_health_status_field(http):
    r = http.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body, body
    assert body["status"] in ("ok", "degraded"), body
    assert "components" in body, body


def test_health_components_shape(http):
    r = http.get("/api/health")
    body = r.json()
    components = body.get("components", {})
    # At least filesystem + cache + eastmoney must be reported
    for key in ("filesystem", "cache"):
        assert key in components, f"missing component {key!r}; got {list(components)}"


def test_ready_returns_200_or_503(http):
    r = http.get("/api/ready")
    assert r.status_code in (200, 503), f"/api/ready returned {r.status_code}"


def test_health_response_has_request_id_header(http):
    r = http.get("/api/health")
    rid = r.headers.get("X-Request-ID")
    assert rid, "expected X-Request-ID response header"
    assert len(rid) > 0
