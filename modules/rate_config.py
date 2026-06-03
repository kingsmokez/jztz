"""分级速率限制配置

按 endpoint 路径前缀差异化限流：
- SSE 长连接：不限流
- 行情/quote：60/min
- 搜索/search：30/min
- 回测/backtest：5/h
- 认证/auth：10/min
- 默认：60/min

设计为 path 前缀匹配而非 endpoint 名匹配 — 避免蓝图名变化时维护困难。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RateLimit:
    """单个限流策略

    Attributes:
        max_requests: 时间窗口内最大请求数
        window_seconds: 时间窗口（秒）
    """

    max_requests: int
    window_seconds: int


# ===== 限流策略字典 =====
# key: URL 路径前缀（最长匹配，命中即返回）
# 顺序敏感：更具体的前缀在前
RATE_LIMITS: list[tuple[str, RateLimit]] = [
    # SSE 长连接 — 不限流（已通过 EXEMPT_PATHS 处理）
    ("/api/backtest", RateLimit(max_requests=5, window_seconds=3600)),       # 5/h
    ("/api/auth", RateLimit(max_requests=10, window_seconds=60)),            # 10/min
    ("/api/search", RateLimit(max_requests=30, window_seconds=60)),         # 30/min
    ("/api/sse", RateLimit(max_requests=10_000, window_seconds=60)),        # 实际不限流
    ("/api/", RateLimit(max_requests=60, window_seconds=60)),               # 60/min 默认
    ("/api", RateLimit(max_requests=60, window_seconds=60)),
]

# 静态资源完全跳过限流
EXEMPT_PATHS: tuple[str, ...] = ("/static/", "/favicon.ico", "/healthz")

# 默认限流
DEFAULT_LIMIT = RateLimit(max_requests=60, window_seconds=60)


def get_limit_for_path(path: str) -> Optional[RateLimit]:
    """根据请求路径查找限流策略

    Returns:
        RateLimit 实例；如果路径在 EXEMPT_PATHS 中返回 None（不限流）
    """
    # 静态资源直接放行
    for prefix in EXEMPT_PATHS:
        if path.startswith(prefix) or path == prefix.rstrip("/"):
            return None

    # 找最长匹配前缀
    matched: Optional[RateLimit] = None
    matched_len = 0
    for prefix, limit in RATE_LIMITS:
        if path.startswith(prefix) and len(prefix) > matched_len:
            matched = limit
            matched_len = len(prefix)

    return matched or DEFAULT_LIMIT


__all__ = [
    "RateLimit",
    "RATE_LIMITS",
    "EXEMPT_PATHS",
    "DEFAULT_LIMIT",
    "get_limit_for_path",
]
