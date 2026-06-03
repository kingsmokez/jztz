"""统一外部 API 出口 — requests.get 包装, 自动应用熔断器 (按 host 维度)."""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

import requests

from modules.logger import log
from modules.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitOpenError,
    get_breaker,
)


# 默认熔断策略: 5 次连续失败 -> OPEN 30s
DEFAULT_CONFIG = CircuitBreakerConfig(failure_threshold=5, open_timeout_seconds=30.0)


def _host_of(url: str) -> str:
    try:
        return urlparse(url).netloc or "unknown"
    except Exception:
        return "unknown"


def safe_get(url: str, timeout: float = 10.0, **kwargs: Any) -> Optional[requests.Response]:
    """带熔断的 GET. OPEN 时返回 None (调用方按需降级)."""
    breaker = get_breaker(_host_of(url), DEFAULT_CONFIG)
    try:
        return breaker.call(lambda: requests.get(url, timeout=timeout, **kwargs))
    except CircuitOpenError as e:
        log.warning(f"熔断 OPEN, 跳过外部 API: {e}")
        return None
    except requests.RequestException as e:
        log.error(f"外部 API 失败: {url}, {e}")
        return None
    except Exception as e:
        log.error(f"外部 API 未知异常: {url}, {e}")
        return None


def safe_get_json(url: str, timeout: float = 10.0, **kwargs: Any) -> Optional[Any]:
    """带熔断的 GET + JSON 解析. 失败/熔断均返回 None."""
    resp = safe_get(url, timeout=timeout, **kwargs)
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError as e:
        log.error(f"外部 API JSON 解析失败: {url}, {e}")
        return None
