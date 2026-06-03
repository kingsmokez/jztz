"""API响应模块单元测试"""

import pytest
from modules.api_response import api_success, api_error, api_not_found, api_server_error


class TestApiResponse:
    def test_success(self):
        resp, status = api_success({"key": "value"})
        assert status == 200
        # Works with both Flask Response and plain dict
        if hasattr(resp, 'get_json'):
            data = resp.get_json()
        else:
            data = resp
        assert data["success"] is True
        assert data["data"]["key"] == "value"
        assert data["error"] is None

    def test_error(self):
        resp, status = api_error("参数错误")
        assert status == 400
        if hasattr(resp, 'get_json'):
            data = resp.get_json()
        else:
            data = resp
        assert data["success"] is False
        assert data["error"] == "参数错误"

    def test_not_found(self):
        resp, status = api_not_found()
        assert status == 404

    def test_server_error(self):
        resp, status = api_server_error()
        assert status == 500