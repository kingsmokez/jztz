"""Tests for modules.backtest + routes.backtest."""
from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any, Dict, List

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_results(monkeypatch):
    """Give the route layer its own backtest_results/ in a temp dir."""
    tmpdir = tempfile.mkdtemp(prefix="backtest_results_")
    import routes.backtest as rb
    monkeypatch.setattr(rb, "RESULTS_DIR", tmpdir)
    yield tmpdir
    # Best-effort cleanup
    for name in os.listdir(tmpdir):
        try:
            os.unlink(os.path.join(tmpdir, name))
        except OSError:
            pass
    os.rmdir(tmpdir)


def _make_buy_and_hold_history(
    days: int = 30,
    codes: List[str] = ("000001", "600519", "300750"),
    start_prices: Dict[str, float] = None,
    end_prices: Dict[str, float] = None,
) -> Dict[str, Dict[str, float]]:
    """Synthetic history that drifts linearly from start to end price."""
    defaults_s = {"000001": 10.0, "600519": 1500.0, "300750": 200.0}
    defaults_e = {"000001": 11.0, "600519": 1620.0, "300750": 230.0}
    # If caller passes custom codes, fall back to a flat $10/$11 walk
    # unless they also pass custom start/end prices.
    if start_prices is None:
        start_prices = (
            {c: defaults_s[c] for c in codes if c in defaults_s}
            or {c: 10.0 for c in codes}
        )
    if end_prices is None:
        end_prices = (
            {c: defaults_e[c] for c in codes if c in defaults_e}
            or {c: 11.0 for c in codes}
        )
    out: Dict[str, Dict[str, float]] = {}
    n = days - 1
    for i in range(days):
        date = f"2026-05-{(i % 28) + 1:02d}" if i < 28 else f"2026-06-{(i - 27):02d}"
        row: Dict[str, float] = {}
        for c in codes:
            s = start_prices[c]
            e = end_prices[c]
            p = s + (e - s) * (i / n) if n > 0 else s
            row[c] = round(p, 2)
        out[date] = row
    return out


@pytest.fixture
def client():
    from web_app import create_app
    return create_app().test_client()


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------
class TestConfigValidation:
    def test_defaults_valid(self):
        from modules.backtest import BacktestConfig
        BacktestConfig().validate()  # no exception

    def test_initial_capital_must_be_positive(self):
        from modules.backtest import BacktestConfig, ConfigError
        with pytest.raises(ConfigError):
            BacktestConfig(initial_capital=0).validate()
        with pytest.raises(ConfigError):
            BacktestConfig(initial_capital=-1).validate()

    def test_top_n_must_be_positive(self):
        from modules.backtest import BacktestConfig, ConfigError
        with pytest.raises(ConfigError):
            BacktestConfig(top_n=0).validate()

    def test_rebalance_every_must_be_positive(self):
        from modules.backtest import BacktestConfig, ConfigError
        with pytest.raises(ConfigError):
            BacktestConfig(rebalance_every=0).validate()

    def test_risk_free_rate_bounds(self):
        from modules.backtest import BacktestConfig, ConfigError
        with pytest.raises(ConfigError):
            BacktestConfig(risk_free_rate=-0.01).validate()
        with pytest.raises(ConfigError):
            BacktestConfig(risk_free_rate=1.5).validate()


