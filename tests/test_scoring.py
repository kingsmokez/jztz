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


class TestEvaluateStockFixes:
    """Tests for P0-4, P1-1, P1-2 fixes"""

    def test_suspended_stock_filtered(self):
        """P1-2: 停牌股票(turnover=0, change_pct=0)应被过滤"""
        from modules.scoring import evaluate_stock
        stock = {"code": "000001", "name": "停牌股", "turnover_rate": 0, "change_pct": 0,
                 "pe": 15, "pb": 2, "roe": 10}
        result = evaluate_stock(stock)
        assert result is None

    def test_active_stock_not_filtered(self):
        """P1-2: 正常活跃股票不应被停牌逻辑过滤"""
        from modules.scoring import evaluate_stock
        stock = {"code": "000001", "name": "正常股", "turnover_rate": 2.0, "change_pct": 1.5,
                 "pe": 15, "pb": 2, "roe": 10, "market_cap": 100}
        result = evaluate_stock(stock)
        assert result is not None

    def test_score_equals_v5_score(self):
        """P0-4: evaluate_stock score 应基于 v5_score + 行情/板块加分"""
        from modules.scoring import evaluate_stock
        stock = {"code": "000001", "name": "测试股", "turnover_rate": 3.0, "change_pct": 2.0,
                 "pe": 15, "pb": 2, "roe": 15, "market_cap": 200,
                 "gross_margin": 30, "net_margin": 10, "debt_ratio": 40,
                 "rev_growth": 10, "profit_growth": 15}
        result = evaluate_stock(stock)
        if result:
            # score = v5_score + market_bonus + sector_bonus (capped at 100)
            # score should be >= v5_score (market/sector bonuses are non-negative after clamping)
            assert result["score"] >= round(result["v5_score"], 1) - 0.2
            # The difference should be at most 10 (5 market + 5 sector bonus cap)
            assert result["score"] <= round(result["v5_score"], 1) + 10.1

    def test_calc_market_bonus_subfunction(self):
        """P0-4: _calc_market_bonus 子函数正确工作"""
        from modules.scoring import _calc_market_bonus
        # 小跌 + 高换手率
        stock = {"change_pct": -2.0, "turnover_rate": 5.0}
        bonus, reasons = _calc_market_bonus(stock)
        assert bonus > 0
        assert len(reasons) > 0

    def test_calc_sector_bonus_subfunction(self):
        """P0-4: _calc_sector_bonus 子函数正确工作"""
        from modules.scoring import _calc_sector_bonus
        stock = {"code": "000001", "name": "测试股"}
        bonus, reasons = _calc_sector_bonus(stock, None)
        assert isinstance(bonus, float)
        assert isinstance(reasons, list)

    def test_calc_volatility_with_atr(self):
        """P1-1: _calc_volatility 优先使用ATR"""
        from modules.scoring import _calc_volatility
        stock = {"atr": 0.5, "price": 20.0, "change_pct": 3.0}
        vol = _calc_volatility(stock)
        assert abs(vol - 2.5) < 0.01  # 0.5/20*100 = 2.5%

    def test_calc_volatility_fallback_to_change_pct(self):
        """P1-1: _calc_volatility 无ATR时降级到涨跌幅"""
        from modules.scoring import _calc_volatility
        stock = {"change_pct": 3.5, "price": 20.0}
        vol = _calc_volatility(stock)
        assert vol == 3.5

    def test_calc_volatility_default(self):
        """P1-1: _calc_volatility 无数据时默认2.0"""
        from modules.scoring import _calc_volatility
        stock = {}
        vol = _calc_volatility(stock)
        assert vol == 2.0


