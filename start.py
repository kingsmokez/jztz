"""生产环境启动脚本 - 使用waitress (Windows兼容)"""

import sys
sys.path.insert(0, ".")

from web_app import app, start_scheduler, _acquire_pid_lock, _release_pid_lock, _graceful_shutdown

# 检查是否已有实例运行
if not _acquire_pid_lock():
    print("错误: 无法获取实例锁，已有实例运行或端口被占用，退出")
    sys.exit(1)

start_scheduler()

try:
    from waitress import serve
    from modules.config import load_config
    cfg = load_config()
    print(f"生产模式启动: {cfg.server.host}:{cfg.server.port}")
    serve(
        app,
        host=cfg.server.host,
        port=cfg.server.port,
        threads=12,               # 工作线程数（原4，提升以支持更多并发）
        channel_timeout=120,      # 通道超时120秒（原600，降低以更快回收断线连接）
        request_timeout=120,      # 请求超时120秒（无默认值，不设置则挂起请求永不释放线程）
        cleanup_interval=30,      # 清理间隔30秒
        connection_limit=500,     # 最大连接数（原100，提升以支持更多客户端）
        max_request_body_size=0,  # 不限制请求体大小
    )
except ImportError:
    print("waitress未安装，使用Flask开发服务器")
    app.run(host="0.0.0.0", port=5559, threaded=True)
finally:
    _graceful_shutdown()
    _release_pid_lock()
