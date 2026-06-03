"""统一配置管理 - 所有配置集中管理，支持环境变量覆盖"""
import warnings

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServerConfig:
    port: int = int(os.environ.get("APP_PORT", "5559"))
    host: str = os.environ.get("APP_HOST", "0.0.0.0")
    debug: bool = os.environ.get("APP_DEBUG", "false").lower() == "true"
    secret_key: str = os.environ.get("APP_SECRET_KEY", "change-me-in-production")

    def __post_init__(self):
        if self.secret_key == "change-me-in-production":
            warnings.warn(
                "APP_SECRET_KEY not set; using insecure default. "
                "Set APP_SECRET_KEY env var in production."
            )

@dataclass(frozen=True)
class DataConfig:
    tencent_api_url: str = "https://qt.gtimg.cn/q="
    datacenter_url: str = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    smartbox_url: str = "https://smartbox.gtimg.cn/s3/?q={keyword}&t=all&v={token}"
    smartbox_token: str = os.environ.get("SMARTBOX_TOKEN", "")

    timeout: int = int(os.environ.get("DATA_TIMEOUT", "10"))
    calibrate_threads: int = int(os.environ.get("CALIBRATE_THREADS", "30"))
    tech_threads: int = int(os.environ.get("TECH_THREADS", "15"))

    # 缓存TTL (秒)
    realtime_cache_ttl: int = 60
    financial_cache_ttl: int = 3600
    technical_cache_ttl: int = 1800
    industry_cache_ttl: int = 86400


@dataclass(frozen=True)
class ScoringConfig:
    min_market_cap: float = 30.0  # 亿元
    max_pe_negative: float = 0
    max_pe_high: float = 200
    min_turnover_rate: float = 0.5  # %
    score_short_circuit_threshold: float = 40.0  # 快速评分低于此值直接跳过详细评分


@dataclass(frozen=True)
class SecurityConfig:
    rate_limit_default: str = os.environ.get("RATE_LIMIT", "60/minute")
    rate_limit_api: str = os.environ.get("RATE_LIMIT_API", "30/minute")
    rate_limit_expensive: str = os.environ.get("RATE_LIMIT_EXPENSIVE", "5/minute")
    api_key: str = os.environ.get("API_KEY", "")
    ssl_skip_domains: tuple = ()


@dataclass(frozen=True)
class NotifyConfig:
    wecom_webhook_url: str = os.environ.get("WECOM_WEBHOOK_URL", "")


@dataclass(frozen=True)
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    data: DataConfig = field(default_factory=DataConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)


def load_config() -> Config:
    """加载配置，优先从环境变量"""
    return Config()


# === 常量 ===
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DAILY_PICK_FILE = os.path.join(BASE_DIR, 'daily_pick_cache.json')
AUCTION_PICK_FILE = os.path.join(BASE_DIR, 'auction_pick_cache.json')
WP2_PICK_FILE = os.path.join(BASE_DIR, 'wp2_pick_cache.json')

LIQUOR_NAMES = [
    "贵州茅台", "五粮液", "洋河股份", "泸州老窖", "山西汾酒",
    "酒鬼酒", "水井坊", "古井贡酒", "迎驾贡酒", "今世缘",
    "舍得酒业", "老白干酒", "伊力特", "口子窖", "金徽酒",
    "皇台酒业", "岩石股份", "顺鑫农业",
]

BANK_CODES = [
    "601398", "601288", "600000", "600036", "601166",
    "600015", "600016", "601328", "600919", "600028",
    "601939", "601988", "601318", "600030",
]