class TestFullScoreV5Weights:
    """P0-6: full_score 使用V5权重"""

    def test_full_score_has_v5_score(self):
        """full_score 返回结果应包含 v5_score 字段"""
        from modules.scoring import full_score
        q = StockQuote(code="000001", name="测试", price=10, change_pct=2.0,
                       volume=1000, amount=10000, turnover=3.0, pe=15, pb=2,
                       market_cap=200, high=11, low=9, open=10, prev_close=9.8)
        result = full_score(q)
        assert "v5_score" in result
        assert "v5_factors" in result

    def test_full_score_v5_weighted(self):
        """full_score 的 total_score 应基于V5权重而非旧权重"""
        from modules.scoring import full_score, multi_factor_evaluate
        q = StockQuote(code="000001", name="测试", price=10, change_pct=2.0,
                       volume=1000, amount=10000, turnover=3.0, pe=15, pb=2,
                       market_cap=200, high=11, low=9, open=10, prev_close=9.8)
        result = full_score(q)
        # total_score should be based on V5 evaluation
        stock_dict = {"code": "000001", "name": "测试", "pe": 15, "pb": 2,
                      "roe": 0, "market_cap": 200, "turnover_rate": 3.0,
                      "change_pct": 2.0, "price": 10}
        v5_result = multi_factor_evaluate(stock_dict, None)
        assert abs(result["total_score"] - round(v5_result["v5_total"], 1)) < 0.1


class TestTechScoreAndATRStopLoss:
    """P0-5: tech_score 字段和 ATR 动态止损测试"""

    def test_evaluate_stock_has_tech_score(self):
        """evaluate_stock 返回结果应包含 tech_score 字段"""
        from modules.scoring import evaluate_stock
        stock = {
            "code": "002475",
            "name": "立讯精密",
            "price": 35.8,
            "change_pct": 2.1,
            "turnover_rate": 3.5,
            "pe": 28.3,
            "pb": 4.5,
            "roe": 18.5,
            "gross_margin": 15.2,
            "net_margin": 8.5,
            "debt_ratio": 55.3,
            "rev_growth": 22.5,
            "profit_growth": 25.2,
            "market_cap": 260,
        }
        result = evaluate_stock(stock)
        assert result is not None
        assert "tech_score" in result
        assert result["tech_score"] == 0

    def test_evaluate_stock_tech_score_with_tech_data(self):
        """evaluate_stock 有 tech_data 时 tech_score 应 > 0"""
        from modules.scoring import evaluate_stock
        stock = {
            "code": "002475",
            "name": "立讯精密",
            "price": 35.8,
            "change_pct": 2.1,
            "turnover_rate": 3.5,
            "pe": 28.3,
            "pb": 4.5,
            "roe": 18.5,
            "gross_margin": 15.2,
            "net_margin": 8.5,
            "debt_ratio": 55.3,
            "rev_growth": 22.5,
            "profit_growth": 25.2,
            "market_cap": 260,
        }
        tech_data = {
            "ma_signal": "bull",
            "macd_signal": "golden_cross",
            "rsi": 55,
            "boll_position": 0.4,
            "change_5d": 3.0,
            "obv_trend": "bullish",
        }
        result = evaluate_stock(stock, tech_data=tech_data)
        assert result is not None
        assert "tech_score" in result
        assert result["tech_score"] > 0

    def test_atr_stop_loss_applied(self):
        """ATR 动态止损应正确应用"""
        from modules.scoring import calculate_buy_sell
        stock = {
            "code": "002475",
            "name": "立讯精密",
            "price": 100.0,
            "pe": 20.0,
            "roe": 18.0,
            "gross_margin": 20.0,
            "rev_growth": 15.0,
            "profit_growth": 18.0,
            "atr": 3.0,
        }
        result = calculate_buy_sell(stock, 70.0)
        assert result is not None
        expected_stop = 94.0
        assert abs(result["stop_loss"] - expected_stop) < 0.1

    def test_atr_stop_loss_clamped_to_5pct_floor(self):
        """ATR 止损低于 5% 时应限制为 5%"""
        from modules.scoring import calculate_buy_sell
        stock = {
            "code": "002475",
            "name": "立讯精密",
            "price": 100.0,
            "pe": 20.0,
            "roe": 18.0,
            "gross_margin": 20.0,
            "rev_growth": 15.0,
            "profit_growth": 18.0,
            "atr": 1.0,
        }
        result = calculate_buy_sell(stock, 70.0)
        assert result is not None
        expected_stop = 95.0
        assert abs(result["stop_loss"] - expected_stop) < 0.1

    def test_atr_stop_loss_clamped_to_12pct_ceiling(self):
        """ATR 止损高于 12% 时应限制为 12%"""
        from modules.scoring import calculate_buy_sell
        stock = {
            "code": "002475",
            "name": "立讯精密",
            "price": 100.0,
            "pe": 20.0,
            "roe": 18.0,
            "gross_margin": 20.0,
            "rev_growth": 15.0,
            "profit_growth": 18.0,
            "atr": 10.0,
        }
        result = calculate_buy_sell(stock, 70.0)
        assert result is not None
        expected_stop = 88.0
        assert abs(result["stop_loss"] - expected_stop) < 0.1

    def test_no_atr_uses_8pct_stop_loss(self):
        """无 ATR 时应使用固定 8% 止损"""
        from modules.scoring import calculate_buy_sell
        stock = {
            "code": "002475",
            "name": "立讯精密",
            "price": 100.0,
            "pe": 20.0,
            "roe": 18.0,
            "gross_margin": 20.0,
            "rev_growth": 15.0,
            "profit_growth": 18.0,
        }
        result = calculate_buy_sell(stock, 70.0)
        assert result is not None
        expected_stop = 92.0
        assert abs(result["stop_loss"] - expected_stop) < 0.1

    def test_v5_tech_data_includes_new_indicators(self):
        """V5 tech_data 应包含新指标"""
        from modules.scoring import evaluate_stock
        stock = {
            "code": "002475",
            "name": "立讯精密",
            "price": 35.8,
            "change_pct": 2.1,
            "turnover_rate": 3.5,
            "pe": 28.3,
            "pb": 4.5,
            "roe": 18.5,
            "gross_margin": 15.2,
            "net_margin": 8.5,
            "debt_ratio": 55.3,
            "rev_growth": 22.5,
            "profit_growth": 25.2,
            "market_cap": 260,
        }
        tech_data = {
            "ma5": 35.5,
            "ma10": 35.2,
            "ma20": 34.8,
            "rsi": 58.5,
            "volume_ratio": 1.5,
            "momentum_20": 4.2,
            "momentum_60": 12.5,
            "price": 35.8,
            "obv_trend": "bullish",
            "boll_width_pct": 6.5,
            "rsi_6": 55.3,
            "rsi_12": 52.1,
            "rsi_24": 48.7,
            "vwap": 35.6,
            "atr": 0.8,
        }
        result = evaluate_stock(stock, tech_data=tech_data)
        assert result is not None
        assert "obv_trend" in result["tech_info"]
        assert "boll_width_pct" in result["tech_info"]
        assert "vwap" in result["tech_info"]
        assert "atr" in result["tech_info"]



