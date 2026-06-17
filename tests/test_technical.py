"""技术指标单元测试"""

import pytest
from modules.technical import calc_ma, calc_ema, calc_macd, calc_rsi, calc_kdj, calc_boll, evaluate_technical


class TestMA:
    def test_basic(self):
        prices = [1, 2, 3, 4, 5]
        result = calc_ma(prices, 3)
        assert result[0] is None
        assert result[1] is None
        assert result[2] == 2.0
        assert result[3] == 3.0
        assert result[4] == 4.0

    def test_short_data(self):
        result = calc_ma([1, 2], 5)
        assert all(v is None for v in result)


class TestEMA:
    def test_basic(self):
        prices = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        result = calc_ema(prices, 5)
        assert result[0] is None
        assert result[4] is not None

    def test_short_data(self):
        result = calc_ema([1, 2], 5)
        assert all(v is None for v in result)


class TestMACD:
    def test_basic(self):
        prices = [i * 1.0 for i in range(1, 50)]
        result = calc_macd(prices)
        assert "dif" in result
        assert "dea" in result
        assert "macd" in result

    def test_short_data(self):
        result = calc_macd([1, 2, 3])
        assert result["dif"][0] is None


class TestRSI:
    def test_basic(self):
        prices = [10, 11, 10, 12, 11, 13, 12, 14, 13, 15, 14, 16, 15, 17, 16, 18]
        result = calc_rsi(prices, period=14)
        assert len(result) == len(prices)
        assert result[-1] is not None

    def test_short_data(self):
        result = calc_rsi([1, 2], period=14)
        assert all(v is None for v in result)


class TestKDJ:
    def test_basic(self):
        closes = [10 + i * 0.5 for i in range(20)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        result = calc_kdj(highs, lows, closes)
        assert "k" in result
        assert "d" in result
        assert "j" in result
        assert result["k"][-1] is not None

    def test_short_data(self):
        result = calc_kdj([2], [1], [1.5], n=9)
        assert result["k"][0] is None


class TestBoll:
    def test_basic(self):
        prices = [10 + i * 0.3 for i in range(30)]
        result = calc_boll(prices)
        assert result["upper"][-1] is not None
        assert result["mid"][-1] is not None
        assert result["lower"][-1] is not None
        assert result["upper"][-1] > result["mid"][-1] > result["lower"][-1]


class TestEvaluateTechnical:
    def test_sufficient_data(self):
        prices = [10 + i * 0.1 + (i % 5) * 0.2 for i in range(60)]
        score = evaluate_technical(prices)
        assert 0 <= score <= 100

    def test_insufficient_data(self):
        score = evaluate_technical([1, 2, 3])
        assert score == 0


class TestNewIndicatorsIntegration:
    """Tests for P0-5: new indicators (OBV, VWAP, Boll width, multi-RSI, ATR)"""

    def test_calc_atr(self):
        """ATR 计算正确"""
        from modules.technical import calc_atr
        highs = [10, 11, 12, 11, 10, 11, 12, 13, 12, 11, 10, 11, 12, 11, 10]
        lows = [8, 9, 10, 9, 8, 9, 10, 11, 10, 9, 8, 9, 10, 9, 8]
        closes = [9, 10, 11, 10, 9, 10, 11, 12, 11, 10, 9, 10, 11, 10, 9]
        atr = calc_atr(highs, lows, closes, period=14)
        assert atr is not None
        assert atr > 0

    def test_calc_atr_insufficient_data(self):
        """数据不足时返回None"""
        from modules.technical import calc_atr
        atr = calc_atr([10, 11], [8, 9], [9, 10], period=14)
        assert atr is None

    def test_evaluate_technical_score_with_obv(self):
        """OBV bullish 趋势应获得加分"""
        from modules.technical import evaluate_technical_score
        tech_data = {"ma_signal": "bull", "macd_signal": "golden_cross",
                     "rsi": 45, "boll_position": 0.3, "change_5d": 0,
                     "obv_trend": "bullish"}
        score, reasons = evaluate_technical_score("000001", tech_data)
        assert score >= 5  # MA(3) + MACD(3) + OBV(2) = 8
        assert any("OBV" in r for r in reasons)

    def test_evaluate_technical_score_with_boll_width_squeeze(self):
        """布林带收敛应获得加分"""
        from modules.technical import evaluate_technical_score
        tech_data = {"ma_signal": "bull", "macd_signal": "neutral",
                     "rsi": 50, "boll_position": 0.5, "change_5d": 0,
                     "boll_width_pct": 3.5}
        score, reasons = evaluate_technical_score("000001", tech_data)
        assert any("收敛" in r for r in reasons)

    def test_evaluate_technical_score_with_multi_rsi_oversold(self):
        """多周期RSI超卖共振应获得加分"""
        from modules.technical import evaluate_technical_score
        tech_data = {"ma_signal": "unknown", "macd_signal": "neutral",
                     "rsi": 25, "boll_position": 0.5, "change_5d": -8,
                     "rsi_6": 20, "rsi_12": 35, "rsi_24": 45}
        score, reasons = evaluate_technical_score("000001", tech_data)
        assert any("共振" in r for r in reasons)

    def test_evaluate_technical_score_with_vwap_support(self):
        """价格贴近VWAP应获得加分"""
        from modules.technical import evaluate_technical_score
        tech_data = {"ma_signal": "unknown", "macd_signal": "neutral",
                     "rsi": 50, "boll_position": 0.5, "change_5d": 0,
                     "vwap": 10.05, "price": 10.0, "ma5": 10.0}
        score, reasons = evaluate_technical_score("000001", tech_data)
        assert any("VWAP" in r for r in reasons)
