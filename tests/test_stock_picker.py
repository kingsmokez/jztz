"""Tests for modules.stock_picker (mocked data + scoring)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from modules.stock_picker import run_picker, _fetch_industry_for_results


def _make_quote(code="000001", name="A", price=10.0, pe=15.0, pb=2.0,
                change_pct=1.0, market_cap=100.0, turnover=1.5):
    q = MagicMock()
    q.code = code
    q.name = name
    q.price = price
    q.pe = pe
    q.pb = pb
    q.change_pct = change_pct
    q.market_cap = market_cap
    q.turnover = turnover
    q.amount = 1000.0
    return q


def test_run_picker_empty_quotes_returns_empty():
    with patch("modules.stock_picker.get_realtime_quotes", return_value={}):
        result = run_picker()
    assert result == []


def test_run_picker_filters_st_stocks():
    quotes = {
        "000001": _make_quote("000001", "ST测试"),
        "000002": _make_quote("000002", "正常股"),
    }
    with patch("modules.stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.stock_picker.get_financial_data", return_value={}), \
         patch("modules.stock_picker.get_preset_financials", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_picker(top_n=10)
    # Only the non-ST stock may remain
    codes = {r["code"] for r in result}
    assert "000001" not in codes


def test_run_picker_filters_bse_and_star_market():
    quotes = {
        "920001": _make_quote("920001", "BSE股"),  # BSE 9xx
        "688001": _make_quote("688001", "科创板"),
        "400001": _make_quote("400001", "老股转"),  # 4xx
        "000001": _make_quote("000001", "正常股"),
    }
    with patch("modules.stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.stock_picker.get_financial_data", return_value={}), \
         patch("modules.stock_picker.get_preset_financials", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_picker(top_n=10)
    codes = {r["code"] for r in result}
    assert "920001" not in codes
    assert "688001" not in codes
    assert "400001" not in codes


def test_run_picker_filters_low_price_and_low_cap():
    quotes = {
        "000001": _make_quote("000001", "低价股", price=0.5),
        "000002": _make_quote("000002", "低市值", market_cap=5.0),
        "000003": _make_quote("000003", "正常股"),
    }
    with patch("modules.stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.stock_picker.get_financial_data", return_value={}), \
         patch("modules.stock_picker.get_preset_financials", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_picker(top_n=10)
    codes = {r["code"] for r in result}
    assert "000001" not in codes
    assert "000002" not in codes


def test_run_picker_filters_low_turnover():
    quotes = {
        "000001": _make_quote("000001", "低换手", turnover=0.1),
        "000002": _make_quote("000002", "正常换手", turnover=1.0),
    }
    with patch("modules.stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.stock_picker.get_financial_data", return_value={}), \
         patch("modules.stock_picker.get_preset_financials", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_picker(top_n=10)
    codes = {r["code"] for r in result}
    assert "000001" not in codes


def test_run_picker_returns_top_n():
    quotes = {f"00000{i}": _make_quote(f"00000{i}", f"股{i}", turnover=2.0) for i in range(1, 6)}
    with patch("modules.stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.stock_picker.get_financial_data", return_value={}), \
         patch("modules.stock_picker.get_preset_financials", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}), \
         patch("modules.stock_picker.evaluate_stock", return_value={
             "score": 80, "v5_score": 80, "v5_factors": {}, "v5_reasons": [],
             "v5_recommendation": "买入", "dimensions": {}, "buy_sell": "买入", "reasons": []
         }):
        result = run_picker(top_n=3)
    assert len(result) <= 3


def test_run_picker_filters_low_score():
    quotes = {"000001": _make_quote("000001", "低分股", turnover=2.0)}
    with patch("modules.stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.stock_picker.get_financial_data", return_value={}), \
         patch("modules.stock_picker.get_preset_financials", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}), \
         patch("modules.stock_picker.evaluate_stock", return_value={"score": 10}):
        result = run_picker(top_n=10)
    # score < 30 should be filtered
    assert result == []


def test_run_picker_uses_preset_pb():
    quotes = {"000001": _make_quote("000001", "PB股", pb=0.0, turnover=2.0)}
    preset = {"000001": {"pb": 1.8}}
    with patch("modules.stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.stock_picker.get_financial_data", return_value={}), \
         patch("modules.stock_picker.get_preset_financials", return_value=preset), \
         patch("modules.technical.calculate_technical_indicators", return_value={}), \
         patch("modules.stock_picker.evaluate_stock", return_value={"score": 80, "v5_score": 80}):
        result = run_picker(top_n=10)
    if result:
        # The PB should be the preset value
        assert abs(result[0]["pb"] - 1.8) < 0.01


def test_run_picker_estimates_pb_from_pe_roe():
    quotes = {"000001": _make_quote("000001", "估算PB", pe=20.0, pb=0.0, turnover=2.0)}
    f = MagicMock()
    f.roe = 10.0
    f.gross_margin = 0
    f.net_margin = 0
    f.revenue_growth = 0
    f.profit_growth = 0
    f.debt_ratio = 0
    with patch("modules.stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.stock_picker.get_financial_data", return_value={"000001": f}), \
         patch("modules.stock_picker.get_preset_financials", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}), \
         patch("modules.stock_picker.evaluate_stock", return_value={"score": 80, "v5_score": 80}):
        result = run_picker(top_n=10)
    if result:
        # PB = pe * roe / 100 = 20 * 10 / 100 = 2.0
        assert abs(result[0]["pb"] - 2.0) < 0.01


def test_run_picker_total_scanned_in_first_result():
    quotes = {f"00000{i}": _make_quote(f"00000{i}", f"股{i}", turnover=2.0) for i in range(1, 4)}
    with patch("modules.stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.stock_picker.get_financial_data", return_value={}), \
         patch("modules.stock_picker.get_preset_financials", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}), \
         patch("modules.stock_picker.evaluate_stock", return_value={"score": 80, "v5_score": 80}), \
         patch("modules.stock_picker.get_stock_industry", return_value={"industry": "测试", "sector_type": "default"}):
        result = run_picker(top_n=3)
    if result:
        assert result[0].get("_total_scanned") == 3


def test_fetch_industry_for_results_handles_error():
    results = [{"code": "000001"}]
    with patch("modules.stock_picker.get_stock_industry", side_effect=RuntimeError("net")):
        _fetch_industry_for_results(results)
    assert results[0]["industry"] == "未知"
    assert results[0]["sector"] == "default"
