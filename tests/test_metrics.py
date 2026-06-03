"""Tests for the Prometheus /api/metrics endpoint and the in-process
metrics registry.  No real Prometheus server required — we scrape
the text output and parse it back to a dict.
"""
from __future__ import annotations

import re
import time
from typing import Dict

import pytest

from modules.metrics import (
    CACHE_HITS,
    CACHE_MISSES,
    CACHE_SIZE,
    CIRCUIT_STATE,
    EXTERNAL_API_LATENCY,
    HTTP_REQUESTS,
    PICKER_RUNS,
    REGISTRY,
    record_cache,
    record_circuit_state,
    generate_latest,
    key_prefix,
)


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Wipe the global registry between tests so counters don't leak."""
    REGISTRY.reset()
    yield
    REGISTRY.reset()


def _parse_metrics(text: str) -> Dict[str, float]:
    """Parse a Prometheus text-format dump into {sample: value}.

    Supports counter, gauge, and histogram bucket/count/sum lines.
    Histogram bucket lines end in ``_bucket{...}``; ``_sum`` and
    ``_count`` are exposed as separate samples.
    """
    out: Dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        # "name{labels}  value"  OR  "name value"
        m = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([0-9eE.+\-]+)$", line)
        if not m:
            continue
        name = m.group(1)
        try:
            value = float(m.group(3))
        except ValueError:
            continue
        out[name] = value
    return out


# ---------------------------------------------------------------------------
# Unit tests — registry + helpers
# ---------------------------------------------------------------------------
class TestRegistry:
    def test_counter_inc_default(self):
        CACHE_HITS.labels(key_prefix="quotes").inc()
        text = generate_latest().decode()
        assert 'jztz_cache_hits_total{key_prefix="quotes"} 1' in text

    def test_counter_inc_amount(self):
        CACHE_HITS.labels(key_prefix="quotes").inc(3)
        text = generate_latest().decode()
        assert 'jztz_cache_hits_total{key_prefix="quotes"} 3' in text

    def test_counter_cannot_decrease(self):
        c = CACHE_HITS.labels(key_prefix="quotes")
        with pytest.raises(ValueError):
            c.inc(-1)

    def test_gauge_set_inc_dec(self):
        g = CACHE_SIZE
        g.set(10)
        assert generate_latest().decode().count("jztz_cache_entries 10") == 1
        g.inc(5)
        g.dec(2)
        text = generate_latest().decode()
        assert "jztz_cache_entries 13" in text

    def test_histogram_observe(self):
        h = EXTERNAL_API_LATENCY
        h.observe(0.1, api="eastmoney", endpoint="quote")
        h.observe(0.3, api="eastmoney", endpoint="quote")
        text = generate_latest().decode()
        # cumulative bucket counts
        assert 'jztz_external_api_latency_seconds_bucket{api="eastmoney",endpoint="quote",le="0.25"} 1' in text
        assert 'jztz_external_api_latency_seconds_bucket{api="eastmoney",endpoint="quote",le="0.5"} 2' in text
        assert 'jztz_external_api_latency_seconds_bucket{api="eastmoney",endpoint="quote",le="+Inf"} 2' in text
        # sum + count
        assert 'jztz_external_api_latency_seconds_sum{api="eastmoney",endpoint="quote"} 0.4' in text
        assert 'jztz_external_api_latency_seconds_count{api="eastmoney",endpoint="quote"} 2' in text

    def test_label_mismatch_raises(self):
        with pytest.raises(ValueError):
            CACHE_HITS.labels(wrong="label")

    def test_registry_reset_clears_values(self):
        CACHE_HITS.labels(key_prefix="quotes").inc(5)
        REGISTRY.reset()
        text = generate_latest().decode()
        # After reset, the line should not appear (no children).
        assert 'jztz_cache_hits_total{key_prefix="quotes"}' not in text


class TestHelpers:
    def test_record_cache_hit(self):
        record_cache("quotes", hit=True)
        text = generate_latest().decode()
        assert 'jztz_cache_hits_total{key_prefix="quotes"} 1' in text
        assert 'jztz_cache_misses_total{key_prefix="quotes"}' not in text

    def test_record_cache_miss(self):
        record_cache("quotes", hit=False)
        text = generate_latest().decode()
        assert 'jztz_cache_misses_total{key_prefix="quotes"} 1' in text

    def test_record_circuit_state(self):
        record_circuit_state("eastmoney", "CLOSED")
        record_circuit_state("akshare", "OPEN")
        text = generate_latest().decode()
        assert 'jztz_circuit_breaker_state{api="eastmoney"} 0' in text
        assert 'jztz_circuit_breaker_state{api="akshare"} 2' in text

    def test_record_circuit_trip(self):
        record_circuit_state("eastmoney", "OPEN", just_tripped=True)
        text = generate_latest().decode()
        assert 'jztz_circuit_breaker_trips_total{api="eastmoney"} 1' in text

    def test_key_prefix(self):
        assert key_prefix("quotes:all") == "quotes"
        assert key_prefix("fundamental:000001.SZ") == "fundamental"
        assert key_prefix("noColon") == "noColon"


