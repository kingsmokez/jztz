"""Tests for /api/openapi.json and /api/docs (Swagger UI)."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def client():
    from web_app import create_app
    app = create_app()
    return app.test_client()


class TestOpenApiJson:
    def test_returns_200(self, client):
        r = client.get("/api/openapi.json")
        assert r.status_code == 200

    def test_content_type_json(self, client):
        r = client.get("/api/openapi.json")
        assert r.headers.get("Content-Type", "").startswith("application/json")

    def test_spec_is_valid_json(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        assert isinstance(spec, dict)

    def test_spec_openapi_version(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        assert spec.get("openapi", "").startswith("3.")

    def test_spec_has_info(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        assert "title" in spec.get("info", {})
        assert "version" in spec.get("info", {})

    def test_documents_health_endpoints(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        paths = spec.get("paths", {})
        assert "/api/live" in paths
        assert "/api/ready" in paths
        assert "/api/health" in paths

    def test_documents_metrics(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        assert "/api/metrics" in spec.get("paths", {})

    def test_documents_sse(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        assert "/api/sse" in spec.get("paths", {})

    def test_documents_market(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        paths = spec.get("paths", {})
        assert "/api/market" in paths
        assert "/api/quote" in paths

    def test_documents_pickers(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        paths = spec.get("paths", {})
        assert "/api/daily_pick" in paths
        assert "/api/auction_pick" in paths

    def test_documents_admin(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        paths = spec.get("paths", {})
        assert "/api/cache/stats" in paths
        assert "/api/industries" in paths

    def test_has_health_schema(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        schemas = spec.get("components", {}).get("schemas", {})
        assert "Health" in schemas
        assert "ApiResponse" in schemas
        assert "Readiness" in schemas

    def test_servers_defined(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        assert len(spec.get("servers", [])) >= 1


class TestSwaggerUi:
    def test_returns_200(self, client):
        r = client.get("/api/docs")
        assert r.status_code == 200

    def test_content_type_html(self, client):
        r = client.get("/api/docs")
        assert r.headers.get("Content-Type", "").startswith("text/html")

    def test_body_contains_swagger_ui(self, client):
        r = client.get("/api/docs")
        body = r.data.decode("utf-8")
        assert "swagger-ui" in body.lower()
        assert "SwaggerUIBundle" in body

    def test_body_contains_spec_url(self, client):
        r = client.get("/api/docs")
        body = r.data.decode("utf-8")
        assert "/api/openapi.json" in body

    def test_trailing_slash_works(self, client):
        r = client.get("/api/docs/")
        assert r.status_code == 200


class TestSpecIsValid:
    """Static structural checks — every path has at least one method."""

    def test_every_path_has_method(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        for path, ops in spec.get("paths", {}).items():
            methods = [k for k in ops if k in ("get", "post", "put", "delete", "patch")]
            assert methods, f"path {path} has no HTTP method"

    def test_every_method_has_summary(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        for path, ops in spec.get("paths", {}).items():
            for method, op in ops.items():
                if method not in ("get", "post", "put", "delete", "patch"):
                    continue
                assert "summary" in op, f"{method.upper()} {path} missing summary"

    def test_every_method_has_responses(self, client):
        r = client.get("/api/openapi.json")
        spec = json.loads(r.data)
        for path, ops in spec.get("paths", {}).items():
            for method, op in ops.items():
                if method not in ("get", "post", "put", "delete", "patch"):
                    continue
                assert "responses" in op, f"{method.upper()} {path} missing responses"
