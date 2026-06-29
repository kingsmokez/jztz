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
import os
import queue
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime as _datetime
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, g, jsonify, render_template, request, Response
from werkzeug.exceptions import HTTPException

from modules.config import Config, load_config
from modules.errors import ApiError
from modules.logger import log, set_request_id

# 全局选股数据
# 读写锁：读操作（API获取数据）并发无阻塞，写操作（选股更新数据）独占
_picker_read_lock = threading.RLock()   # 读锁（可重入，多读并发）
_picker_write_lock = threading.Lock()   # 写锁（独占）
DAILY_PICK_DATA: Optional[list[dict]] = None
AUCTION_PICK_DATA: Optional[list[dict]] = None
WP2_PICK_DATA: Optional[list[dict]] = None
STRONG_PICK_DATA: Optional[list[dict]] = None


class SSEHub:
    """SSE推送中心 — 后台线程统一推送，请求线程仅订阅队列

    优势：SSE连接不再阻塞工作线程做5秒轮询，只需等队列消息。
    后台线程每5秒推送一次数据可用状态到所有订阅者。
    """

    def __init__(self) -> None:
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """启动后台推送线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._broadcast_loop, daemon=True, name="sse_hub")
        self._thread.start()
        log.info("SSE推送中心启动")

    def subscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.append(q)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def connection_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def _broadcast(self, message: str) -> None:
        """向所有订阅者广播消息"""
        with self._lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(message)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)

    def _broadcast_loop(self) -> None:
        """后台循环：每5秒推送数据可用状态"""
        while self._running:
            try:
                data = {
                    "daily": DAILY_PICK_DATA is not None,
                    "auction": AUCTION_PICK_DATA is not None,
                    "wp2": WP2_PICK_DATA is not None,
                    "strong": STRONG_PICK_DATA is not None,
                }
                msg = f"event: update\ndata: {json.dumps(data)}\n\n"
                self._broadcast(msg)
            except Exception as e:
                log.error(f"SSE推送异常: {e}")
            time.sleep(5)


_sse_hub = SSEHub()

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_DAILY_CACHE_FILE = os.path.join(_CACHE_DIR, "daily_pick_raw_cache.json")
_AUCTION_CACHE_FILE = os.path.join(_CACHE_DIR, "auction_pick_cache.json")
_WP2_CACHE_FILE = os.path.join(_CACHE_DIR, "wp2_pick_cache.json")
_STRONG_CACHE_FILE = os.path.join(_CACHE_DIR, "strong_pick_cache.json")

_config = load_config()
_scheduler_started = False

# 公共JS: fetchWithRetry 封装（超时+重试+断线检测）
COMMON_JS = r"""
/**
 * fetchWithRetry - 带超时和重试的 fetch 封装
 * 
 * @param {string} url - 请求URL
 * @param {object} options - fetch选项
 * @param {number} [timeout=30000] - 超时时间(ms)
 * @param {number} [retries=2] - 重试次数
 * @param {number} [retryDelay=1000] - 重试延迟(ms)
 * @returns {Promise<Response>}
 */
async function fetchWithRetry(url, options = {}, timeout = 30000, retries = 2, retryDelay = 1000) {
    for (let attempt = 0; attempt <= retries; attempt++) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeout);
        
        try {
            const resp = await fetch(url, {
                ...options,
                signal: controller.signal,
            });
            clearTimeout(timeoutId);
            
            if (resp.ok) return resp;
            
            // 服务器错误(5xx)可重试，客户端错误(4xx)不重试
            if (resp.status >= 500 && attempt < retries) {
                console.warn(`请求失败(${resp.status}), ${retryDelay}ms后重试 (${attempt+1}/${retries})...`);
                await new Promise(r => setTimeout(r, retryDelay));
                retryDelay *= 1.5;  // 递增延迟
                continue;
            }
            return resp;
        } catch (err) {
            clearTimeout(timeoutId);
            
            if (err.name === 'AbortError') {
                console.warn(`请求超时(${timeout}ms), 重试 (${attempt+1}/${retries})...`);
            } else {
                console.warn(`网络错误: ${err.message}, 重试 (${attempt+1}/${retries})...`);
            }
            
            if (attempt < retries) {
                await new Promise(r => setTimeout(r, retryDelay));
                retryDelay *= 1.5;
            } else {
                throw err;
            }
        }
    }
}

/**
 * fetchJSON - 带超时重试的 JSON 请求
 * @returns {Promise<object>} 解析后的JSON
 */
