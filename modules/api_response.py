"""统一API响应格式"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ApiResponse:
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
        }

    def to_json(self):
        """转换为Flask JSON响应（需要应用上下文）"""
        from flask import jsonify
        return jsonify(self.to_dict())


def api_success(data: Any = None) -> tuple:
    resp = ApiResponse(success=True, data=data)
    try:
        return resp.to_json(), 200
    except RuntimeError:
        # Flask上下文不可用时，返回纯字典
        return resp.to_dict(), 200


def api_error(message: str, status_code: int = 400) -> tuple:
    resp = ApiResponse(success=False, error=message)
    try:
        return resp.to_json(), status_code
    except RuntimeError:
        return resp.to_dict(), status_code


def api_not_found(message: str = "资源不存在") -> tuple:
    resp = ApiResponse(success=False, error=message)
    try:
        from flask import jsonify
        return jsonify(resp.to_dict()), 404
    except RuntimeError:
        return resp.to_dict(), 404


def api_server_error(message: str = "服务器内部错误") -> tuple:
    resp = ApiResponse(success=False, error=message)
    try:
        from flask import jsonify
        return jsonify(resp.to_dict()), 500
    except RuntimeError:
        return resp.to_dict(), 500