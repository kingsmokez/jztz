"""Tests for modules.circuit_breaker state machine + external_api wrapper."""
import time
from unittest.mock import patch, MagicMock

import pytest

from modules.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    CircuitState,
    get_breaker,
    get_all_states,
    reset_all,
)
import modules.external_api as external_api


@pytest.fixture(autouse=True)
def _reset_breakers():
    """每个测试前清空全局熔断器注册表."""
    reset_all()
    yield
    reset_all()


# --- CircuitBreaker 单元测试 ---

def test_initial_state_closed():
    cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3, open_timeout_seconds=0.1))
    assert cb.state == CircuitState.CLOSED
    assert cb.allow() is True


def test_closed_to_open_after_threshold():
    cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3, open_timeout_seconds=10))
    for _ in range(3):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow() is False


def test_open_to_half_open_after_timeout():
    cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=2, open_timeout_seconds=0.1))
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.15)
    # 访问 state 触发状态转换
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allow() is True


def test_half_open_to_closed_on_success():
    cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=2, open_timeout_seconds=0.05))
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.08)
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_half_open_to_open_on_failure():
    cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=2, open_timeout_seconds=0.05))
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.08)
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_success_resets_failure_count():
    cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3, open_timeout_seconds=10))
    cb.record_failure()
    cb.record_failure()
    cb.record_success()  # 重置
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED  # 只 1 次失败, 未达阈值


def test_call_invokes_function():
    cb = CircuitBreaker("test", CircuitBreakerConfig())
    called = []
    result = cb.call(lambda: called.append(1) or "ok")
    assert result == "ok"
    assert called == [1]


def test_call_raises_when_open():
    cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=1, open_timeout_seconds=10))
    cb.record_failure()
    with pytest.raises(CircuitOpenError):
        cb.call(lambda: "should not run")


def test_call_records_failure_on_exception():
    cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=1, open_timeout_seconds=10))

    def fail():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        cb.call(fail)
    assert cb.state == CircuitState.OPEN


def test_force_open_and_force_close():
    cb = CircuitBreaker("test")
    cb.force_open()
    assert cb.state == CircuitState.OPEN
    cb.force_close()
    assert cb.state == CircuitState.CLOSED


# --- get_breaker / get_all_states / reset_all ---

def test_get_breaker_singleton():
    a = get_breaker("eastmoney")
    b = get_breaker("eastmoney")
    assert a is b


def test_get_breaker_different_names_different_instances():
    a = get_breaker("eastmoney")
    b = get_breaker("tencent")
    assert a is not b


def test_get_all_states_empty():
    assert get_all_states() == {}


def test_get_all_states_after_registering():
    cb = get_breaker("eastmoney")
    cb.record_failure()
    states = get_all_states()
    assert "eastmoney" in states
    assert states["eastmoney"] in ("CLOSED", "OPEN", "HALF_OPEN")


# --- external_api.safe_get ---

def test_safe_get_returns_response_on_success():
    fake = MagicMock()
    fake.json.return_value = {"ok": True}
    with patch("modules.external_api.requests.get", return_value=fake) as mg:
        resp = external_api.safe_get("https://push2.eastmoney.com/api/qt/clist/get")
    assert resp is fake
    assert mg.called


def test_safe_get_returns_none_on_circuit_open():
    """熔断 OPEN 时直接返回 None, 不发请求."""
    cb = get_breaker("eastmoney.com", CircuitBreakerConfig(failure_threshold=1, open_timeout_seconds=10))
    cb.force_open()
    with patch("modules.external_api.requests.get") as mg:
        resp = external_api.safe_get("https://eastmoney.com/api/data")
    assert resp is None
    assert not mg.called


def test_safe_get_returns_none_on_request_exception():
    import requests
    with patch("modules.external_api.requests.get", side_effect=requests.ConnectionError("nope")):
        resp = external_api.safe_get("https://example.com/api")
    assert resp is None


def test_safe_get_records_failure_in_circuit():
    """每次失败应计入熔断器失败计数."""
    import requests
    cb = get_breaker("example.com", CircuitBreakerConfig(failure_threshold=2, open_timeout_seconds=10))
    with patch("modules.external_api.requests.get", side_effect=requests.Timeout()):
        external_api.safe_get("https://example.com/api")
        external_api.safe_get("https://example.com/api")
    assert cb.state == CircuitState.OPEN


def test_safe_get_json_parses_response():
    fake = MagicMock()
    fake.json.return_value = {"data": [1, 2, 3]}
    with patch("modules.external_api.requests.get", return_value=fake):
        result = external_api.safe_get_json("https://example.com/api")
    assert result == {"data": [1, 2, 3]}


def test_safe_get_json_returns_none_on_invalid_json():
    fake = MagicMock()
    fake.json.side_effect = ValueError("bad json")
    with patch("modules.external_api.requests.get", return_value=fake):
        result = external_api.safe_get_json("https://example.com/api")
    assert result is None


def test_safe_get_json_returns_none_on_circuit_open():
    cb = get_breaker("example.com", CircuitBreakerConfig(failure_threshold=1, open_timeout_seconds=10))
    cb.force_open()
    result = external_api.safe_get_json("https://example.com/api")
    assert result is None


# --- /api/health 集成 ---

def test_health_includes_circuits_field():
    from web_app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/api/health")
    assert r.status_code == 200
    data = r.get_json()
    assert "circuits" in data
    assert isinstance(data["circuits"], dict)
