"""缓存管理模块 — 线程安全 TTL 缓存 + 命中率统计 + get_or_set"""
from __future__ import annotations

import functools
import sys
import threading
import time
from typing import Any, Callable, Optional

from modules.logger import log


def _key_prefix(key: str) -> str:
    """Return the prefix before the first ':' in ``key`` for low-cardinality metrics."""
    return key.split(":", 1)[0] if ":" in key else key


class CacheManager:
    """线程安全的 TTL 缓存, 支持命中率统计与 get_or_set 模式"""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._store:
                value, expire_at = self._store[key]
                if time.time() < expire_at:
                    self._hits += 1
                    self._emit_metric(hit=True, key=key)
                    return value
                del self._store[key]
        self._misses += 1
        self._emit_metric(hit=False, key=key)
        return None

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        with self._lock:
            self._store[key] = (value, time.time() + ttl)
        self._emit_size()

    def get_or_set(self, key: str, fetch_fn: Callable[[], Any], ttl: int = 300) -> Any:
        """缓存优先; miss 时调 fetch_fn() 并写入"""
        cached = self.get(key)
        if cached is not None:
            return cached
        value = fetch_fn()
        if value is not None:
            self.set(key, value, ttl=ttl)
        return value

    def ttl(self, seconds: int = 60) -> Callable:
        """装饰器: 按 func_name + args 自动构建 cache key, 命中后短路返回."""
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                key = f"{func.__module__}.{func.__name__}:{args!r}:{kwargs!r}"
                return self.get_or_set(key, lambda: func(*args, **kwargs), ttl=seconds)
            return wrapper
        return decorator

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)
        self._emit_size()

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            # 不清空 hits/misses — 用于长期监控
        self._emit_size()

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now >= exp]
            for k in expired:
                del self._store[k]
        if expired:
            log.debug(f"清理过期缓存: {len(expired)} 项")
            self._emit_size()
        return len(expired)

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def memory_bytes(self) -> int:
        """估算内存占用 (字节). key 长度 + value 浅 sizeof."""
        total = 0
        with self._lock:
            for k, (v, _) in self._store.items():
                total += sys.getsizeof(k) + sys.getsizeof(v)
        return total

    def stats(self) -> dict[str, Any]:
        """命中率统计 + 键数 + 内存估算"""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total > 0 else 0.0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 4),
                "keys": len(self._store),
                "memory_bytes": self.memory_bytes(),
            }

    # ------------------------------------------------------------------
    # Prometheus emission — wrapped in try/except so tests / no-prom
    # environments (e.g. minimal CI step) still work.
    # ------------------------------------------------------------------
    def _emit_metric(self, hit: bool, key: str) -> None:
        try:
            from modules.metrics import record_cache
            record_cache(_key_prefix(key), hit)
        except Exception:
            pass

    def _emit_size(self) -> None:
        try:
            from modules.metrics import CACHE_SIZE
            CACHE_SIZE.set(self.size())
        except Exception:
            pass


cache = CacheManager()
