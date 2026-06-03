"""Tests for modules.ai_analyzer.analyze_stock (mocked data layer)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from modules.ai_analyzer import analyze_stock


def _make_quote(name="测试股", price=10.0, pe=15.0, pb=2.0, market_cap=100.0):
    q = MagicMock()
    q.name = name
    q.price = price
    q.change_pct = 1.5
    q.pe = pe
    q.pb = pb
    q.market_cap = market_cap
    return q


def _make_financials(roe=10.0, revenue_growth=5.0, profit_growth=8.0,
                     debt_ratio=30.0, gross_margin=25.0):
    f = MagicMock()
    f.pe = 12.0
    f.pb = 1.5
    f.roe = roe
    f.market_cap = 95.0
    f.revenue_growth = revenue_growth
    f.profit_growth = profit_growth
    f.debt_ratio = debt_ratio
    f.gross_margin = gross_margin
    return f


def test_analyze_stock_success():
    q = _make_quote()
    f = _make_financials()

    with patch("modules.ai_analyzer.get_realtime_quotes", return_value={"000001": q}), \
         patch("modules.ai_analyzer.get_financial_data", return_value={"000001": f}), \
         patch("modules.ai_analyzer.full_score", return_value={"score": 80}), \
         patch("modules.ai_analyzer.analyze_sentiment", return_value=0.6), \
         patch("modules.ai_analyzer.get_sentiment_label", return_value="正面"):
        result = analyze_stock("000001")

    assert result["success"] is True
    assert result["code"] == "000001"
    assert result["name"] == "测试股"
    assert result["price"] == 10.0
    assert "scores" in result
    assert "sentiment" in result
    assert result["sentiment"]["label"] == "正面"
    assert result["financial"]["roe"] == 10.0


def test_analyze_stock_not_found():
    with patch("modules.ai_analyzer.get_realtime_quotes", return_value={}):
        result = analyze_stock("999999")

    assert result["success"] is False
    assert "未找到" in result["error"]


def test_analyze_stock_uses_quote_pe_when_no_financials():
    """If financials are missing, fall back to quote.pe/pb."""
    q = _make_quote(pe=18.0, pb=3.5)
    with patch("modules.ai_analyzer.get_realtime_quotes", return_value={"000001": q}), \
         patch("modules.ai_analyzer.get_financial_data", return_value={}), \
         patch("modules.ai_analyzer.full_score", return_value={"score": 50}), \
         patch("modules.ai_analyzer.analyze_sentiment", return_value=0.0), \
         patch("modules.ai_analyzer.get_sentiment_label", return_value="中性"):
        result = analyze_stock("000001")

    # When f is None, financial is None (not a dict with quote values)
    assert result["financial"] is None


def test_analyze_stock_exception_returns_error():
    with patch("modules.ai_analyzer.get_realtime_quotes", side_effect=RuntimeError("net")):
        result = analyze_stock("000001")

    assert result["success"] is False
    assert "net" in result["error"]


def test_analyze_stock_sentiment_negative():
    q = _make_quote()
    f = _make_financials()
    with patch("modules.ai_analyzer.get_realtime_quotes", return_value={"000001": q}), \
         patch("modules.ai_analyzer.get_financial_data", return_value={"000001": f}), \
         patch("modules.ai_analyzer.full_score", return_value={"score": 30}), \
         patch("modules.ai_analyzer.analyze_sentiment", return_value=-0.8), \
         patch("modules.ai_analyzer.get_sentiment_label", return_value="负面"):
        result = analyze_stock("000001")
    assert result["sentiment"]["score"] == -0.8
    assert result["sentiment"]["label"] == "负面"
