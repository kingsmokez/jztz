"""Tests for the real-data backtest endpoint.

These tests verify the endpoint logic without hitting the real Tencent API
by mocking the K-line fetcher functions.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock
from collections import defaultdict


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from web_app import create_app
    return create_app().test_client()


def _make_realistic_price_history(
    days: int = 60,
    codes: list[str] = None,
) -> dict[str, dict[str, float]]:
    """Generate realistic-looking price history for testing."""
    if codes is None:
        codes = ["000001", "600519", "300750", "000858", "600036"]
    history: dict[str, dict[str, float]] = {}
    prices = {c: 10.0 + hash(c) % 100 for c in codes}
    for i in range(days):
        day = i % 28 + 1
        month = 5 if i < 28 else 6
        date = f"2026-{month:02d}-{day:02d}"
        row = {}
        for c in codes:
            import random
            random.seed(hash(c) + i)
            change = random.uniform(-0.03, 0.04)
            prices[c] = prices[c] * (1 + change)
            row[c] = round(prices[c], 2)
        history[date] = row
    return history


# ---------------------------------------------------------------------------
# Unit tests for strategy rankers
# ---------------------------------------------------------------------------

class TestStrategyRankers:
    """Test the strategy ranking functions directly."""

    def test_momentum_picks_top_gainers(self):
        from routes.backtest import _rank_by_momentum
        ph = {
            "2026-05-01": {"A": 10.0, "B": 100.0, "C": 50.0},
            "2026-05-02": {"A": 10.0, "B": 100.0, "C": 50.0},
            "2026-05-03": {"A": 10.0, "B": 100.0, "C": 50.0},
            "2026-05-04": {"A": 10.0, "B": 100.0, "C": 50.0},
            "2026-05-05": {"A": 12.0, "B": 90.0, "C": 55.0},  # A +20%, B -10%, C +10%
        }
        dates = sorted(ph.keys())
        result = _rank_by_momentum(ph, dates, top_n=2)
        assert result[0] == "A"  # A has highest momentum (+20%)
        assert "B" not in result  # B has negative momentum

    def test_breakout_picks_near_highs(self):
        from routes.backtest import _rank_by_breakout
        # Stock A is at 20-day high, Stock B is far from high
        ph = {}
        for i in range(20):
            day = f"2026-05-{i+1:02d}"
            ph[day] = {
                "A": 10.0 + i * 0.5,  # trending up, at high
                "B": 20.0 - i * 0.3,  # trending down, far from high
            }
        dates = sorted(ph.keys())
        result = _rank_by_breakout(ph, dates, top_n=1)
        assert result[0] == "A"

    def test_trend_picks_accelerating(self):
        from routes.backtest import _rank_by_trend
        ph = {}
        for i in range(10):
            day = f"2026-05-{i+1:02d}"
            ph[day] = {
                "A": 10.0 + i * 1.0,  # steady rise
                "B": 10.0 + (i - 5) ** 2 * 0.5,  # accelerating
            }
        dates = sorted(ph.keys())
        result = _rank_by_trend(ph, dates, top_n=2)
        assert len(result) == 2

    def test_momentum_short_history_fallback(self):
        from routes.backtest import _rank_by_momentum
        ph = {"2026-05-01": {"A": 10.0}}
        result = _rank_by_momentum(ph, ["2026-05-01"], top_n=1)
        assert result == []  # Not enough data


class TestFetchKlinesConcurrent:
    """Test the concurrent K-line fetcher with mocked HTTP."""

    def test_fetch_concurrent_success(self):
        from routes.backtest import _fetch_klines_concurrent

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "sh600519": {
                    "qfqday": [
                        ["2026-05-01", "1500", "1510", "1520", "1490", "1000"],
                        ["2026-05-02", "1510", "1520", "1530", "1500", "1100"],
                    ]
                }
            }
        }

        with patch("routes.backtest._get_http_session") as mock_sess:
            mock_sess.return_value.get.return_value = mock_response
            result = _fetch_klines_concurrent(["600519"], lookback=10)
            assert len(result) >= 1
            for date, prices in result.items():
                assert "600519" in prices

    def test_fetch_concurrent_empty(self):
        from routes.backtest import _fetch_klines_concurrent

        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {}}

        with patch("routes.backtest._get_http_session") as mock_sess:
            mock_sess.return_value.get.return_value = mock_response
            result = _fetch_klines_concurrent(["999999"], lookback=10)
            assert len(result) == 0


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestRealBacktestEndpoint:
    """Test POST /api/backtest/run_real endpoint."""

    def test_run_real_single_strategy(self, client):
        mock_ph = _make_realistic_price_history(days=60)
        mock_bm = {"2026-05-01": {"CSI300": 3800.0}}

        with patch("routes.backtest._fetch_klines_concurrent", return_value=mock_ph), \
             patch("routes.backtest._fetch_benchmark_kline", return_value=mock_bm):
            r = client.post("/api/backtest/run_real", json={
                "strategy": "daily",
                "lookback_days": 60,
                "top_n": 3,
                "rebalance_every": 5,
            })
            assert r.status_code == 200
            body = r.get_json()
            assert body["ok"] is True
            assert body["strategy"] == "daily"
            assert "daily" in body["results"]
            assert "daily" in body["summary"]
            assert "CSI300基准" in body["results"]
            assert "trading_days" in body
            assert body["trading_days"] > 0

    def test_run_real_all_strategies(self, client):
        mock_ph = _make_realistic_price_history(days=40)

        with patch("routes.backtest._fetch_klines_concurrent", return_value=mock_ph), \
             patch("routes.backtest._fetch_benchmark_kline", return_value={}):
            r = client.post("/api/backtest/run_real", json={
                "strategy": "all",
                "include_benchmark": False,
            })
            assert r.status_code == 200
            body = r.get_json()
            assert body["ok"] is True
            # Should have all 4 strategies
            for strat in ["daily", "strong", "auction", "wp2"]:
                assert strat in body["results"], f"Missing strategy: {strat}"

    def test_run_real_invalid_strategy(self, client):
        r = client.post("/api/backtest/run_real", json={
            "strategy": "invalid",
        })
        assert r.status_code == 400
        body = r.get_json()
        assert body["ok"] is False
        assert "strategy" in body["error"]

    def test_run_real_insufficient_data(self, client):
        # Only 5 days of data — not enough
        mock_ph = _make_realistic_price_history(days=5)

        with patch("routes.backtest._fetch_klines_concurrent", return_value=mock_ph):
            r = client.post("/api/backtest/run_real", json={
                "strategy": "daily",
            })
            assert r.status_code == 500

    def test_run_real_fetch_failure(self, client):
        with patch("routes.backtest._fetch_klines_concurrent", side_effect=Exception("API down")):
            r = client.post("/api/backtest/run_real", json={
                "strategy": "daily",
            })
            assert r.status_code == 500
            body = r.get_json()
            assert body["ok"] is False

    def test_run_real_custom_codes(self, client):
        mock_ph = _make_realistic_price_history(
            days=40, codes=["000001", "600519"]
        )

        with patch("routes.backtest._fetch_klines_concurrent", return_value=mock_ph) as mock_fetch, \
             patch("routes.backtest._fetch_benchmark_kline", return_value={}):
            r = client.post("/api/backtest/run_real", json={
                "strategy": "strong",
                "custom_codes": ["000001", "600519"],
                "include_benchmark": False,
            })
            assert r.status_code == 200
            body = r.get_json()
            assert body["ok"] is True
            assert body["codes_used"] == 2
            # Verify custom_codes was passed to fetcher
            mock_fetch.assert_called_once()
            call_codes = mock_fetch.call_args[0][0]
            assert call_codes == ["000001", "600519"]

    def test_run_real_summary_has_cost_metrics(self, client):
        mock_ph = _make_realistic_price_history(days=60)

        with patch("routes.backtest._fetch_klines_concurrent", return_value=mock_ph), \
             patch("routes.backtest._fetch_benchmark_kline", return_value={}):
            r = client.post("/api/backtest/run_real", json={
                "strategy": "daily",
                "include_benchmark": False,
            })
            assert r.status_code == 200
            body = r.get_json()
            summary = body["summary"]["daily"]
            assert "total_cost_pct" in summary
            assert "total_cost_amount" in summary

    def test_run_real_default_params(self, client):
        mock_ph = _make_realistic_price_history(days=60)

        with patch("routes.backtest._fetch_klines_concurrent", return_value=mock_ph), \
             patch("routes.backtest._fetch_benchmark_kline", return_value={}):
            # Empty body should use defaults
            r = client.post("/api/backtest/run_real", json={})
            assert r.status_code == 200
            body = r.get_json()
            assert body["ok"] is True
            assert body["strategy"] == "all"
            assert body["lookback_days"] == 60
            assert body["top_n"] == 10

    def test_run_real_lookback_clamped(self, client):
        mock_ph = _make_realistic_price_history(days=60)

        with patch("routes.backtest._fetch_klines_concurrent", return_value=mock_ph), \
             patch("routes.backtest._fetch_benchmark_kline", return_value={}):
            # Request 500 days but should be clamped to 120
            r = client.post("/api/backtest/run_real", json={
                "strategy": "daily",
                "lookback_days": 500,
                "include_benchmark": False,
            })
            assert r.status_code == 200
            body = r.get_json()
            assert body["lookback_days"] == 120


class TestRealBacktestCostVerification:
    """Verify that real-data backtests include transaction costs."""

    def test_backtest_with_costs_lower_than_without(self, client):
        """A backtest with costs should have lower return than one without."""
        from modules.backtest import BacktestConfig, BacktestInput, run

        mock_ph = _make_realistic_price_history(days=30)
        dates = sorted(mock_ph.keys())
        picks = {dates[0]: list(list(mock_ph.values())[0].keys())[:3]}

        # With costs (default)
        cfg_cost = BacktestConfig(
            top_n=3, rebalance_every=5, name="with_cost",
            commission_rate=0.00025, stamp_tax_rate=0.0005, slippage=0.001,
        )
        # Without costs
        cfg_free = BacktestConfig(
            top_n=3, rebalance_every=5, name="no_cost",
            commission_rate=0, stamp_tax_rate=0, slippage=0,
        )

        result_cost = run(BacktestInput(
            price_history=mock_ph, picks_by_date=picks, config=cfg_cost,
        ))
        result_free = run(BacktestInput(
            price_history=mock_ph, picks_by_date=picks, config=cfg_free,
        ))

        # With costs should have lower or equal total return
        assert result_cost.metrics["total_return_pct"] <= result_free.metrics["total_return_pct"]
        # Cost metrics should be present
        assert result_cost.metrics["total_cost_pct"] > 0
        assert result_cost.metrics["total_cost_amount"] > 0
        # Free should have zero cost
        assert result_free.metrics["total_cost_pct"] == 0.0
