"""数据模型单元测试"""

import pytest
from modules.models import StockQuote, _safe_float


class TestSafeFloat:
    def test_normal(self):
        assert _safe_float("3.14") == 3.14

    def test_negative(self):
        assert _safe_float("-5.2") == -5.2

    def test_with_comma(self):
        assert _safe_float("1,234.56") == 1234.56

    def test_empty(self):
        assert _safe_float("") == 0.0

    def test_none(self):
        assert _safe_float(None) == 0.0

    def test_non_numeric(self):
        assert _safe_float("abc") == 0.0

    def test_custom_default(self):
        assert _safe_float("bad", default=-1.0) == -1.0


class TestStockQuoteFromParts:
    def test_insufficient_parts(self):
        result = StockQuote.from_tencent_parts("600519", ["a", "b"])
        assert result is None

    def test_valid_parts(self):
        parts = ["v_sh"] + [""] * 50
        parts[1] = "贵州茅台"
        parts[2] = "600519"
        parts[3] = "1800.00"
        parts[4] = "1764.00"
        parts[5] = "1795.00"
        parts[6] = "50000"
        parts[32] = "2.04"
        parts[37] = "9000000"
        parts[38] = "3.50"
        parts[43] = "30.5"
        parts[44] = "22000"
        result = StockQuote.from_tencent_parts("600519", parts)
        assert result is not None
        assert result.code == "600519"
        assert result.name == "贵州茅台"
        assert result.price == 1800.0
