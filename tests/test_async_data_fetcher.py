"""异步数据获取模块单元测试"""

import pytest
from modules.async_data_fetcher import _parse_tencent_response


class TestParseTencentResponse:
    def test_empty_input(self):
        result = _parse_tencent_response("")
        assert result == {}

    def test_invalid_line(self):
        result = _parse_tencent_response("invalid;line;here;")
        assert result == {}

    def test_short_parts(self):
        result = _parse_tencent_response("v_sh~600519~600519~~10~~")
        assert result == {}
