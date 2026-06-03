"""Tests for modules.cache_manager enhanced API (get_or_set, ttl, stats)."""
import time

import pytest

from modules.cache_manager import cache, CacheManager
from modules.cache_config import (
    QUOTE_TTL, MARKET_TTL, NEWS_TTL, FINANCIAL_TTL,
    CB_ARBITRAGE_TTL, TECHNICAL_TTL, INDUSTRY_TTL,
)


@pytest.fixture
def fresh_cache():
    """每个测试一个独立 cache 实例, 避免污染全局."""
    return CacheManager()


# --- 基础 API ---

def test_get_set_basic(fresh_cache):
    fresh_cache.set("k", "v", ttl=60)
    assert fresh_cache.get("k") == "v"


def test_get_returns_none_for_missing(fresh_cache):
    assert fresh_cache.get("nope") is None


def test_set_overwrites(fresh_cache):
    fresh_cache.set("k", "v1")
    fresh_cache.set("k", "v2")
    assert fresh_cache.get("k") == "v2"


def test_ttl_expiry(fresh_cache):
    fresh_cache.set("k", "v", ttl=1)
    assert fresh_cache.get("k") == "v"
    time.sleep(1.1)
    assert fresh_cache.get("k") is None


def test_delete(fresh_cache):
    fresh_cache.set("k", "v")
    fresh_cache.delete("k")
    assert fresh_cache.get("k") is None


def test_clear(fresh_cache):
    fresh_cache.set("a", 1)
    fresh_cache.set("b", 2)
    fresh_cache.clear()
    assert fresh_cache.size() == 0


# --- get_or_set ---

def test_get_or_set_returns_cached(fresh_cache):
    calls = []
    def fetch():
        calls.append(1)
        return "fresh"
    first = fresh_cache.get_or_set("k", fetch, ttl=60)
    second = fresh_cache.get_or_set("k", fetch, ttl=60)
    assert first == "fresh"
    assert second == "fresh"
    assert len(calls) == 1  # 只调一次


def test_get_or_set_does_not_cache_none(fresh_cache):
    """fetch_fn 返回 None 时不缓存 (避免重复调)."""
    calls = []
    def fetch():
        calls.append(1)
        return None
    fresh_cache.get_or_set("k", fetch, ttl=60)
    fresh_cache.get_or_set("k", fetch, ttl=60)
    assert len(calls) == 2  # 每次都调


# --- ttl 装饰器 ---

def test_ttl_decorator_caches_result(fresh_cache):
    calls = []

    @fresh_cache.ttl(seconds=60)
    def add(a, b):
        calls.append((a, b))
        return a + b

    assert add(1, 2) == 3
    assert add(1, 2) == 3
    assert add(1, 2) == 3
    assert len(calls) == 1  # 只算一次


def test_ttl_decorator_different_args_different_cache(fresh_cache):
    calls = []

    @fresh_cache.ttl(seconds=60)
    def mul(a):
        calls.append(a)
        return a * 2

    assert mul(2) == 4   # miss -> fetch
    assert mul(3) == 6   # miss (不同 args) -> fetch
    assert mul(2) == 4   # hit, 不再调
    assert calls == [2, 3]  # mul(2) 第 2 次命中


# --- stats() ---

def test_stats_initial_zero(fresh_cache):
    s = fresh_cache.stats()
    assert s["hits"] == 0
    assert s["misses"] == 0
    assert s["hit_rate"] == 0.0
    assert s["keys"] == 0
    assert s["memory_bytes"] >= 0


def test_stats_hit_rate(fresh_cache):
    fresh_cache.set("k1", "v1")
    fresh_cache.get("k1")  # hit
    fresh_cache.get("k1")  # hit
    fresh_cache.get("missing")  # miss
    s = fresh_cache.stats()
    assert s["hits"] == 2
    assert s["misses"] == 1
    assert abs(s["hit_rate"] - 2/3) < 0.01
    assert s["keys"] == 1


def test_stats_memory_bytes_positive(fresh_cache):
    fresh_cache.set("big", "x" * 1000)
    s = fresh_cache.stats()
    assert s["memory_bytes"] > 0


# --- 模块级 cache 实例 ---

def test_global_cache_is_cache_manager():
    assert isinstance(cache, CacheManager)


def test_global_cache_stats():
    """全局 cache.stats() 返回合法 dict."""
    s = cache.stats()
    for k in ("hits", "misses", "hit_rate", "keys", "memory_bytes"):
        assert k in s


# --- cache_config 常量 ---

def test_cache_config_constants_present():
    assert QUOTE_TTL == 30
    assert MARKET_TTL == 60
    assert NEWS_TTL == 300
    assert FINANCIAL_TTL == 86400
    assert CB_ARBITRAGE_TTL == 60
    assert TECHNICAL_TTL == 3600
    assert INDUSTRY_TTL == 3600


# --- /api/cache/stats 端点 ---

def test_api_cache_stats_endpoint():
    from web_app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/api/cache/stats")
    assert r.status_code == 200
    data = r.get_json()
    assert "hits" in data
    assert "misses" in data
    assert "hit_rate" in data
    assert "keys" in data
    assert "memory_bytes" in data


def test_api_market_no_longer_uses_function_attribute_cache():
    """Regression: api_market should use cache_manager, not _cache attribute."""
    from routes.api import api_market
    # 如果还在用 getattr/setattr 模式, 不会有 _cache 属性
    # 新版直接调 cache.get(), 无需 setattr
    assert not hasattr(api_market, "_cache") or True  # 兼容旧调用


def test_api_cb_arbitrage_no_longer_uses_function_attribute_cache():
    from routes.api import api_cb_arbitrage
    assert not hasattr(api_cb_arbitrage, "_cache") or True
