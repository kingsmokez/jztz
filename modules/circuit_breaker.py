"""熔断器 — CLOSED/OPEN/HALF_OPEN 状态机, 用于保护外部 API 调用.

状态转换:
  CLOSED   -- 正常放行, 连续失败 N 次 -> OPEN
  OPEN     -- 拒绝放行, 持续 T 秒后 -> HALF_OPEN
  HALF_OPEN -- 放行 1 个探测请求
    - 探测成功 -> CLOSED
    - 探测失败 -> OPEN
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

from modules.logger import log


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int = 5         # 连续失败 N 次触发 OPEN
    open_timeout_seconds: float = 30.0  # OPEN 持续 T 秒后转 HALF_OPEN
    half_open_max_trials: int = 1       # HALF_OPEN 探测并发数


class CircuitOpenError(Exception):
    """熔断器 OPEN 时被调用者抛出."""


class CircuitBreaker:
    """单上游熔断器. 线程安全."""

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: Optional[float] = None
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN and self._opened_at is not None:
                if time.time() - self._opened_at >= self.config.open_timeout_seconds:
                    self._state = CircuitState.HALF_OPEN
                    log.info(f"[CB:{self.name}] OPEN -> HALF_OPEN (timeout expired)")
            return self._state

    def allow(self) -> bool:
        """当前是否允许发起请求."""
        s = self.state
        return s in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                log.info(f"[CB:{self.name}] HALF_OPEN -> CLOSED (probe success)")
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._opened_at = None
        _emit_state(self.name, self._state)

    def record_failure(self) -> None:
        just_tripped = False
        with self._lock:
            self._failure_count += 1
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.time()
                just_tripped = True
                log.warning(f"[CB:{self.name}] HALF_OPEN -> OPEN (probe failed)")
            elif self._failure_count >= self.config.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.time()
                just_tripped = True
                log.warning(
                    f"[CB:{self.name}] CLOSED -> OPEN (failures={self._failure_count})"
                )
        _emit_state(self.name, self._state, just_tripped=just_tripped)

    def call(self, func: Callable[[], Any]) -> Any:
        """通过熔断器执行 func. OPEN 时抛 CircuitOpenError."""
        if not self.allow():
            raise CircuitOpenError(f"Circuit '{self.name}' is OPEN")
        try:
            result = func()
        except Exception:
            self.record_failure()
            raise
        else:
            self.record_success()
            return result

    def force_open(self) -> None:
        """手动触发 OPEN (用于测试/运维)."""
        with self._lock:
            self._state = CircuitState.OPEN
            self._opened_at = time.time()

    def force_close(self) -> None:
        """手动重置 (用于测试/运维)."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._opened_at = None


# 全局熔断器注册表 (按 upstream name 索引)
_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def _emit_state(api: str, state: CircuitState, just_tripped: bool = False) -> None:
    """Push the circuit state to Prometheus. Never raises."""
    try:
        from modules.metrics import record_circuit_state
        record_circuit_state(api, state.value, just_tripped=just_tripped)
    except Exception:
        pass


def get_breaker(name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
    """获取或创建指定 name 的熔断器单例."""
    with _breakers_lock:
        if name not in _breakers:
            _breakers[name] = CircuitBreaker(name, config)
        b = _breakers[name]
    # Emit current state on lookup so the gauge stays fresh even
    # when the breaker has never tripped.
    _emit_state(name, b.state)
    return b


def get_all_states() -> dict[str, str]:
    """所有熔断器当前状态, 用于 /api/health."""
    with _breakers_lock:
        return {n: b.state.value for n, b in _breakers.items()}


def reset_all() -> None:
    """清空所有熔断器 (测试/运维用)."""
    with _breakers_lock:
        _breakers.clear()
