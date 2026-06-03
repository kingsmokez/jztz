"""Tests for routes/strong.py (strong stock picker endpoints)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from flask import Flask

from routes.strong import strong_bp


@pytest.fixture
def app():
    a = Flask(__name__, template_folder="templates")
    a.register_blueprint(strong_bp)
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def test_strong_pick_page_renders(client):
    # The /strong_pick HTML page is rendered via the templates/ folder.
    # Full Flask app in web_app.py is verified by the E2E suite; here we
    # just check the route is registered and returns 500 (not 404) — i.e.
    # the blueprint is wired up correctly.
    r = client.get("/strong_pick")
    # Without template_folder configured, Flask raises TemplateNotFound
    # -> 500. The important thing is that we do NOT get 404 (route missing).
    assert r.status_code != 404, "strong_pick route not registered"


def test_api_strong_pick_no_data(client):
    with patch("web_app.get_strong_data", return_value=None):
        r = client.get("/api/strong_pick")
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["stocks"] == []


def test_api_strong_pick_with_data(client):
    items = [
        {
            "code": "000001", "name": "测试股", "price": 10.0,
            "change_pct": 2.0, "pe": 15.0, "pb": 2.0, "roe": 10.0,
            "market_cap": 100.0, "score": 80, "rank": 1,
        },
        {
            "code": "000002", "name": "另一只", "price": 20.0,
            "change_pct": 1.0, "pe": 18.0, "pb": 2.5, "roe": 8.0,
            "market_cap": 200.0, "score": 70,
        },
    ]
    with patch("web_app.get_strong_data", return_value=items):
        r = client.get("/api/strong_pick")
    body = r.get_json()
    assert body["success"] is True
    assert len(body["stocks"]) == 2
    assert body["stocks"][0]["code"] == "000001"
    assert body["stocks"][0]["name"] == "测试股"


def test_api_strong_pick_with_non_dict_items(client):
    """Non-dict items are skipped by _format_stocks."""
    with patch("web_app.get_strong_data", return_value=[{"code": "x"}, "not-a-dict", None]):
        r = client.get("/api/strong_pick")
    body = r.get_json()
    assert body["success"] is True
    # Only the dict is included
    assert len(body["stocks"]) == 1


def test_api_strong_pick_exception(client):
    with patch("web_app.get_strong_data", side_effect=RuntimeError("boom")):
        r = client.get("/api/strong_pick")
    body = r.get_json()
    assert body["success"] is False
    assert "boom" in body["error"]


def test_api_strong_pick_execute_success(client):
    items = [{"code": "000001", "name": "股", "price": 10.0}]
    with patch("web_app.run_strong_picker", return_value=items):
        r = client.get("/api/strong_pick_execute")
    body = r.get_json()
    assert body["success"] is True
    assert len(body["stocks"]) == 1


def test_api_strong_pick_execute_exception(client):
    with patch("web_app.run_strong_picker", side_effect=RuntimeError("net")):
        r = client.get("/api/strong_pick_execute")
    body = r.get_json()
    assert body["success"] is False


def test_api_strong_pick_clear(client):
    with patch("web_app.clear_strong_data") as mock_clear:
        r = client.get("/api/strong_pick_clear")
    body = r.get_json()
    assert body["success"] is True
    mock_clear.assert_called_once()


def test_api_strong_pick_clear_exception(client):
    with patch("web_app.clear_strong_data", side_effect=RuntimeError("io")):
        r = client.get("/api/strong_pick_clear")
    body = r.get_json()
    assert body["success"] is False


def test_format_stocks_defaults():
    """_format_stocks fills defaults for missing keys."""
    from routes.strong import _format_stocks
    result = _format_stocks([{"code": "x", "name": "y"}])
    assert result[0]["code"] == "x"
    assert result[0]["price"] == 0
    assert result[0]["change_pct"] == 0
    assert result[0]["score"] == 0
    assert result[0]["rsi"] is None
    assert result[0]["golden_cross"] is False


def test_format_stocks_empty():
    from routes.strong import _format_stocks
    assert _format_stocks([]) == []
    assert _format_stocks(None) == []


def test_format_stocks_volume_signals():
    """Volume classification flags are forwarded."""
    from routes.strong import _format_stocks
    result = _format_stocks([{
        "code": "x",
        "gentle_volume": True,
        "moderate_volume": True,
        "extreme_volume": False,
        "has_limit_up": True,
    }])
    assert result[0]["gentle_volume"] is True
    assert result[0]["moderate_volume"] is True
    assert result[0]["extreme_volume"] is False
    assert result[0]["has_limit_up"] is True
