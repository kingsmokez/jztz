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
