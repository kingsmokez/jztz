"""Tests for modules.portfolio + routes.portfolio."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from typing import Any, Dict, List
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_store(monkeypatch):
    """Give every test its own portfolio.json in a temp dir."""
    tmpdir = tempfile.mkdtemp(prefix="portfolio_test_")
    path = os.path.join(tmpdir, "portfolio.json")
    from modules import portfolio as p_mod
    store = p_mod.PortfolioStore(path)
    monkeypatch.setattr(p_mod, "_default", store, raising=False)
    yield store
    # cleanup
    if os.path.exists(path):
        os.unlink(path)
    os.rmdir(tmpdir)


@pytest.fixture
def client():
    from web_app import create_app
    app = create_app()
    return app.test_client()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestValidatePosition:
    def test_minimal_valid(self):
        from modules.portfolio import _validate_position
        out = _validate_position(
            {"code": "000001", "shares": 100, "cost": 10.5}
        )
        assert out["code"] == "000001"
        assert out["shares"] == 100
        assert out["cost"] == 10.5

    def test_missing_required(self):
        from modules.portfolio import _validate_position, ValidationError
        with pytest.raises(ValidationError):
            _validate_position({"code": "000001"})

    def test_partial_allowed(self):
        from modules.portfolio import _validate_position
        out = _validate_position({"notes": "x"}, partial=True)
        assert out["notes"] == "x"

    def test_invalid_code(self):
        from modules.portfolio import _validate_position, ValidationError
        with pytest.raises(ValidationError):
            _validate_position(
                {"code": "abc", "shares": 1, "cost": 1}
            )

    def test_invalid_shares(self):
        from modules.portfolio import _validate_position, ValidationError
        with pytest.raises(ValidationError):
            _validate_position(
                {"code": "000001", "shares": 0, "cost": 1}
            )

    def test_invalid_cost(self):
        from modules.portfolio import _validate_position, ValidationError
        with pytest.raises(ValidationError):
            _validate_position(
                {"code": "000001", "shares": 1, "cost": -1}
            )

    def test_invalid_date(self):
        from modules.portfolio import _validate_position, ValidationError
        with pytest.raises(ValidationError):
            _validate_position(
                {"code": "000001", "shares": 1, "cost": 1,
                 "buy_date": "2026-13-40"}
            )


# ---------------------------------------------------------------------------
# CRUD on a temp store
# ---------------------------------------------------------------------------
class TestStoreCRUD:
    def test_add_assigns_id(self, tmp_store):
        p = tmp_store.add({
            "code": "000001", "name": "平安银行",
            "shares": 100, "cost": 10.5,
        })
        assert p["id"].startswith("p_")
        assert len(p["id"]) > 5

    def test_add_persists_to_disk(self, tmp_store):
        tmp_store.add({
            "code": "000001", "shares": 100, "cost": 10.5,
        })
        # Re-open
        from modules.portfolio import PortfolioStore
        s2 = PortfolioStore(tmp_store.path)
        assert len(s2.list()) == 1

    def test_list_empty(self, tmp_store):
        assert tmp_store.list() == []

    def test_get_existing(self, tmp_store):
        p = tmp_store.add({
            "code": "000001", "shares": 100, "cost": 10.5,
        })
        assert tmp_store.get(p["id"]) is not None
        assert tmp_store.get(p["id"])["code"] == "000001"

    def test_get_missing(self, tmp_store):
        assert tmp_store.get("p_doesnotexist") is None

    def test_update(self, tmp_store):
        p = tmp_store.add({
            "code": "000001", "shares": 100, "cost": 10.5,
        })
        updated = tmp_store.update(p["id"], {"shares": 200, "notes": "ok"})
        assert updated["shares"] == 200
        assert updated["notes"] == "ok"
        assert tmp_store.get(p["id"])["shares"] == 200

    def test_update_missing_raises(self, tmp_store):
        from modules.portfolio import NotFoundError
        with pytest.raises(NotFoundError):
            tmp_store.update("p_doesnotexist", {"shares": 100})

    def test_update_empty_patch(self, tmp_store):
        from modules.portfolio import ValidationError
        p = tmp_store.add({
            "code": "000001", "shares": 100, "cost": 10.5,
        })
        with pytest.raises(ValidationError):
            tmp_store.update(p["id"], {})

    def test_delete(self, tmp_store):
        p = tmp_store.add({
            "code": "000001", "shares": 100, "cost": 10.5,
        })
        assert tmp_store.delete(p["id"]) is True
        assert tmp_store.get(p["id"]) is None

    def test_delete_missing(self, tmp_store):
        assert tmp_store.delete("p_doesnotexist") is False

    def test_clear(self, tmp_store):
        tmp_store.add({"code": "000001", "shares": 1, "cost": 1})
        tmp_store.add({"code": "600000", "shares": 1, "cost": 1})
        assert tmp_store.clear() == 2
        assert tmp_store.list() == []

    def test_dedup_same_code_and_date_merges(self, tmp_store):
        tmp_store.add({
            "code": "000001", "shares": 100, "cost": 10.0,
            "buy_date": "2026-05-01",
        })
        tmp_store.add({
            "code": "000001", "shares": 100, "cost": 12.0,
            "buy_date": "2026-05-01",
        })
        positions = tmp_store.list()
        assert len(positions) == 1
        # Weighted-average cost = (10*100 + 12*100) / 200 = 11
        assert positions[0]["cost"] == 11.0
        assert positions[0]["shares"] == 200

    def test_different_dates_kept_separate(self, tmp_store):
        tmp_store.add({
            "code": "000001", "shares": 100, "cost": 10.0,
            "buy_date": "2026-05-01",
        })
        tmp_store.add({
            "code": "000001", "shares": 100, "cost": 12.0,
            "buy_date": "2026-05-02",
        })
        assert len(tmp_store.list()) == 2

    def test_summary(self, tmp_store):
        tmp_store.add({"code": "000001", "shares": 100, "cost": 10.0})
        tmp_store.add({"code": "600000", "shares": 200, "cost": 20.0})
        s = tmp_store.summary()
        assert s["position_count"] == 2
        assert s["total_shares"] == 300
        assert s["total_cost"] == 100 * 10.0 + 200 * 20.0

    def test_concurrent_adds(self, tmp_store):
        """Many threads adding positions must not lose any."""
        from modules.portfolio import PortfolioStore
        N = 30
        results = []

        def worker(i):
            results.append(tmp_store.add({
                "code": f"00000{i % 10}",
                "shares": 1,
                "cost": 1.0,
            }))

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert len(results) == N
        assert len(tmp_store.list()) == N


# ---------------------------------------------------------------------------
# P&L
# ---------------------------------------------------------------------------
class TestPnl:
    def test_no_price_lookup(self, monkeypatch):
        from modules.portfolio import compute_pnl
        # No live snapshot in cache → current_price is None
        from modules.cache_manager import cache
        cache.clear()
        p = {"code": "000001", "shares": 100, "cost": 10.0}
        out = compute_pnl(p)
        assert out["current_price"] is None
        assert out["cost_value"] == 1000.0
        assert "market_value" not in out

    def test_with_price_lookup(self, monkeypatch):
        from modules.portfolio import compute_pnl
        from modules import portfolio as p_mod
        from modules.cache_manager import cache

        def fake_lookup(code):
            mapping = {"000001": 12.0, "600519": 1500.0}
            return mapping.get(code)

        monkeypatch.setattr(p_mod, "_latest_price_for", fake_lookup)
        cache.clear()
        p = {"code": "000001", "shares": 100, "cost": 10.0}
        out = compute_pnl(p)
        assert out["current_price"] == 12.0
        assert out["market_value"] == 1200.0
        assert out["profit"] == 200.0
        assert out["profit_pct"] == 20.0

    def test_portfolio_aggregate(self, monkeypatch):
        from modules.portfolio import compute_portfolio_pnl
        from modules import portfolio as p_mod
        from modules.cache_manager import cache

        def fake_lookup(code):
            return {"000001": 12.0, "600000": 5.0}.get(code)

        monkeypatch.setattr(p_mod, "_latest_price_for", fake_lookup)
        cache.clear()
        positions = [
            {"code": "000001", "shares": 100, "cost": 10.0},
            {"code": "600000", "shares": 200, "cost": 4.0},
        ]
        agg = compute_portfolio_pnl(positions)
        # cost = 100*10 + 200*4 = 1800
        # market = 100*12 + 200*5 = 2200
        # profit = 400
        assert agg["totals"]["cost"] == 1800.0
        assert agg["totals"]["market"] == 2200.0
        assert agg["totals"]["profit"] == 400.0
        # pct = 400/1800*100 ≈ 22.22
        assert abs(agg["totals"]["profit_pct"] - 22.22) < 0.01
        assert agg["totals"]["valued"] == 2

    def test_portfolio_partial_valuation(self, monkeypatch):
        from modules.portfolio import compute_portfolio_pnl
        from modules import portfolio as p_mod
        from modules.cache_manager import cache

        def fake_lookup(code):
            return {"000001": 12.0}.get(code)  # 600000 missing

        monkeypatch.setattr(p_mod, "_latest_price_for", fake_lookup)
        cache.clear()
        positions = [
            {"code": "000001", "shares": 100, "cost": 10.0},
            {"code": "600000", "shares": 200, "cost": 4.0},
        ]
        agg = compute_portfolio_pnl(positions)
        # Only one position is valued.
        assert agg["totals"]["valued"] == 1


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
class TestRoutes:
    def test_list_empty(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        # Also patch the route module's reference.
        import routes.portfolio as rp
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        r = client.get("/api/portfolio")
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True
        assert body["count"] == 0
        assert body["positions"] == []

    def test_add_then_list(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)

        r = client.post(
            "/api/portfolio",
            json={
                "code": "000001", "name": "平安银行",
                "shares": 100, "cost": 10.0,
            },
        )
        assert r.status_code == 201
        body = r.get_json()
        assert body["success"] is True
        assert body["position"]["id"].startswith("p_")

        r2 = client.get("/api/portfolio")
        assert r2.get_json()["count"] == 1

    def test_add_validation_error(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        r = client.post("/api/portfolio", json={"code": "abc"})
        assert r.status_code == 400

    def test_get_single(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        p = tmp_store.add({
            "code": "000001", "shares": 100, "cost": 10.0,
        })
        r = client.get(f"/api/portfolio/{p['id']}")
        assert r.status_code == 200
        assert r.get_json()["position"]["code"] == "000001"

    def test_get_invalid_id(self, client):
        r = client.get("/api/portfolio/not_a_valid_id")
        assert r.status_code == 400

    def test_get_missing(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        r = client.get("/api/portfolio/p_1234567890_abcdef12")
        assert r.status_code == 404

    def test_update(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        p = tmp_store.add({
            "code": "000001", "shares": 100, "cost": 10.0,
        })
        r = client.put(
            f"/api/portfolio/{p['id']}",
            json={"shares": 200, "notes": "doubled"},
        )
        assert r.status_code == 200
        assert r.get_json()["position"]["shares"] == 200
        assert r.get_json()["position"]["notes"] == "doubled"

    def test_delete(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        p = tmp_store.add({
            "code": "000001", "shares": 100, "cost": 10.0,
        })
        r = client.delete(f"/api/portfolio/{p['id']}")
        assert r.status_code == 200
        # Follow-up GET is 404
        r2 = client.get(f"/api/portfolio/{p['id']}")
        assert r2.status_code == 404

    def test_delete_missing(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        r = client.delete("/api/portfolio/p_doesnotexist")
        assert r.status_code == 404

    def test_summary(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        tmp_store.add({"code": "000001", "shares": 100, "cost": 10.0})
        tmp_store.add({"code": "600000", "shares": 200, "cost": 20.0})
        r = client.get("/api/portfolio/summary")
        assert r.status_code == 200
        body = r.get_json()
        assert body["position_count"] == 2
        assert body["total_shares"] == 300
        assert body["total_cost"] == 100 * 10.0 + 200 * 20.0

    def test_export_csv(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        tmp_store.add({"code": "000001", "name": "平安银行",
                       "shares": 100, "cost": 10.0})
        r = client.get("/api/portfolio/export?format=csv")
        assert r.status_code == 200
        assert r.headers.get("Content-Type", "").startswith("text/csv")
        assert r.data.startswith(b"\xef\xbb\xbf")
        assert "000001".encode() in r.data

    def test_export_xlsx(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        tmp_store.add({"code": "000001", "name": "A",
                       "shares": 100, "cost": 10.0})
        r = client.get("/api/portfolio/export?format=xlsx")
        assert r.status_code == 200
        assert r.data[:2] == b"PK"

    def test_export_bad_format(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        r = client.get("/api/portfolio/export?format=pdf")
        assert r.status_code == 400

    def test_export_empty(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        r = client.get("/api/portfolio/export?format=csv")
        assert r.status_code == 404

    def test_list_with_pnl(self, client, monkeypatch, tmp_store):
        from modules import portfolio as p_mod
        import routes.portfolio as rp
        monkeypatch.setattr(p_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(rp, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(
            p_mod, "_latest_price_for",
            lambda code: {"000001": 12.0}.get(code),
        )
        from modules.cache_manager import cache
        cache.clear()
        tmp_store.add({"code": "000001", "shares": 100, "cost": 10.0})
        r = client.get("/api/portfolio?pnl=true")
        assert r.status_code == 200
        body = r.get_json()
        assert body["positions"][0]["current_price"] == 12.0
        assert body["totals"]["market"] == 1200.0
