"""评分模块单元测试"""

import pytest
from modules.scoring import (
    calculate_value_score,
    calculate_growth_score,
    calculate_quality_score,
    calculate_momentum_score,
    quick_score,
    rank_stocks,
    _pe_score,
    _pb_score,
    _roe_score,
    _market_cap_score,
)
from modules.models import StockQuote, FinancialData


class TestPEScore:
    def test_pe_in_sector_range(self):
        score = _pe_score(6, "银行")
        assert score >= 20

    def test_pe_negative(self):
        score = _pe_score(-5)
        assert score <= 5

    def test_pe_zero(self):
        score = _pe_score(0)
        assert score <= 5

    def test_pe_very_high(self):
        score = _pe_score(200)
        assert score <= 10

    def test_pe_moderate(self):
        score = _pe_score(15)
        assert 10 <= score <= 25


class TestPBScore:
    def test_pb_good_range(self):
        score = _pb_score(1.5)
        assert score >= 20

    def test_pb_negative(self):
        score = _pb_score(-1)
        assert score <= 5

    def test_pb_very_high(self):
        score = _pb_score(20)
        assert score <= 10


class TestROEScore:
    def test_roe_excellent(self):
        score = _roe_score(25)
        assert score >= 22

    def test_roe_poor(self):
        score = _roe_score(-5)
        assert score <= 5

    def test_roe_zero(self):
        score = _roe_score(0)
        assert score <= 5


class TestMarketCapScore:
    def test_large_cap(self):
        score = _market_cap_score(1000)
        assert score >= 22

    def test_small_cap(self):
        score = _market_cap_score(5)
        assert score <= 10

    def test_zero_cap(self):
        score = _market_cap_score(0)
        assert score <= 5


class TestValueScore:
    def test_good_stock(self):
        score = calculate_value_score(pe=12, pb=1.5, roe=18, market_cap=200)
        assert score >= 60

    def test_bad_stock(self):
        score = calculate_value_score(pe=-5, pb=-1, roe=-10, market_cap=5)
        assert score <= 30

    def test_sector_adjusted(self):
        score_bank = calculate_value_score(pe=6, pb=0.8, roe=12, market_cap=500, sector="银行")
        score_generic = calculate_value_score(pe=6, pb=0.8, roe=12, market_cap=500)
        assert score_bank >= score_generic


class TestGrowthScore:
    def test_high_growth(self):
        score = calculate_growth_score(revenue_growth=30, profit_growth=40, roe=18)
        assert score >= 70

    def test_declining(self):
        score = calculate_growth_score(revenue_growth=-20, profit_growth=-30, roe=2)
        assert score <= 30


class TestQualityScore:
    def test_high_quality(self):
        score = calculate_quality_score(debt_ratio=25, gross_margin=50, roe=15)
        assert score >= 70

    def test_low_quality(self):
        score = calculate_quality_score(debt_ratio=80, gross_margin=5, roe=-5)
        assert score <= 30


class TestMomentumScore:
    def test_moderate_momentum(self):
        score = calculate_momentum_score(change_pct=2.5, turnover=3.5, amount=8000)
        assert 40 <= score <= 100

    def test_extreme_up(self):
        score = calculate_momentum_score(change_pct=10, turnover=15, amount=20000)
        assert score >= 30


class TestQuickScore:
    def test_good_stock_passes(self):
        q = StockQuote(
            code="600519", name="贵州茅台", price=1800, change_pct=2.0,
            volume=50000, amount=9000000, turnover=3.5, pe=30, pb=10,
            market_cap=22000, high=1810, low=1790, open=1795, prev_close=1764,
        )
        score = quick_score(q)
        assert score >= 40

    def test_bad_stock_fails(self):
        q = StockQuote(
            code="000001", name="某股", price=2, change_pct=-5,
            volume=1000, amount=200, turnover=0.1, pe=-10, pb=-1,
            market_cap=2, high=3, low=1.5, open=2.5, prev_close=2.1,
        )
        score = quick_score(q)
        assert score < 40


class TestRankStocks:
    def test_ranking(self):
        stocks = [
            {"code": "A", "name": "A", "total_score": 80},
            {"code": "B", "name": "B", "total_score": 60},
            {"code": "C", "name": "C", "total_score": 90},
        ]
        ranked = rank_stocks(stocks)
        assert ranked[0]["rank"] == 1
        assert ranked[0]["code"] == "C"
        assert ranked[-1]["rank"] == 3


class TestStockQuoteModel:
    def test_is_st(self):
        q = StockQuote(code="000001", name="*ST某某", price=1, change_pct=0,
                       volume=0, amount=0, turnover=0, pe=0, pb=0, market_cap=0,
                       high=1, low=1, open=1, prev_close=1)
        assert q.is_st is True

    def test_is_beijing(self):
        q = StockQuote(code="830001", name="北交所股", price=1, change_pct=0,
                       volume=0, amount=0, turnover=0, pe=0, pb=0, market_cap=0,
                       high=1, low=1, open=1, prev_close=1)
        assert q.is_beijing is True

    def test_is_b_share(self):
        q = StockQuote(code="200001", name="B股", price=1, change_pct=0,
                       volume=0, amount=0, turnover=0, pe=0, pb=0, market_cap=0,
                       high=1, low=1, open=1, prev_close=1)
        assert q.is_b_share is True

    def test_is_eligible(self):
        q = StockQuote(code="600519", name="贵州茅台", price=1800, change_pct=0,
                       volume=0, amount=0, turnover=0, pe=0, pb=0, market_cap=0,
                       high=1, low=1, open=1, prev_close=1)
        assert q.is_eligible is True

    def test_filter_eligible(self):
        from modules.models import filter_eligible_stocks
        quotes = {
            "600519": StockQuote(code="600519", name="贵州茅台", price=1800, change_pct=0,
                                 volume=0, amount=0, turnover=0, pe=0, pb=0, market_cap=0,
                                 high=1, low=1, open=1, prev_close=1),
            "000001": StockQuote(code="000001", name="*ST某某", price=1, change_pct=0,
                                 volume=0, amount=0, turnover=0, pe=0, pb=0, market_cap=0,
                                 high=1, low=1, open=1, prev_close=1),
        }
        filtered = filter_eligible_stocks(quotes)
        assert "600519" in filtered
        assert "000001" not in filtered


class TestNewsSentiment:
    def test_positive(self):
        from modules.news import analyze_sentiment, get_sentiment_label
        score = analyze_sentiment("公司业绩大涨，涨停突破新高")
        assert score > 0
        assert get_sentiment_label(score) == "利好"

    def test_negative(self):
        from modules.news import analyze_sentiment, get_sentiment_label
        score = analyze_sentiment("公司暴雷暴跌，利空消息")
        assert score < 0
        assert get_sentiment_label(score) == "利空"

    def test_neutral(self):
        from modules.news import analyze_sentiment, get_sentiment_label
        score = analyze_sentiment("公司正常运营")
        assert get_sentiment_label(score) == "中性"

    def test_empty(self):
        from modules.news import analyze_sentiment
        assert analyze_sentiment("") == 0.0