# ---------------------------------------------------------------------------
# Integration — module interactions
# ---------------------------------------------------------------------------
class TestCacheManagerEmitsMetrics:
    def _fresh_cache(self):
        from modules.cache_manager import cache
        cache.clear()
        return cache

    def test_get_hit_emits_hit(self):
        cache = self._fresh_cache()
        cache.set("quotes:foo", 123, ttl=60)
        cache.get("quotes:foo")
        text = generate_latest().decode()
        assert 'jztz_cache_hits_total{key_prefix="quotes"} 1' in text
        assert "jztz_cache_entries 1" in text

    def test_get_miss_emits_miss(self):
        cache = self._fresh_cache()
        cache.get("quotes:missing")
        text = generate_latest().decode()
        assert 'jztz_cache_misses_total{key_prefix="quotes"} 1' in text

    def test_set_emits_size_gauge(self):
        cache = self._fresh_cache()
        cache.set("k1", 1, ttl=60)
        cache.set("k2", 2, ttl=60)
        text = generate_latest().decode()
        assert "jztz_cache_entries 2" in text


class TestCircuitBreakerEmitsMetrics:
    def test_get_breaker_emits_closed_state(self):
        from modules.circuit_breaker import get_breaker
        get_breaker("test_breaker")
        text = generate_latest().decode()
        assert 'jztz_circuit_breaker_state{api="test_breaker"} 0' in text

    def test_trip_emits_open_and_trip_counter(self):
        from modules.circuit_breaker import (
            CircuitBreakerConfig,
            get_breaker,
        )
        cb = get_breaker("trip_test", CircuitBreakerConfig(failure_threshold=2))
        cb.record_failure()
        cb.record_failure()  # threshold reached -> OPEN
        text = generate_latest().decode()
        assert 'jztz_circuit_breaker_state{api="trip_test"} 2' in text
        assert 'jztz_circuit_breaker_trips_total{api="trip_test"} 1' in text

    def test_recovery_resets_trips(self):
        from modules.circuit_breaker import get_breaker
        cb = get_breaker("recover_test")
        cb.record_failure()  # not a trip — threshold=5 default
        cb.record_success()
        text = generate_latest().decode()
        assert 'jztz_circuit_breaker_state{api="recover_test"} 0' in text
        # No trip recorded
        assert 'jztz_circuit_breaker_trips_total{api="recover_test"}' not in text


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------
class TestMetricsEndpoint:
    def test_metrics_returns_200(self):
        from web_app import create_app

        app = create_app()
        client = app.test_client()
        r = client.get("/api/metrics")
        assert r.status_code == 200

    def test_metrics_content_type(self):
        from web_app import create_app

        app = create_app()
        client = app.test_client()
        r = client.get("/api/metrics")
        ct = r.headers.get("Content-Type", "")
        assert ct.startswith("text/plain")
        assert "version=0.0.4" in ct

    def test_metrics_body_has_help_and_type_lines(self):
        from web_app import create_app

        app = create_app()
        client = app.test_client()
        r = client.get("/api/metrics")
        text = r.data.decode("utf-8")
        assert "# HELP jztz_cache_hits_total" in text
        assert "# TYPE jztz_cache_hits_total counter" in text
        assert "# HELP jztz_cache_entries" in text
        assert "# TYPE jztz_cache_entries gauge" in text

    def test_request_to_endpoint_increments_http_counter(self):
        from web_app import create_app

        app = create_app()
        client = app.test_client()
        # Two separate requests, then read the body of a third.
        # (The body of request N is rendered BEFORE request N's
        # after_request hook runs, so the response body never reflects
        # the increment caused by the request that produced it.)
        client.get("/api/metrics")
        client.get("/api/metrics")
        text = client.get("/api/metrics").data.decode("utf-8")
        assert re.search(
            r'jztz_http_requests_total\{[^}]*endpoint="/api/metrics"[^}]*\}\s+2',
            text,
        )

    def test_histogram_in_endpoint(self):
        from web_app import create_app
        from modules.metrics import EXTERNAL_API_LATENCY

        app = create_app()
        client = app.test_client()
        EXTERNAL_API_LATENCY.observe(0.07, api="eastmoney", endpoint="q")
        text = client.get("/api/metrics").data.decode("utf-8")
        # le=0.05 should be 0, le=0.1 should be 1
        assert re.search(
            r'jztz_external_api_latency_seconds_bucket\{api="eastmoney",endpoint="q",le="0.05"\}\s+0',
            text,
        )
        assert re.search(
            r'jztz_external_api_latency_seconds_bucket\{api="eastmoney",endpoint="q",le="0.1"\}\s+1',
            text,
        )
