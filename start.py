"""生产环境启动脚本 - 使用waitress (Windows兼容)"""

import sys
sys.path.insert(0, ".")

from web_app import app, start_scheduler, _acquire_pid_lock, _release_pid_lock, _graceful_shutdown

# 检查是否已有实例运行
if not _acquire_pid_lock():
    print("错误: 无法获取实例锁，已有实例运行或端口被占用，退出")
    sys.exit(1)

# 异步预选股：后台线程选股，不阻塞服务启动
import threading
print("[预选股] 启动后台选股线程...", flush=True)
from web_app import run_auction_picker, run_strong_picker, run_wp2_picker, run_daily_picker
def _bg_preserve():
    import sys, time
    # 跳过竞价选股(仅交易日开盘前有效)，优先执行强势和每日选股
    for name, func in [("强势", run_strong_picker), ("每日", run_daily_picker), ("WP2", run_wp2_picker), ("竞价", run_auction_picker)]:
        try:
            sys.stderr.write(f"[预选股] {name}选股中...\n")
            sys.stderr.flush()
            func()
            sys.stderr.write(f"[预选股] {name}选股完成\n")
            sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f"[预选股] {name}选股失败: {e}\n")
            sys.stderr.flush()
    sys.stderr.write("[预选股] 后台选股全部完成\n")
    sys.stderr.flush()
_t = threading.Thread(target=_bg_preserve, daemon=True, name="preserve_pick")
_t.start()
print("[预选股] 后台选股已启动，立即启动web服务", flush=True)

# 预选股完成后再启动调度器（后台定时刷新）
start_scheduler()

try:
    from waitress import serve
    from modules.config import load_config
    cfg = load_config()
    login_url = f"http://localhost:{cfg.server.port}/"
    print(f"生产模式启动: {cfg.server.host}:{cfg.server.port}")
    print("=" * 50)
    print(f"OK 服务已启动，登录地址: {login_url}")
    print("=" * 50)
    serve(
        app,
        host=cfg.server.host,
        port=cfg.server.port,
        threads=12,
        channel_timeout=120,
        cleanup_interval=30,
        connection_limit=500,
    )
except ImportError:
    print("waitress未安装，使用Flask开发服务器")
    print("=" * 50)
    print(f"OK 服务已启动，登录地址: http://localhost:{cfg.server.port}/")
    print("=" * 50)
    app.run(host=cfg.server.host, port=cfg.server.port, threaded=True)
finally:
    _graceful_shutdown()
    _release_pid_lock()
