"""生产环境启动脚本 - 使用waitress (Windows兼容)"""

import sys
sys.path.insert(0, ".")

from web_app import app, start_scheduler

start_scheduler()

try:
    from waitress import serve
    from modules.config import load_config
    cfg = load_config()
    print(f"生产模式启动: {cfg.server.host}:{cfg.server.port}")
    serve(app, host=cfg.server.host, port=cfg.server.port)
except ImportError:
    print("waitress未安装，使用Flask开发服务器")
    app.run(host="0.0.0.0", port=5559)
