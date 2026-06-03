"""统一异常类与 Flask 错误处理

将业务异常、外部 API 异常、限流异常统一为可序列化的结构，
让前端能通过 `code` 字段做分支处理、`status` 字段判断 HTTP 状态。
"""

from __future__ import annotations

from typing import Optional

from flask import jsonify


class ApiError(Exception):
    """API 业务异常基类

    Attributes:
        message: 人类可读的错误描述
        code: 错误代码（前端用于分支处理）
        status: HTTP 状态码
    """

    status: int = 500
    code: str = "INTERNAL"

    def __init__(
        self,
        message: str,
        status: Optional[int] = None,
        code: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code

    def to_dict(self) -> dict:
        return {"error": self.message, "code": self.code}


class UpstreamApiError(ApiError):
    """外部 API 调用失败（Eastmoney / Tencent / Sina / AkShare 等）"""

    code = "UPSTREAM_ERROR"
    status = 502


class RateLimitError(ApiError):
    """限流错误（flask_limiter 触发）"""

    code = "RATE_LIMITED"
    status = 429


class ValidationError(ApiError):
    """请求参数校验错误"""

    code = "VALIDATION_ERROR"
    status = 400


class NotFoundError(ApiError):
    """资源未找到"""

    code = "NOT_FOUND"
    status = 404


class AuthError(ApiError):
    """认证失败"""

    code = "UNAUTHORIZED"
    status = 401


def register_error_handlers(app):
    """注册 Flask 全局错误处理器

    将 ApiError 子类、429、500 统一为 `{"error": "...", "code": "..."}` 结构。
    用法：`app = create_app()` 后调用 `register_error_handlers(app)`。
    """

    @app.errorhandler(ApiError)
    def _handle_api_error(e: ApiError):
        return jsonify(e.to_dict()), e.status

    @app.errorhandler(429)
    def _handle_429(e):
        return jsonify({"error": "Too Many Requests", "code": "RATE_LIMITED"}), 429

    @app.errorhandler(404)
    def _handle_404(e):
        return jsonify({"error": "Not Found", "code": "NOT_FOUND"}), 404

    @app.errorhandler(500)
    def _handle_500(e):
        return jsonify({"error": "Internal Server Error", "code": "INTERNAL"}), 500

    @app.errorhandler(Exception)
    def _handle_exception(e):
        # 必须放最后 — Flask 按最具体匹配，ApiError 子类优先
        import traceback
        return jsonify({
            "error": "Internal Server Error",
            "code": "INTERNAL",
            "detail": str(e) if app.debug else None,
        }), 500


__all__ = [
    "ApiError",
    "UpstreamApiError",
    "RateLimitError",
    "ValidationError",
    "NotFoundError",
    "AuthError",
    "register_error_handlers",
]