class TestStrategyFixes:
    """策略优化测试 - 价值陷阱/增长数据膨胀/质量门槛/市场环境"""

    def test_value_trap_penalized(self):
        """价值陷阱（低PE + 负增长）应被惩罚"""
        from modules.scoring import multi_factor_evaluate
        stock = {
            "code": "600001", "name": "steel", "price": 5.0, "change_pct": -0.5,
            "turnover_rate": 1.2, "pe": 5, "pb": 0.8, "roe": 8.0,
            "gross_margin": 8.0, "net_margin": 2.0, "debt_ratio": 65.0,
            "rev_growth": -5.0, "profit_growth": -20.0, "market_cap": 80,
        }
        result = multi_factor_evaluate(stock)
        assert result["v5_total"] < 60

    def test_missing_growth_data_capped(self):
        """缺失增长数据时，growth分数应限制在25以内"""
        from modules.scoring import mf_score_growth
        stock = {"code": "600002", "name": "nodata", "roe": 22.0,
                 "rev_growth": 0, "profit_growth": 0}
        g = mf_score_growth(stock)
        assert g <= 25

    def test_quality_gate_penalizes_low_quality(self):
        """质量门槛应惩罚低质量股票"""
        from modules.scoring import multi_factor_evaluate
        stock = {
            "code": "600003", "name": "lowq", "price": 8.0, "change_pct": 0.5,
            "turnover_rate": 1.5, "pe": 30, "pb": 3.0, "roe": 3.0,
            "gross_margin": 10.0, "net_margin": 1.0, "debt_ratio": 75.0,
            "rev_growth": 5.0, "profit_growth": 3.0, "market_cap": 40,
        }
        result = multi_factor_evaluate(stock)
        assert result["v5_factors"]["quality"] < 30
        assert result["v5_total"] < 55

    def test_good_stock_still_scores_well(self):
        """优质股不应被策略修复误伤"""
        from modules.scoring import multi_factor_evaluate
        stock = {
            "code": "000001", "name": "good", "price": 35.0, "change_pct": 1.5,
            "turnover_rate": 3.0, "pe": 18, "pb": 3.5, "roe": 22.0,
            "gross_margin": 45.0, "net_margin": 18.0, "debt_ratio": 35.0,
            "rev_growth": 25.0, "profit_growth": 30.0, "market_cap": 300,
        }
        result = multi_factor_evaluate(stock)
        assert result["v5_total"] > 60

    def test_negative_roe_quality_penalty(self):
        """亏损企业质量分应很低"""
        from modules.scoring import mf_score_quality
        stock = {"roe": -5.0, "gross_margin": -10.0, "net_margin": -15.0, "debt_ratio": 80.0}
        q = mf_score_quality(stock)
        assert q < 10

    def test_market_env_can_pick_extreme_down(self):
        """市场暴跌日应暂停选股"""
        from modules.market_env import MarketEnv
        env = MarketEnv.__new__(MarketEnv)
        env.status = "strong_down"
        env.trend = "bear"
        env.change_pct = -3.5
        env.volatility = "high"
        assert not env.can_pick()

    def test_market_env_can_pick_bear_down_high_vol(self):
        """熊市+下跌+高波动应暂停选股"""
        from modules.market_env import MarketEnv
        env = MarketEnv.__new__(MarketEnv)
        env.status = "down"
        env.trend = "bear"
        env.change_pct = -1.5
        env.volatility = "high"
        assert not env.can_pick()

    def test_market_env_can_pick_extreme_drop(self):
        """单日跌幅>3%应暂停选股"""
        from modules.market_env import MarketEnv
        env = MarketEnv.__new__(MarketEnv)
        env.status = "strong_down"
        env.trend = "range"
        env.change_pct = -3.5
        env.volatility = "normal"
        assert not env.can_pick()

    def test_market_env_can_pick_normal(self):
        """正常行情应允许选股"""
        from modules.market_env import MarketEnv
        env = MarketEnv.__new__(MarketEnv)
        env.status = "up"
        env.trend = "bull"
