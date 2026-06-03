"""modules/errors.py 单元测试

覆盖 6 个异常类的 status/code 默认值与 to_dict 序列化，
以及 Flask errorhandler 的注册行为。
"""

import pytest
from flask import Flask, jsonify

from modules.errors import (
    ApiError,
    AuthError,
    NotFoundError,
    RateLimitError,
    UpstreamApiError,
    ValidationError,
    register_error_handlers,
)


class TestApiErrorBase:
    def test_default_status_and_code(self):
        e = ApiError("boom")
        assert e.status == 500
        assert e.code == "INTERNAL"
        assert e.message == "boom"
        assert isinstance(e, Exception)

    def test_custom_status_and_code(self):
        e = ApiError("nope", status=418, code="TEAPOT")
        assert e.status == 418
        assert e.code == "TEAPOT"

    def test_to_dict_shape(self):
        e = ApiError("err", status=400, code="BAD")
        assert e.to_dict() == {"error": "err", "code": "BAD"}


class TestSubclassDefaults:
    def test_upstream(self):
        assert UpstreamApiError("x").status == 502
        assert UpstreamApiError("x").code == "UPSTREAM_ERROR"

    def test_rate_limit(self):
        assert RateLimitError("x").status == 429
        assert RateLimitError("x").code == "RATE_LIMITED"

    def test_validation(self):
        assert ValidationError("x").status == 400
        assert ValidationError("x").code == "VALIDATION_ERROR"

    def test_not_found(self):
        assert NotFoundError("x").status == 404
        assert NotFoundError("x").code == "NOT_FOUND"

    def test_auth(self):
        assert AuthError("x").status == 401
        assert AuthError("x").code == "UNAUTHORIZED"


class TestFlaskHandlers:
    @pytest.fixture
    def app(self):
        app = Flask(__name__)
        app.config["TESTING"] = True

        @app.route("/raise_api")
        def _raise():
            raise ApiError("bad input", status=400, code="BAD")

        @app.route("/raise_429")
        def _429():
            raise RateLimitError("slow down")

        @app.route("/raise_500")
        def _500():
            raise ValueError("unhandled")

        register_error_handlers(app)
        return app

    def test_api_error_returns_custom_status(self, app):
        client = app.test_client()
        resp = client.get("/raise_api")
        assert resp.status_code == 400
        assert resp.get_json() == {"error": "bad input", "code": "BAD"}

    def test_rate_limit_error(self, app):
        client = app.test_client()
        resp = client.get("/raise_429")
        assert resp.status_code == 429
        body = resp.get_json()
        assert body["code"] == "RATE_LIMITED"
        assert body["error"] == "slow down"

    def test_404_handler(self, app):
        client = app.test_client()
        resp = client.get("/does-not-exist")
        assert resp.status_code == 404
        assert resp.get_json()["code"] == "NOT_FOUND"

    def test_500_unhandled_returns_internal(self, app):
        client = app.test_client()
        resp = client.get("/raise_500")
        assert resp.status_code == 500
        body = resp.get_json()
        assert body["code"] == "INTERNAL"
