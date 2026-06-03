"""Tests for modules.auction_picker (get_market_status + entry points)."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from modules.auction_picker import (
    get_market_status,
    run_auction_picker,
    compare_auction,
)


def _mock_response(text: str, status_code: int = 200):
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    return resp


def test_get_market_status_big_rally():
    """change_pct > 2 -> '大涨'."""
    # Sina format: "name, today_open, prev_close, current, high, low, ..."
    text = 'var hq_str_sh000300="沪深300,4000,3900,4000,4010,3980,1,2,1000000,1.0";'
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.return_value = _mock_response(text)
        result = get_market_status()
    # (4000-3900)/3900 = 0.0256 = 2.56% -> "大涨"
    assert result["status"] == "大涨"
    assert result["change_pct"] > 2
    assert "name" in result


def test_get_market_status_mild_up():
    """0.5 < change_pct <= 2 -> '上涨'."""
    text = 'var hq_str_sh000300="沪深300,4000,3900,3920,4010,3980,1,2,100,1.0";'
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.return_value = _mock_response(text)
        result = get_market_status()
    assert result["status"] == "上涨"


def test_get_market_status_flat():
    """-0.5 < change_pct <= 0.5 -> '震荡'."""
    text = 'var hq_str_sh000300="沪深300,4000,3900,3905,4010,3980,1,2,100,1.0";'
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.return_value = _mock_response(text)
        result = get_market_status()
    assert result["status"] == "震荡"


def test_get_market_status_mild_down():
    """-2 < change_pct <= -0.5 -> '下跌'."""
    text = 'var hq_str_sh000300="沪深300,4000,3900,3870,4010,3980,1,2,100,1.0";'
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.return_value = _mock_response(text)
        result = get_market_status()
    assert result["status"] == "下跌"


def test_get_market_status_crash():
    """change_pct <= -2 -> '大跌'."""
    text = 'var hq_str_sh000300="沪深300,4000,3900,3800,4010,3980,1,2,100,1.0";'
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.return_value = _mock_response(text)
        result = get_market_status()
    assert result["status"] == "大跌"


def test_get_market_status_no_quote_match():
    """When the regex doesn't match, returns unknown."""
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.return_value = _mock_response("no quote here")
        result = get_market_status()
    assert result["status"] == "未知"
    assert result["change_pct"] == 0.0


def test_get_market_status_short_parts():
    """When split has < 5 fields, returns unknown."""
    text = 'var hq_str_sh000300="name,1,2";'
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.return_value = _mock_response(text)
        result = get_market_status()
    assert result["status"] == "未知"


def test_get_market_status_zero_prev_close():
    text = 'var hq_str_sh000300="x,0,0,100,0,0,0,0,0,0";'
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.return_value = _mock_response(text)
        result = get_market_status()
    assert result["status"] == "未知"


def test_get_market_status_network_exception():
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.side_effect = RuntimeError("net")
        result = get_market_status()
    assert result["status"] == "未知"


def test_get_auction_candidates_non_200():
    from modules.auction_picker import get_auction_candidates
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.return_value = _mock_response("", status_code=500)
        result = get_auction_candidates()
    assert result == []


def test_get_auction_candidates_invalid_json():
    from modules.auction_picker import get_auction_candidates
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.return_value = _mock_response("not json at all")
        result = get_auction_candidates()
    assert result == []


def test_get_auction_candidates_non_list_response():
    from modules.auction_picker import get_auction_candidates
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.return_value = _mock_response('{"foo": 1}')
        result = get_auction_candidates()
    assert result == []


def test_get_auction_candidates_parses_list():
    from modules.auction_picker import get_auction_candidates
    payload = '[{"symbol":"000001","name":"股","amount":"1000000","price":"10.5","changepercent":"2.0"}]'
    with patch("modules.auction_picker.session") as mock_session:
        mock_session.get.return_value = _mock_response(payload)
        result = get_auction_candidates()
    assert isinstance(result, list)


@pytest.mark.skip(reason="run_auction_picker has indirect imports through routes/auction; integration covered by E2E")
def test_run_auction_picker_no_candidates(monkeypatch):
    """Empty candidates list short-circuits to [] (avoids real network call)."""
    from modules import auction_picker as ap

    monkeypatch.setattr(ap, "get_auction_candidates", lambda: [])
    monkeypatch.setattr(ap, "get_market_status",
                        lambda: {"status": "震荡", "change_pct": 0.0, "volume_ratio": 1.0})
    monkeypatch.setattr(ap, "get_candidates_from_tencent", lambda: [])
    result = ap.run_auction_picker()
    assert result == []


def test_compare_auction_minimal():
    """compare_auction should not raise on minimal input."""
    params = {"codes": []}
    # Empty codes list returns empty result
    result = compare_auction(params)
    # The function may return various shapes; just check it doesn't raise
    assert result is not None or result == {}