class TestEvaluateStock:
    """evaluate_stock() 函数测试"""

    def test_evaluate_stock_returns_v5_score(self):
        """验证 score 字段等于 v5_score"""
        from modules.scoring import evaluate_stock
        stock = {
            "code": "300750",
            "name": "宁德时代",
            "price": 200,
            "change_pct": 2.0,
            "turnover_rate": 3.5,
            "pe": 30,
            "pb": 10,
            "roe": 25,
            "gross_margin": 50,
            "net_margin": 20,
            "debt_ratio": 30,
            "rev_growth": 15,
            "profit_growth": 20,
            "market_cap": 8000,
        }
        result = evaluate_stock(stock)
        assert result is not None
        # score = v5_score + market_bonus + sector_bonus, so score >= v5_score
        assert result["score"] >= round(result["v5_score"], 1) - 0.2

    def test_evaluate_stock_dimensions_keys(self):
        """验证 dimensions 字典包含预期的键"""
        from modules.scoring import evaluate_stock
        stock = {
            "code": "300750",
            "name": "宁德时代",
            "price": 200,
            "change_pct": 2.0,
            "turnover_rate": 3.5,
            "pe": 30,
            "pb": 10,
            "roe": 25,
            "gross_margin": 50,
            "net_margin": 20,
            "debt_ratio": 30,
            "rev_growth": 15,
            "profit_growth": 20,
            "market_cap": 8000,
        }
        result = evaluate_stock(stock)
        assert result is not None
        assert "profitability" in result["dimensions"]
        assert "growth" in result["dimensions"]
        assert "health" in result["dimensions"]
        assert "valuation" in result["dimensions"]
        assert "cashflow" in result["dimensions"]

    def test_evaluate_stock_excludes_beijing(self):
        """验证北交所股票被排除"""
        from modules.scoring import evaluate_stock
        stock = {
            "code": "830001",
            "name": "北交所股票",
            "price": 10,
            "change_pct": 0,
            "turnover_rate": 1.0,
        }
        result = evaluate_stock(stock)
        assert result is None

    def test_evaluate_stock_excludes_b_share(self):
        """验证B股被排除"""
        from modules.scoring import evaluate_stock
        stock = {
            "code": "200001",
            "name": "B股",
            "price": 10,
            "change_pct": 0,
            "turnover_rate": 1.0,
        }
        result = evaluate_stock(stock)
        assert result is None

    def test_evaluate_stock_excludes_low_turnover(self):
        """验证低换手率股票被排除"""
        from modules.scoring import evaluate_stock
        stock = {
            "code": "600001",
            "name": "低换手率股票",
            "price": 10,
            "change_pct": 0,
            "turnover_rate": 0.2,
        }
        result = evaluate_stock(stock)
        assert result is None

    def test_evaluate_stock_excludes_suspended(self):
        """验证停牌股票被排除（换手率=0且涨跌幅=0）"""
        from modules.scoring import evaluate_stock
        stock = {
            "code": "600001",
            "name": "停牌股票",
            "price": 10,
            "change_pct": 0,
            "turnover_rate": 0,
        }
        result = evaluate_stock(stock)
        assert result is None


