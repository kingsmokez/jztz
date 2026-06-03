"""价值投资之王智能选股系统 - Flask应用

重构要点:
1. App Factory模式，蓝图注册
2. 安全: 移除硬编码secret/token，添加速率限制
3. 日志: logging替代print
4. 错误处理: 全局错误处理器，具体异常替代bare except
5. 配置: 统一Config管理
"""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, g, jsonify, render_template, request, Response
from werkzeug.exceptions import HTTPException

from modules.config import Config, load_config
from modules.errors import ApiError
from modules.logger import log, set_request_id

# 全局选股数据
_picker_lock = threading.Lock()
DAILY_PICK_DATA: Optional[list[dict]] = None
AUCTION_PICK_DATA: Optional[list[dict]] = None
WP2_PICK_DATA: Optional[list[dict]] = None
STRONG_PICK_DATA: Optional[list[dict]] = None

_config = load_config()
_scheduler_started = False


def create_app() -> Flask:
    """App Factory"""
    app = Flask(__name__)
    app.secret_key = _config.server.secret_key

    if not _config.server.debug:
        app.config["TEMPLATES_AUTO_RELOAD"] = False

    # 注册蓝图
    from routes import ALL_BLUEPRINTS
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)

    # 全局错误处理器
    _register_error_handlers(app)

    # 速率限制
    _setup_rate_limit(app)

    # 请求日志中间件 (request_id 串联)
    _setup_request_logging(app)

    # SSE推送
    @app.route("/api/sse")
    def sse_stream():
        def generate():
            try:
                # 初始连接事件, 让客户端知道已就绪
                yield f"event: connected\ndata: {json.dumps({'status': 'ok'})}\n\n"
                while True:
                    # 客户端断开检测 (socket 关闭)
                    sock = request.environ.get("werkzeug.socket")
                    if sock is None:
                        log.debug("SSE 客户端已断开 (no socket)")
                        return
                    data = {
                        "daily": get_picker_data() is not None,
                        "auction": get_auction_data() is not None,
                        "wp2": get_wp2_data() is not None,
                    }
                    yield f"event: update\ndata: {json.dumps(data)}\n\n"
                    # 5s sleep 分片, 让断开检测 < 1s 生效
                    for _ in range(5):
                        time.sleep(1)
            except (GeneratorExit, BrokenPipeError, ConnectionResetError):
                log.debug("SSE 客户端断开 (generator closed)")
                return
            except Exception:
                log.error("SSE 推流出错", exc_info=True)
                return
        return Response(generate(), mimetype="text/event-stream")

    log.info(f"应用初始化完成: host={_config.server.host}, port={_config.server.port}")
    return app


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def handle_api_error(e: ApiError):
        log.warning(f"API 业务异常: {e.code} - {e.message} ({request.path})")
        return jsonify(e.to_dict()), e.status

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "error": "资源不存在", "code": "NOT_FOUND"}), 404
        return render_template("index.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        log.error(f"500错误: {request.path}, {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": "INTERNAL"}), 500

    @app.errorhandler(Exception)
    def handle_exception(e):
        # Preserve HTTPException status codes (404/405/400/etc) instead of
        # masking them as 500. Werkzeug raises MethodNotAllowed, NotFound,
        # BadRequest etc. as HTTPException subclasses; let them surface as
        # their real status with a JSON body for /api/* callers.
        if isinstance(e, HTTPException):
            if request.path.startswith("/api/"):
                payload = {
                    "success": False,
                    "error": e.description or "请求错误",
                    "code": (e.name or "ERROR").upper().replace(" ", "_"),
                }
                return jsonify(payload), e.code or 500
            return e  # let Flask serve the default HTML error page
        log.error(f"未捕获异常: {traceback.format_exc()}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": "INTERNAL"}), 500


def _setup_rate_limit(app: Flask) -> None:
    from modules.rate_config import get_limit_for_path

    _rate_store: dict[tuple[str, int], list[float]] = {}
    _rate_lock = threading.Lock()

    @app.before_request
    def check_rate_limit():
        limit = get_limit_for_path(request.path)
        if limit is None:
            return None  # 静态资源 / SSE 放行

        ip = request.remote_addr or "unknown"
        bucket_key = (ip, limit.window_seconds)
        now = time.time()
        with _rate_lock:
            history = _rate_store.setdefault(bucket_key, [])
            history[:] = [t for t in history if now - t < limit.window_seconds]
            if len(history) >= limit.max_requests:
                resp = jsonify({
                    "success": False,
                    "error": "请求过于频繁，请稍后再试",
                    "code": "RATE_LIMITED",
                    "limit": limit.max_requests,
                    "window_seconds": limit.window_seconds,
                })
                resp.headers["Retry-After"] = str(limit.window_seconds)
                return resp, 429
            history.append(now)
        return None


def start_scheduler() -> None:
    """启动后台调度器 - 只在main中调用"""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    from modules.scheduler import Scheduler
    scheduler = Scheduler()
    scheduler.add_job("daily_picker", run_daily_picker, interval_seconds=300)
    scheduler.add_job("auction_picker", run_auction_picker, interval_seconds=60)
    scheduler.add_job("wp2_picker", run_wp2_picker, interval_seconds=300)
    scheduler.add_job("strong_picker", run_strong_picker, interval_seconds=300)
    scheduler.start_background()
    log.info("后台调度已启动")


# === 数据访问接口 ===

def get_picker_data() -> Optional[list[dict]]:
    with _picker_lock:
        return DAILY_PICK_DATA

def get_auction_data() -> Optional[list[dict]]:
    with _picker_lock:
        return AUCTION_PICK_DATA

def get_wp2_data() -> Optional[list[dict]]:
    with _picker_lock:
        return WP2_PICK_DATA

def get_strong_data() -> Optional[list[dict]]:
    with _picker_lock:
        return STRONG_PICK_DATA


def clear_strong_data() -> None:
    global STRONG_PICK_DATA
    with _picker_lock:
        STRONG_PICK_DATA = None


# === 选股执行函数 ===

def run_daily_picker() -> list[dict]:
    global DAILY_PICK_DATA
    try:
        from modules.stock_picker import run_picker
        result = run_picker()
        with _picker_lock:
            DAILY_PICK_DATA = result
        log.info(f"每日选股完成: {len(result)} 只")
        return result
    except Exception as e:
        log.error(f"每日选股失败: {e}", exc_info=True)
        return []

def run_auction_picker() -> list[dict]:
    global AUCTION_PICK_DATA
    try:
        from modules.auction_picker import run_auction_picker as _run
        result = _run()
        with _picker_lock:
            AUCTION_PICK_DATA = result
        log.info(f"集合竞价选股完成: {len(result)} 只")
        return result
    except Exception as e:
        log.error(f"集合竞价选股失败: {e}", exc_info=True)
        return []

def run_wp2_picker() -> list[dict]:
    global WP2_PICK_DATA
    try:
        from modules.wp2_picker import run_wp2_picker as _run
        result = _run()
        with _picker_lock:
            WP2_PICK_DATA = result
        log.info(f"WP2选股完成: {len(result)} 只")
        return result
    except Exception as e:
        log.error(f"WP2选股失败: {e}", exc_info=True)
        return []

def run_strong_picker() -> list[dict]:
    global STRONG_PICK_DATA
    try:
        from modules.strong_stock_picker import run_strong_stock_picker
        result = run_strong_stock_picker()
        with _picker_lock:
            STRONG_PICK_DATA = result
        log.info(f"强势选股完成: {len(result)} 只")
        return result
    except Exception as e:
        log.error(f"强势选股失败: {e}", exc_info=True)
        return []

def run_auction_compare(params: dict) -> dict:
    try:
        from modules.auction_picker import compare_auction
        return compare_auction(params)
    except Exception as e:
        log.error(f"集合竞价对比失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

def api_search_stock(keyword: str) -> list[dict]:
    try:
        from modules.data_fetcher import search_stock
        return search_stock(keyword)
    except Exception as e:
        log.error(f"搜索失败: {keyword}, {e}")
        return []


def _setup_request_logging(app: Flask) -> None:
    """为每个请求注入 request_id, 响应头回传, 日志可按 rid 串联."""

    @app.before_request
    def _assign_request_id():
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        g.request_id = rid
        set_request_id(rid)
        log.debug(f"→ {request.method} {request.path}")

    @app.after_request
    def _log_response(response):
        rid = getattr(g, "request_id", "-")
        response.headers["X-Request-ID"] = rid
        log.debug(f"← {response.status_code} {request.method} {request.path}")
        return response


# === 入口 ===

app = create_app()

if __name__ == "__main__":
    sys.modules["web_app"] = sys.modules["__main__"]
    start_scheduler()
    app.run(
        host=_config.server.host,
        port=_config.server.port,
        debug=_config.server.debug,
    )
