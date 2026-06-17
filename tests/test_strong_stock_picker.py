"""Tests for modules.strong_stock_picker (mocked data layer)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from modules.strong_stock_picker import run_strong_stock_picker, _fetch_industry_for_results


def _make_quote(name="股", price=10.0, change_pct=2.0, market_cap=100.0,
                turnover=2.0, pe=15.0, pb=2.0, high=0.0, prev_close=0.0,
                volume_ratio=0.0, low=0.0):
    q = MagicMock()
    q.name = name
    q.price = price
    q.change_pct = change_pct
    q.market_cap = market_cap
    q.turnover = turnover
    q.pe = pe
    q.pb = pb
    q.amount = 500.0
    q.high = high
    q.prev_close = prev_close
    q.volume_ratio = volume_ratio
    q.low = low
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


def test_run_strong_picker_filters_bse_only():
    """科创板(688xxx) should now be included with correct 20% threshold;
    only 北交所(9xxxxx) is excluded."""
    quotes = {
        "688001": _make_quote("科创板", change_pct=3.0),
        "920001": _make_quote("BSE股"),
        "000001": _make_quote("正常股", change_pct=3.0),
    }
    with patch("modules.strong_stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.strong_stock_picker.get_financial_data", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_strong_stock_picker()
    codes = {r["code"] for r in result}
    assert "688001" in codes  # 科创板 now included with 20% threshold
    assert "920001" not in codes  # 北交所 still excluded


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
    mock_env = MagicMock()
    mock_env.can_pick.return_value = True
    mock_env.adjusted_top_n.side_effect = lambda n: n
    with patch("modules.strong_stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.strong_stock_picker.get_financial_data", return_value={}), \
         patch("modules.strong_stock_picker.get_market_env", return_value=mock_env), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_strong_stock_picker(top_n=2)
    assert len(result) <= 2


def test_fetch_industry_for_results_handles_error():
    results = [{"code": "000001"}]
    with patch("modules.strong_stock_picker.get_stock_industry", side_effect=RuntimeError("net")):
        _fetch_industry_for_results(results)
    assert results[0]["industry"] == "未知"

def test_gem_stock_15pct_passes_pre_filter():
    """创业板(300xxx) stock with 15% change should pass pre-filter (20% limit)."""
    quotes = {
        "300001": _make_quote("创业板", change_pct=15.0),
        "000001": _make_quote("主板", change_pct=3.0),
    }
    with patch("modules.strong_stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.strong_stock_picker.get_financial_data", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_strong_stock_picker()
    codes = {r["code"] for r in result}
    assert "300001" in codes  # 创业板 15% should pass


def test_gem_stock_20pct_filtered_out():
    """创业板(300xxx) stock with 20% change should be filtered out (at limit)."""
    quotes = {
        "300001": _make_quote("创业板涨停", change_pct=20.0),
        "000001": _make_quote("主板", change_pct=3.0),
    }
    with patch("modules.strong_stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.strong_stock_picker.get_financial_data", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_strong_stock_picker()
    codes = {r["code"] for r in result}
    assert "300001" not in codes  # 创业板 20% should be filtered


def test_star_market_18pct_passes_pre_filter():
    """科创板(688xxx) stock with 18% change should pass pre-filter (20% limit)."""
    quotes = {
        "688001": _make_quote("科创板", change_pct=18.0),
        "000001": _make_quote("主板", change_pct=3.0),
    }
    with patch("modules.strong_stock_picker.get_realtime_quotes", return_value=quotes), \
         patch("modules.strong_stock_picker.get_financial_data", return_value={}), \
         patch("modules.technical.calculate_technical_indicators", return_value={}):
        result = run_strong_stock_picker()
    codes = {r["code"] for r in result}
    assert "688001" in codes  # 科创板 18% should pass


def test_get_limit_threshold():
    """Test _get_limit_threshold returns correct values for different code prefixes."""
    from modules.strong_stock_picker import _get_limit_threshold
    
    # 创业板 300xxx
    assert _get_limit_threshold("300001") == 19.5
    assert _get_limit_threshold("301001") == 19.5
    
    # 科创板 688xxx
    assert _get_limit_threshold("688001") == 19.5
    
    # 主板
    assert _get_limit_threshold("000001") == 9.5
    assert _get_limit_threshold("600001") == 9.5
    assert _get_limit_threshold("601001") == 9.5