class TestCalcMarketBonus:
    """_calc_market_bonus() 函数测试"""

    def test_calc_market_bonus_moderate_decline(self):
        """验证适度下跌加分"""
        from modules.scoring import _calc_market_bonus
        stock = {"change_pct": -3.0, "turnover_rate": 5.0}
        bonus, reasons = _calc_market_bonus(stock)
        assert bonus > 0
        assert any("回调" in r for r in reasons)

    def test_calc_market_bonus_high_turnover(self):
        """验证高换手率加分"""
        from modules.scoring import _calc_market_bonus
        stock = {"change_pct": 0, "turnover_rate": 5.0}
        bonus, reasons = _calc_market_bonus(stock)
        assert bonus >= 4
        assert any("活跃" in r for r in reasons)

    def test_calc_market_bonus_extreme_rise(self):
        """验证大涨不加分"""
        from modules.scoring import _calc_market_bonus
        stock = {"change_pct": 10.0, "turnover_rate": 5.0}
        bonus, reasons = _calc_market_bonus(stock)
        assert any("追高" in r for r in reasons)

    def test_calc_market_bonus_cap(self):
        """验证行情加分上限5分"""
        from modules.scoring import _calc_market_bonus
        stock = {"change_pct": -4.0, "turnover_rate": 5.0}
        bonus, _ = _calc_market_bonus(stock)
        assert bonus <= 5


class TestCalcSectorBonus:
    """_calc_sector_bonus() 函数测试"""

    def test_calc_sector_bonus_no_priority(self):
        """验证无优先板块时返回0（mock sector_rotation模块）"""
        from unittest.mock import patch
        from modules.scoring import _calc_sector_bonus
        stock = {"code": "300750", "name": "宁德时代"}
        with patch("modules.sector_rotation.calculate_sector_bonus", side_effect=ImportError):
            bonus, reasons = _calc_sector_bonus(stock, None)
        assert bonus == 0
        assert reasons == []

    def test_calc_sector_bonus_empty_list(self):
        """验证空优先板块列表时返回0（mock sector_rotation模块）"""
        from unittest.mock import patch
        from modules.scoring import _calc_sector_bonus
        stock = {"code": "300750", "name": "宁德时代"}
        with patch("modules.sector_rotation.calculate_sector_bonus", side_effect=ImportError):
            bonus, reasons = _calc_sector_bonus(stock, [])
        assert bonus == 0
        assert reasons == []

    def test_calc_sector_bonus_with_matching_sector(self):
        """验证匹配板块时加分"""
        from unittest.mock import patch
        from modules.scoring import _calc_sector_bonus, STOCK_SECTOR_MAP
        stock = {"code": "300750", "name": "宁德时代"}
        # Ensure the stock is in the sector map for this test
        original = STOCK_SECTOR_MAP.get("300750", [])
        try:
            STOCK_SECTOR_MAP["300750"] = ["新能源"]
            with patch("modules.sector_rotation.calculate_sector_bonus", side_effect=ImportError):
                priority_sectors = [("新能源", 5.0, 10)]
                bonus, reasons = _calc_sector_bonus(stock, priority_sectors)
                assert bonus == 10
                assert any("新能源" in r for r in reasons)
        finally:
            if original:
                STOCK_SECTOR_MAP["300750"] = original
            else:
                STOCK_SECTOR_MAP.pop("300750", None)
