"""蓝图注册包"""

from routes.daily import daily_bp
from routes.auction import auction_bp
from routes.wp2 import wp2_bp
from routes.strong import strong_bp
from routes.ai import ai_bp
from routes.api import api_bp
from routes.health import health_bp
from routes.metrics import metrics_bp
from routes.docs import docs_bp
from routes.export import export_bp
from routes.portfolio import portfolio_bp
from routes.backtest import backtest_bp
from routes.auth import auth_bp

ALL_BLUEPRINTS = [
    daily_bp,
    auction_bp,
    wp2_bp,
    strong_bp,
    ai_bp,
    api_bp,
    health_bp,
    metrics_bp,
    docs_bp,
    export_bp,
    portfolio_bp,
    backtest_bp,
    auth_bp,
]
