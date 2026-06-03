"""modules/rate_config.py 单元测试

覆盖最长前缀匹配、静态资源放行、默认限流回退。
"""

from modules.rate_config import (
    DEFAULT_LIMIT,
    EXEMPT_PATHS,
    RateLimit,
    get_limit_for_path,
)


class TestGetLimitForPath:
    def test_static_resources_exempt(self):
        for path in ["/static/js/app.js", "/favicon.ico", "/healthz"]:
            assert get_limit_for_path(path) is None

    def test_sse_high_limit(self):
        limit = get_limit_for_path("/api/sse")
        assert limit is not None
        # SSE 配置的"实际不限流"值
        assert limit.max_requests >= 1000

    def test_backtest_5_per_hour(self):
        limit = get_limit_for_path("/api/backtest/run")
        assert limit is not None
        assert limit.max_requests == 5
        assert limit.window_seconds == 3600

    def test_auth_10_per_minute(self):
        limit = get_limit_for_path("/api/auth/login")
        assert limit is not None
        assert limit.max_requests == 10
        assert limit.window_seconds == 60

    def test_search_30_per_minute(self):
        limit = get_limit_for_path("/api/search")
        assert limit is not None
        assert limit.max_requests == 30

    def test_quote_default_60_per_minute(self):
        limit = get_limit_for_path("/api/quote")
        assert limit is not None
        assert limit.max_requests == 60
        assert limit.window_seconds == 60

    def test_unknown_path_falls_back_to_default(self):
        limit = get_limit_for_path("/totally/unknown")
        assert limit == DEFAULT_LIMIT

    def test_longest_prefix_wins(self):
        # /api/backtest 应优先于 /api/ 默认
        backtest = get_limit_for_path("/api/backtest")
        api = get_limit_for_path("/api/quote")
        assert backtest is not None and api is not None
        assert backtest.max_requests < api.max_requests


class TestRateLimitDataclass:
    def test_immutable(self):
        rl = RateLimit(max_requests=10, window_seconds=60)
        with __import__("pytest").raises(Exception):
            rl.max_requests = 20  # type: ignore[misc]

    def test_equality(self):
        a = RateLimit(max_requests=10, window_seconds=60)
        b = RateLimit(max_requests=10, window_seconds=60)
        assert a == b
