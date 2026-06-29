"""Tests for /api/health, /api/live, /api/ready (k8s probes)."""
import pytest

from web_app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_live_always_200(client):
    """Liveness probe must always return 200 when process is alive."""
    r = client.get("/api/live")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "alive"
    assert "ts" in data
    assert isinstance(data["ts"], int)


def test_health_returns_components(client):
    """/api/health always 200 (never 5xx), includes version + components."""
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["version"] == "v20"
    assert "components" in data
    for name in ("eastmoney", "filesystem", "cache"):
        assert name in data["components"]


def test_health_status_in_allowed_set(client):
    """/api/health status is 'ok' or 'degraded', never 'error'."""
    r = client.get("/api/health")
    data = r.get_json()
    assert data["status"] in ("ok", "degraded")
    # ts must be present
    assert "ts" in data


def test_health_filesystem_ok(client):
    """Filesystem check is critical and must report ok in test env."""
    r = client.get("/api/health")
    data = r.get_json()
    assert data["components"]["filesystem"]["status"] == "ok"
    assert "logs" in data["components"]["filesystem"]
    assert "data" in data["components"]["filesystem"]


def test_health_cache_ok(client):
    """Cache check is critical and must report ok."""
    r = client.get("/api/health")
    data = r.get_json()
    assert data["components"]["cache"]["status"] == "ok"
    assert "keys" in data["components"]["cache"]
    assert isinstance(data["components"]["cache"]["keys"], int)


def test_ready_status_code_in_200_503(client):
    """/api/ready is 200 when critical deps ok, 503 when not. Never anything else."""
    r = client.get("/api/ready")
    assert r.status_code in (200, 503)
    data = r.get_json()
    assert data["status"] in ("ready", "not_ready")
    assert "components" in data
    assert "ts" in data


def test_health_endpoint_paths_registered(client):
    """All three probe paths exist and are routed (no 404)."""
    for path in ("/api/live", "/api/ready", "/api/health"):
        r = client.get(path)
        assert r.status_code != 404, f"{path} should be registered"


def test_health_content_type_json(client):
    """Health endpoints return JSON content type."""
    for path in ("/api/live", "/api/health"):
        r = client.get(path)
        assert r.content_type.startswith("application/json"), f"{path} content-type: {r.content_type}"
