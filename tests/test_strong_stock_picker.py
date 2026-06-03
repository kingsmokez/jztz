"""Tests for modules.strong_stock_picker (mocked data layer)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from modules.strong_stock_picker import run_strong_stock_picker, _fetch_industry_for_results


def _make_quote(name="股", price=10.0, change_pct=2.0, market_cap=100.0,
                turnover=2.0, pe=15.0, pb=2.0):
    q = MagicMock()
    q.name = name
    q.price = price
    q.change_pct = change_pct
    q.market_cap = market_cap
    q.turnover = turnover
    q.pe = pe
    q.pb = pb
    q.amount = 500.0
    return q


def test_run_strong_picker_empty_quotes():
    with patch("modules.strong_stock_picker.get_realtime_quotes", return_value={}):
        result = run_strong_stock_picker()
    assert result == []


def test_run_strong_picker_filters_st():
    quotes = {
        "000001": _make_quote("ST测试"),
        "000002": _make_quote("正常股"),
    }
    with patch("modules.strong_stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.strong_stock_picker.get_financial_data", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_strong_stock_picker()
    codes = {r["code"] for r in result}
    assert "000001" not in codes


def test_run_strong_picker_filters_star_market():
    quotes = {
        "688001": _make_quote("科创板"),
        "920001": _make_quote("BSE股"),
        "000001": _make_quote("正常股"),
    }
    with patch("modules.strong_stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.strong_stock_picker.get_financial_data", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_strong_stock_picker()
    codes = {r["code"] for r in result}
    assert "688001" not in codes
    assert "920001" not in codes


def test_run_strong_picker_filters_limit_up():
    """Stocks with change_pct > 9.5% are filtered (already at limit up)."""
    quotes = {
        "000001": _make_quote("涨停股", change_pct=10.0),
        "000002": _make_quote("正常股", change_pct=3.0),
    }
    with patch("modules.strong_stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.strong_stock_picker.get_financial_data", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_strong_stock_picker()
    codes = {r["code"] for r in result}
    assert "000001" not in codes


def test_run_strong_picker_returns_results_with_template_fields():
    quotes = {"000001": _make_quote("正常股", change_pct=3.0, turnover=2.0)}
    with patch("modules.strong_stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.strong_stock_picker.get_financial_data", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_strong_stock_picker()
    if result:
        item = result[0]
        # Template-required fields
        for k in ("code", "name", "price", "score"):
            assert k in item, f"missing {k!r}"


def test_run_strong_picker_top_n_limit():
    quotes = {f"00000{i}": _make_quote(f"股{i}", change_pct=3.0, turnover=2.0)
              for i in range(1, 6)}
    with patch("modules.strong_stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.strong_stock_picker.get_financial_data", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_strong_stock_picker(top_n=2)
    assert len(result) <= 2


def test_fetch_industry_for_results_handles_error():
    results = [{"code": "000001"}]
    with patch("modules.strong_stock_picker.get_stock_industry", side_effect=RuntimeError("net")):
        _fetch_industry_for_results(results)
    assert results[0]["industry"] == "未知"
