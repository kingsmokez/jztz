"""缓存管理单元测试"""

import time
import pytest
from modules.cache_manager import CacheManager


class TestCacheManager:
    def test_set_and_get(self):
        c = CacheManager()
        c.set("key1", "value1", ttl=60)
        assert c.get("key1") == "value1"

    def test_expired(self):
        c = CacheManager()
        c.set("key1", "value1", ttl=1)
        time.sleep(1.1)
        assert c.get("key1") is None

    def test_delete(self):
        c = CacheManager()
        c.set("key1", "value1", ttl=60)
        c.delete("key1")
        assert c.get("key1") is None

    def test_clear(self):
        c = CacheManager()
        c.set("a", 1, ttl=60)
        c.set("b", 2, ttl=60)
        c.clear()
        assert c.size() == 0

    def test_cleanup_expired(self):
        c = CacheManager()
        c.set("expired", "val", ttl=1)
        c.set("valid", "val", ttl=60)
        time.sleep(1.1)
        removed = c.cleanup_expired()
        assert removed == 1
        assert c.get("valid") == "val"