async function fetchJSON(url, options = {}, timeout = 30000) {
    const resp = await fetchWithRetry(url, options, timeout);
    if (!resp.ok) {
        const text = await resp.text().catch(() => '');
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
    }
    return resp.json();
}
"""


def _save_cache(filename: str, data: Any) -> None:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        filepath = os.path.join(_CACHE_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        log.debug(f"缓存已保存: {filename}")
    except Exception as e:
        log.warning(f"缓存保存失败: {filename}, {e}")


def _load_cache(filename: str) -> Any:
    try:
        filepath = os.path.join(_CACHE_DIR, filename)
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            # V5.5: Filter ROE<0 stocks from all caches
            if isinstance(data, list):
                data = [r for r in data
                        if not (isinstance(r.get('roe'), (int, float)) and r.get('roe') < 0)]
            elif isinstance(data, dict):
                for key in ['stocks', 'results', 'morning', 'afternoon']:
                    sub = data.get(key)
                    if isinstance(sub, list):
                        data[key] = [r for r in sub
                                     if not (isinstance(r.get('roe'), (int, float)) and r.get('roe') < 0)]
                    elif isinstance(sub, dict) and 'results' in sub:
                        data[key]['results'] = [r for r in sub['results']
                                                if not (isinstance(r.get('roe'), (int, float)) and r.get('roe') < 0)]
            log.info(f"缓存已加载: {filename}")
            return data
    except Exception as e:
        log.warning(f"缓存加载失败: {filename}, {e}")
    return None


def _restore_all_caches() -> None:
    global DAILY_PICK_DATA, AUCTION_PICK_DATA, WP2_PICK_DATA, STRONG_PICK_DATA
    with _picker_write_lock:
        DAILY_PICK_DATA = _load_cache("daily_pick_raw_cache.json")
        AUCTION_PICK_DATA = _load_cache("auction_pick_cache.json")
        WP2_PICK_DATA = _load_cache("wp2_pick_cache.json")
        STRONG_PICK_DATA = _load_cache("strong_pick_cache.json")
    log.info("缓存恢复完成")


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

    # 公共JS: fetchWithRetry 统一封装
    @app.route("/api/js/common.js")
    def common_js():
        return Response(
            COMMON_JS,
            mimetype="application/javascript",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    # SSE推送 — 后台线程统一推送，请求线程仅订阅队列
    # ⚠️ SSE_MAX_CONNECTIONS 必须远小于工作线程数，否则SSE连接会耗尽所有线程
    # waitress 12线程 → SSE最多8个，留4个给普通请求
    # gunicorn 4workers×8threads=32线程 → SSE最多24个，留8个给普通请求
    _server_threads = int(os.environ.get("WAITRESS_THREADS", "12"))
    SSE_MAX_CONNECTIONS = max(1, _server_threads - 4)

    @app.route("/api/sse")
    def sse_stream():
        # 连接数限制
        if _sse_hub.connection_count() >= SSE_MAX_CONNECTIONS:
            return jsonify({"success": False, "error": "SSE连接数已满"}), 503

        q: queue.Queue = queue.Queue(maxsize=10)
        _sse_hub.subscribe(q)
        log.debug(f"SSE连接建立, 当前连接数: {_sse_hub.connection_count()}/{SSE_MAX_CONNECTIONS}")
        try:
            def generate():
                try:
                    # 初始连接事件
                    yield f"event: connected\ndata: {json.dumps({'status': 'ok'})}\n\n"
                    while True:
                        try:
                            msg = q.get(timeout=10)  # 10秒超时，更频繁心跳可更快检测断线
                            yield msg
                        except queue.Empty:
                            # 发送心跳，防止代理/浏览器超时断开
                            yield ": heartbeat\n\n"
                except (GeneratorExit, BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    _sse_hub.unsubscribe(q)
                    log.debug(f"SSE连接关闭, 当前连接数: {_sse_hub.connection_count()}")
            return Response(generate(), mimetype="text/event-stream")
        except Exception:
            _sse_hub.unsubscribe(q)
            raise

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
    _rate_last_cleanup = time.time()  # 上次全量清理时间

    def _cleanup_rate_store() -> None:
        """清理过期的 rate limit 条目，防止内存泄漏"""
        nonlocal _rate_last_cleanup
        now = time.time()
        # 每5分钟执行一次全量清理
        if now - _rate_last_cleanup < 300:
            return
        with _rate_lock:
            expired_keys = []
            for key, history in _rate_store.items():
                # 清理已过期的timestamp
                history[:] = [t for t in history if now - t < key[1]]
                # 如果列表为空，标记删除整个key
                if not history:
                    expired_keys.append(key)
            for key in expired_keys:
                del _rate_store[key]
            _rate_last_cleanup = now
            if expired_keys:
                log.debug(f"清理速率限制条目: {len(expired_keys)} 个空key")

    @app.before_request
    def check_rate_limit():
        # 定期清理
        _cleanup_rate_store()

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


def _run_auction_preselect_check() -> None:
    """每分钟检查是否到15:30，自动执行竞价预选"""
    now = _datetime.now()
    # 只在15:30触发
    if now.hour != 15 or now.minute != 30:
        return
    # 周末不执行
    if now.weekday() >= 5:
        return
    try:
        from routes.auction import auto_auction_preselect
        auto_auction_preselect()
        log.info("15:30自动竞价预选完成")
    except Exception as e:
        log.error(f"15:30自动竞价预选失败: {e}")


def _run_cache_cleanup() -> None:
    """每10分钟清理过期缓存，防止内存泄漏"""
    try:
        from modules.cache_manager import cache_manager
        removed = cache_manager.cleanup_expired()
        if removed > 0:
            log.debug(f"缓存清理: 移除 {removed} 条过期条目")
    except Exception as e:
        log.debug(f"缓存清理失败: {e}")


def _run_industry_cache_flush() -> None:
    """每2分钟将脏的行业缓存刷盘"""
    try:
        from modules.data_fetcher import _flush_industry_cache
        _flush_industry_cache()
    except Exception as e:
        log.debug(f"行业缓存刷盘失败: {e}")


def start_scheduler() -> None:
    """启动后台调度器 - 只在main中调用"""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    from modules.scheduler import Scheduler
    scheduler = Scheduler()

    def _run_auction_monitor():
        """V5 实盘监测任务（每日运行）"""
        try:
            from routes.auction import run_auction_monitor_job
            run_auction_monitor_job()
        except Exception as e:
            log.error(f"V5实盘监测任务调度失败: {e}")

    scheduler.add_job("daily_picker", run_daily_picker, interval_seconds=300)
    scheduler.add_job("auction_picker", run_auction_picker, interval_seconds=60)
    scheduler.add_job("wp2_picker", run_wp2_picker, interval_seconds=300)
    scheduler.add_job("strong_picker", run_strong_picker, interval_seconds=300)
    scheduler.add_job("auction_preselect", _run_auction_preselect_check, interval_seconds=60)
    scheduler.add_job("auction_monitor", _run_auction_monitor, interval_seconds=86400)
    scheduler.add_job("cache_cleanup", _run_cache_cleanup, interval_seconds=600)
    scheduler.add_job("industry_cache_flush", _run_industry_cache_flush, interval_seconds=120)
    scheduler.start_background()

    # 启动SSE推送中心
    _sse_hub.start()

    log.info("后台调度已启动")


# === 数据访问接口 ===

def get_picker_data() -> Optional[list[dict]]:
    with _picker_read_lock:
        return DAILY_PICK_DATA

def get_auction_data() -> Optional[list[dict]]:
    with _picker_read_lock:
        return AUCTION_PICK_DATA

def get_wp2_data() -> Optional[list[dict]]:
    with _picker_read_lock:
        return WP2_PICK_DATA

def get_strong_data() -> Optional[list[dict]]:
    with _picker_read_lock:
        return STRONG_PICK_DATA


def clear_strong_data() -> None:
    global STRONG_PICK_DATA
    with _picker_write_lock:
        STRONG_PICK_DATA = None


# === 选股执行函数 ===

def run_daily_picker() -> list[dict]:
    global DAILY_PICK_DATA
    try:
        from modules.stock_picker import run_picker
        result = run_picker()
        with _picker_write_lock:
            DAILY_PICK_DATA = result
        _save_cache("daily_pick_raw_cache.json", result)
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
        # 非竞价时段返回空列表时不覆盖已有缓存
        if not result and AUCTION_PICK_DATA:
            return AUCTION_PICK_DATA
        # 如果内存为空但缓存文件有数据，从文件恢复
        if not result and not AUCTION_PICK_DATA:
            cached = _load_cache("auction_pick_cache.json")
            if cached:
                AUCTION_PICK_DATA = cached
                return cached
        with _picker_write_lock:
            AUCTION_PICK_DATA = result
        # 保存为dict格式（兼容 routes/auction.py 的格式）
        save_data = {
            "date": _datetime.now().strftime('%Y-%m-%d'),
            "stocks": result,
            "pick_time": _datetime.now().strftime('%H:%M:%S'),
            "last_update": _datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "market_info": {},
            "candidate_pool": [],
        }
        _save_cache("auction_pick_cache.json", save_data)
        # 同步到 routes/auction 的 AUCTION_PICK_DATA
        try:
            from routes.auction import AUCTION_PICK_DATA as ROUTE_DATA
            ROUTE_DATA.update(save_data)
        except Exception:
            pass
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
        if not result and WP2_PICK_DATA:
            return WP2_PICK_DATA
        if not result and not WP2_PICK_DATA:
            cached = _load_cache("wp2_pick_cache.json")
            if cached:
                WP2_PICK_DATA = cached
                return cached
        with _picker_write_lock:
            WP2_PICK_DATA = result
        _save_cache("wp2_pick_cache.json", result)
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
        if not result and STRONG_PICK_DATA:
            return STRONG_PICK_DATA
        if not result and not STRONG_PICK_DATA:
            cached = _load_cache("strong_pick_cache.json")
            if cached:
                STRONG_PICK_DATA = cached
                return cached
        with _picker_write_lock:
            STRONG_PICK_DATA = result
        _save_cache("strong_pick_cache.json", result)
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


# === 进程保护 ===
# 标记进程是否正在正常运行，防止 daemon 线程触发 interpreter shutdown
_process_running = True


def _is_process_running() -> bool:
    """检查主进程是否仍在正常运行。
    daemon 线程在 interpreter shutdown 期间调用此函数可安全退出，
    避免 RuntimeError: cannot schedule new futures after interpreter shutdown"""
    return _process_running


def _graceful_shutdown() -> None:
    """优雅退出：标记进程停止，让 daemon 线程安全退出"""
    global _process_running
    _process_running = False
    log.info("进程正在优雅退出，daemon线程将安全停止")
    # 停止SSE推送中心
    _sse_hub._running = False


import atexit
atexit.register(_graceful_shutdown)


# === 入口 ===

app = create_app()

_restore_all_caches()

_PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".app.pid")


def _acquire_pid_lock() -> bool:
    """获取 PID 文件锁，防止多实例同时运行。

    如果端口已被占用，自动终止旧进程再启动。
    返回 True 表示可以安全启动。
    """
    port = _config.server.port

    # 检查端口是否已被其他进程占用
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("127.0.0.1", port))
            if result == 0:
                # 端口已被占用，尝试杀掉占用者
                log.warning(f"端口 {port} 已被占用，尝试终止占用进程...")
                import subprocess
                try:
                    # 用 netstat 找到占用端口的 PID 并杀掉
                    output = subprocess.check_output(
                        f'netstat -ano | findstr ":{port} " | findstr "LISTENING"',
                        shell=True, text=True, timeout=5
                    )
                    for line in output.strip().splitlines():
                        parts = line.split()
                        if parts:
                            pid = parts[-1]
                            log.info(f"终止占用端口的进程 PID={pid}")
                            subprocess.run(
                                ["taskkill", "/F", "/PID", pid],
                                capture_output=True, timeout=5
                            )
                    time.sleep(3)  # 等待端口释放
                except Exception as e:
                    log.warning(f"终止旧进程失败: {e}")
    except Exception as e:
        log.warning(f"端口检查异常: {e}")

    # 写入当前 PID
    try:
        with open(_PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        log.info(f"PID锁获取成功: PID={os.getpid()}, 端口={port}")
    except Exception as e:
        log.warning(f"PID文件写入失败: {e}")

    return True


def _release_pid_lock() -> None:
    """释放 PID 文件锁"""
    try:
        if os.path.exists(_PID_FILE):
            os.remove(_PID_FILE)
    except Exception:
        pass


if __name__ == "__main__":
    # 检查是否已有实例运行
    if not _acquire_pid_lock():
        log.error("无法获取实例锁，已有实例运行或端口被占用，退出")
        sys.exit(1)

    sys.modules["web_app"] = sys.modules["__main__"]
    start_scheduler()

    import signal
    def _handle_exit(signum, frame):
        """信号处理：SIGINT/SIGTERM 触发优雅退出"""
        log.info(f"收到退出信号 {signum}, 开始优雅退出")
        _graceful_shutdown()
        _release_pid_lock()
        sys.exit(0)

    # Windows 不支持 SIGTERM，仅注册 SIGINT
    try:
        signal.signal(signal.SIGINT, _handle_exit)
        signal.signal(signal.SIGTERM, _handle_exit)
    except (OSError, ValueError):
        # 某些环境（如 Windows 服务）可能不支持信号处理
        pass

    try:
        app.run(
            host=_config.server.host,
            port=_config.server.port,
            debug=_config.server.debug,
            threaded=True,  # 多线程模式，避免单线程阻塞
        )
    finally:
        _graceful_shutdown()
        _release_pid_lock()