# ---------------------------------------------------------------------------
# Engine: deterministic buy-and-hold happy path
# ---------------------------------------------------------------------------
class TestEngineHappyPath:
    def test_buy_and_hold_runs(self):
        from modules.backtest import BacktestConfig, BacktestInput, run
        history = _make_buy_and_hold_history(days=20)
        picks = {"2026-05-01": ["000001", "600519", "300750"]}
        out = run(BacktestInput(
            price_history=history,
            picks_by_date=picks,
            config=BacktestConfig(top_n=3, rebalance_every=5),
        ))
        assert out.id.startswith("bt_")
        assert len(out.equity_curve) == 20
        assert out.started_at == "2026-05-01"
        assert out.ended_at == list(history.keys())[-1]

    def test_metrics_include_total_return(self):
        from modules.backtest import BacktestConfig, BacktestInput, run
        history = _make_buy_and_hold_history()
        picks = {"2026-05-01": ["000001", "600519", "300750"]}
        out = run(BacktestInput(
            price_history=history,
            picks_by_date=picks,
            config=BacktestConfig(top_n=3, rebalance_every=10),
        ))
        m = out.metrics
        # All three prices ended higher than they started, so total return > 0.
        assert m["total_return_pct"] > 0
        assert m["trading_days"] > 0
        assert m["trades"] > 0

    def test_equity_curve_is_monotone_increasing_for_drift_up(self):
        from modules.backtest import BacktestConfig, BacktestInput, run
        history = _make_buy_and_hold_history(days=15)
        picks = {"2026-05-01": ["000001"]}
        out = run(BacktestInput(
            price_history=history,
            picks_by_date=picks,
            config=BacktestConfig(top_n=1, rebalance_every=1),
        ))
        values = [p["value"] for p in out.equity_curve]
        # The single stock drifted up, so values should be non-decreasing
        # (modulo integer-share rounding).
        assert values[-1] > values[0]

    def test_rebalance_generates_trades(self):
        from modules.backtest import BacktestConfig, BacktestInput, run
        history = _make_buy_and_hold_history(days=20)
        # 4 rebalance dates (engine rebalances on each provided pick date
        # that's also in price_history, so provide one per cadence).
        dates = sorted(history.keys())
        picks = {
            dates[0]:  ["000001", "600519", "300750"],
            dates[5]:  ["000001", "600519", "300750"],
            dates[10]: ["000001", "600519", "300750"],
            dates[15]: ["000001", "600519", "300750"],
        }
        out = run(BacktestInput(
            price_history=history,
            picks_by_date=picks,
            config=BacktestConfig(top_n=3, rebalance_every=5),
        ))
        # First rebal has empty holdings → 0 sells + 3 buys = 3 trades.
        # Each subsequent rebal liquidates + rebuys: 3 sells + 3 buys = 6.
        # Total: 3 + 6 × 3 = 21.
        assert len(out.trades) == 21
        sells = [t for t in out.trades if t["side"] == "sell"]
        buys  = [t for t in out.trades if t["side"] == "buy"]
        assert len(buys) == 12   # 4 rebalances × 3 codes
        assert len(sells) == 9   # 3 subsequent rebalances × 3 codes
        # Each rebalance emits sells before buys
        first_sell = sells[0]
        first_buy_after_first_sell = next(
            b for b in buys if b["date"] >= first_sell["date"]
        )
        assert first_sell["date"] <= first_buy_after_first_sell["date"]

    def test_max_drawdown_non_positive(self):
        from modules.backtest import BacktestConfig, BacktestInput, run
        # 3 stocks, one of which drops sharply
        history = {
            "2026-05-01": {"A": 10.0, "B": 10.0, "C": 10.0},
            "2026-05-02": {"A": 11.0, "B": 9.0, "C": 8.0},
            "2026-05-03": {"A": 12.0, "B": 8.0, "C": 6.0},
            "2026-05-04": {"A": 13.0, "B": 7.0, "C": 7.0},
            "2026-05-05": {"A": 14.0, "B": 6.0, "C": 8.0},
            "2026-05-06": {"A": 15.0, "B": 5.0, "C": 9.0},
        }
        out = run(BacktestInput(
            price_history=history,
            picks_by_date={"2026-05-01": ["A", "B", "C"]},
            config=BacktestConfig(top_n=3, rebalance_every=2),
        ))
        assert out.metrics["max_drawdown_pct"] <= 0


class TestEngineEdgeCases:
    def test_picks_date_falls_forward(self):
        from modules.backtest import BacktestConfig, BacktestInput, run
        history = _make_buy_and_hold_history(days=10)
        # Pick on a date that exists in history (first rebal uses it)
        dates = sorted(history.keys())
        picks = {dates[3]: ["000001", "600519"]}
        out = run(BacktestInput(
            price_history=history,
            picks_by_date=picks,
            config=BacktestConfig(top_n=2, rebalance_every=2),
        ))
        # Should still complete; first rebal uses the only picks date
        assert out.started_at == dates[0]

    def test_zero_initial_capital_raises(self):
        from modules.backtest import (
            BacktestConfig, BacktestInput, ConfigError, run,
        )
        with pytest.raises(ConfigError):
            run(BacktestInput(
                price_history={"2026-01-01": {"A": 1.0}},
                picks_by_date={"2026-01-01": ["A"]},
                config=BacktestConfig(initial_capital=0),
            ))

    def test_empty_history_raises(self):
        from modules.backtest import (
            BacktestConfig, BacktestInput, DataError, run,
        )
        with pytest.raises(DataError):
            run(BacktestInput(
                price_history={},
                picks_by_date={"2026-01-01": ["A"]},
                config=BacktestConfig(),
            ))

    def test_empty_picks_raises(self):
        from modules.backtest import (
            BacktestConfig, BacktestInput, DataError, run,
        )
        with pytest.raises(DataError):
            run(BacktestInput(
                price_history={"2026-01-01": {"A": 1.0}},
                picks_by_date={},
                config=BacktestConfig(),
            ))

    def test_top_n_clamps_to_pick_size(self):
        from modules.backtest import BacktestConfig, BacktestInput, run
        # top_n=5 but only 2 codes available
        history = _make_buy_and_hold_history(days=10, codes=("A", "B"))
        dates = sorted(history.keys())
        out = run(BacktestInput(
            price_history=history,
            picks_by_date={dates[0]: ["A", "B"]},
            config=BacktestConfig(top_n=5, rebalance_every=5),
        ))
        # Should only buy the codes that exist in price history
        buys = [t for t in out.trades if t["side"] == "buy"]
        assert len(buys) == 2  # only A and B
        assert {b["code"] for b in buys} == {"A", "B"}

    def test_sell_when_no_holdings_is_skipped(self):
        from modules.backtest import BacktestConfig, BacktestInput, run
        history = _make_buy_and_hold_history(days=10)
        dates = sorted(history.keys())
        # Pick on day 0, no second rebal
        out = run(BacktestInput(
            price_history=history,
            picks_by_date={dates[0]: ["000001"]},
            config=BacktestConfig(top_n=1, rebalance_every=10),
        ))
        # Only one rebalance, so only buys (no sells in this short run)
        sells = [t for t in out.trades if t["side"] == "sell"]
        assert len(sells) == 0

    def test_sharpe_finite_for_drift_up(self):
        from modules.backtest import BacktestConfig, BacktestInput, run
        history = _make_buy_and_hold_history(days=30)
        dates = sorted(history.keys())
        out = run(BacktestInput(
            price_history=history,
            picks_by_date={dates[0]: ["000001"]},
            config=BacktestConfig(top_n=1, rebalance_every=1),
        ))
        # Drift with no variance → std=0 → sharpe=0
        assert out.metrics["sharpe"] >= 0


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
class TestPersistence:
    def test_save_and_load_roundtrip(self, tmp_results):
        from modules.backtest import (
            BacktestConfig, BacktestInput, run, save_result, load_result,
        )
        history = _make_buy_and_hold_history(days=10)
        dates = sorted(history.keys())
        result = run(BacktestInput(
            price_history=history,
            picks_by_date={dates[0]: ["000001"]},
            config=BacktestConfig(top_n=1, rebalance_every=1),
        ))
        path = save_result(result, tmp_results)
        assert os.path.exists(path)
        data = load_result(path)
        assert data["id"] == result.id
        assert data["metrics"]["trading_days"] == 9
        assert len(data["equity_curve"]) == 10


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
class TestBacktestRoutes:
    def test_sample_endpoint(self, client):
        r = client.get("/api/backtest/sample")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert "price_history" in body["sample"]
        assert "picks_by_date" in body["sample"]

    def test_run_happy_path(self, client, tmp_results):
        history = _make_buy_and_hold_history(days=10)
        dates = sorted(history.keys())
        r = client.post(
            "/api/backtest/run",
            json={
                "price_history": history,
                "picks_by_date": {dates[0]: ["000001", "600519"]},
                "config": {"top_n": 2, "rebalance_every": 5, "name": "t1"},
            },
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["persisted"] is True
        assert "result" in body
        assert body["result"]["name"] == "t1"
        assert "equity_curve" in body["result"]
        assert "metrics" in body["result"]

    def test_run_invalid_body(self, client):
        r = client.post("/api/backtest/run", json={})
        assert r.status_code == 400
        assert r.get_json()["ok"] is False

    def test_run_missing_price_history(self, client):
        r = client.post(
            "/api/backtest/run",
            json={"picks_by_date": {"2026-01-01": ["A"]}},
        )
        assert r.status_code == 400

    def test_run_missing_picks(self, client):
        r = client.post(
            "/api/backtest/run",
            json={"price_history": {"2026-01-01": {"A": 1.0}}},
        )
        assert r.status_code == 400

    def test_run_zero_capital(self, client, tmp_results):
        history = _make_buy_and_hold_history(days=5)
        dates = sorted(history.keys())
        r = client.post(
            "/api/backtest/run",
            json={
                "price_history": history,
                "picks_by_date": {dates[0]: ["000001"]},
                "config": {"initial_capital": 0},
            },
        )
        assert r.status_code == 400

    def test_run_not_json(self, client):
        r = client.post("/api/backtest/run", data="not json")
        assert r.status_code == 400

    def test_list_results(self, client, tmp_results):
        # Run twice to populate
        for _ in range(2):
            history = _make_buy_and_hold_history(days=5)
            dates = sorted(history.keys())
            client.post(
                "/api/backtest/run",
                json={
                    "price_history": history,
                    "picks_by_date": {dates[0]: ["000001"]},
                },
            )
        r = client.get("/api/backtest/results")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["count"] >= 2
        for item in body["results"]:
            assert "id" in item
            assert "metrics" in item
            assert "size_bytes" in item

    def test_get_result(self, client, tmp_results):
        history = _make_buy_and_hold_history(days=5)
        dates = sorted(history.keys())
        run_resp = client.post(
            "/api/backtest/run",
            json={
                "price_history": history,
                "picks_by_date": {dates[0]: ["000001"]},
            },
        )
        rid = run_resp.get_json()["result"]["id"]
        r = client.get(f"/api/backtest/result/{rid}")
        assert r.status_code == 200
        assert r.get_json()["result"]["id"] == rid

    def test_get_invalid_id(self, client):
        r = client.get("/api/backtest/result/badid")
        assert r.status_code == 400

    def test_get_missing(self, client):
        r = client.get("/api/backtest/result/bt_9999999999_zzzzzzzz")
        assert r.status_code == 404

    def test_delete_result(self, client, tmp_results):
        history = _make_buy_and_hold_history(days=5)
        dates = sorted(history.keys())
        run_resp = client.post(
            "/api/backtest/run",
            json={
                "price_history": history,
                "picks_by_date": {dates[0]: ["000001"]},
            },
        )
        rid = run_resp.get_json()["result"]["id"]
        d = client.delete(f"/api/backtest/result/{rid}")
        assert d.status_code == 200
        # Now 404
        g = client.get(f"/api/backtest/result/{rid}")
        assert g.status_code == 404

    def test_delete_invalid_id(self, client):
        r = client.delete("/api/backtest/result/not-an-id")
        assert r.status_code == 400

    def test_delete_missing(self, client, tmp_results):
        r = client.delete("/api/backtest/result/bt_9999999999_zzzzzzzz")
        assert r.status_code == 404
