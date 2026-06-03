"""routes/api.py 单元测试

覆盖 Phase 1 Task 1: api.py:1088（v19 已在新文件直接写对）
+ 关键分类逻辑：bond 11→sh、12/13→sz；stock 0/3/6 合法、9 非法
"""
import pytest
from unittest.mock import patch, MagicMock
from flask import Flask
from routes.api import api_bp
from modules.cache_manager import cache


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api_bp)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_eastmoney_resp(rows):
    """构造 Eastmoney RPT_BOND_CB_LIST 响应（需含 success + result.count）"""
    resp = MagicMock()
    resp.json.return_value = {
        "success": True,
        "result": {
            "data": rows,
            "count": len(rows),  # 防止 while 循环继续翻页
        },
    }
    resp.raise_for_status = MagicMock()
    return resp


def _make_tencent_resp(qcode_to_data):
    """构造腾讯 qt.gtimg.cn 响应。
    格式: v_<qcode>="<name>~<price>~<pre_close>~...~<change_pct>~...~<volume>~<amount>";
    qcode_to_data: {qcode_str: {name, price, pre_close, change_pct, volume, amount}}
    """
    parts_list = []
    for qcode, d in qcode_to_data.items():
        # 至少 38 个 ~ 分隔字段，索引：1=name, 3=price, 4=pre_close, 32=change_pct, 36=volume, 37=amount
        fields = [""] * 38
        fields[1] = d.get("name", "")
        fields[3] = str(d.get("price", 0))
        fields[4] = str(d.get("pre_close", 0))
        fields[32] = str(d.get("change_pct", 0))
        fields[36] = str(d.get("volume", 0))
        fields[37] = str(d.get("amount", 0))
        parts_list.append(f'v_{qcode}="{"~".join(fields)}";')

    text = "\n".join(parts_list)
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def _make_cb_row(code, stock_code, tp=10.0, name=None, rating="AA+"):
    """构造一行可转债原始数据"""
    return {
        "SECURITY_CODE": code,
        "SECURITY_NAME_ABBR": name or f"Test-{code}",
        "CONVERT_STOCK_CODE": stock_code,
        "INITIAL_TRANSFER_PRICE": tp,
        "RATING": rating,
    }


# === 核心测试：分类逻辑 ===

class TestBondClassification:
    """api.py:1106-1109 债券前缀分类"""

    def test_11_prefix_bond_becomes_sh(self, client):
        """11 开头债券 → sh 前缀（沪市可转债）

        验证策略：检查腾讯行情请求 URL 包含 'sh110001'（证明分类正确）
        """
        rows = [_make_cb_row("110001", "600000", tp=10.0, name="SH-Bond")]
        quotes = {
            "sh110001": {"name": "SH-Bond", "price": 110.0, "pre_close": 108.0, "change_pct": 1.85, "volume": 1000, "amount": 2000},
            "sh600000": {"name": "Stock-600", "price": 25.0, "pre_close": 24.0, "change_pct": 4.17, "volume": 5000, "amount": 10000},
        }
        with patch("modules.http_client.session") as ms, \
             patch("time.time", return_value=0):
            ms.get.side_effect = [_make_eastmoney_resp(rows), _make_tencent_resp(quotes)]
            from routes.api import api_cb_arbitrage
            cache.delete("cb_arbitrage")
            resp = client.get("/api/cb_arbitrage")

        assert resp.status_code == 200
        # 验证分类正确：腾讯请求 URL 应包含 sh110001 和 sh600000
        tencent_call = ms.get.call_args_list[1]
        tencent_url = tencent_call.args[0] if tencent_call.args else tencent_call.kwargs.get("url", "")
        assert "sh110001" in tencent_url, f"11 开头债券应走 sh 前缀，实际 URL: {tencent_url}"
        assert "sh600000" in tencent_url, f"6 开头正股应走 sh 前缀，实际 URL: {tencent_url}"

    def test_12_prefix_bond_becomes_sz(self, client):
        """12 开头债券 → sz 前缀（深市债券）"""
        rows = [_make_cb_row("123001", "000001", tp=10.0, name="SZ-Bond-12")]
        quotes = {
            "sz123001": {"name": "SZ-Bond-12", "price": 115.0, "pre_close": 113.0, "change_pct": 1.77, "volume": 2000, "amount": 3000},
            "sz000001": {"name": "Stock-000", "price": 12.0, "pre_close": 11.5, "change_pct": 4.35, "volume": 3000, "amount": 5000},
        }
        with patch("modules.http_client.session") as ms, \
             patch("time.time", return_value=0):
            ms.get.side_effect = [_make_eastmoney_resp(rows), _make_tencent_resp(quotes)]
            from routes.api import api_cb_arbitrage
            cache.delete("cb_arbitrage")
            resp = client.get("/api/cb_arbitrage")

        assert resp.status_code == 200
        tencent_call = ms.get.call_args_list[1]
        tencent_url = tencent_call.args[0] if tencent_call.args else tencent_call.kwargs.get("url", "")
        assert "sz123001" in tencent_url, f"12 开头债券应走 sz 前缀，实际 URL: {tencent_url}"
        assert "sz000001" in tencent_url, f"0 开头正股应走 sz 前缀，实际 URL: {tencent_url}"

    def test_13_prefix_cb_becomes_sz(self, client):
        """13 开头可转债 → sz 前缀（深市可转债，bug 修复点）

        原始 bug: elif c.startswith("12") or c.startswith("12"):
        修复后:   elif c.startswith("12") or c.startswith("13"):  ← 本测试守护
        """
        rows = [_make_cb_row("127001", "300750", tp=50.0, name="CB-13")]
        quotes = {
            "sz127001": {"name": "CB-13", "price": 120.0, "pre_close": 118.0, "change_pct": 1.69, "volume": 4000, "amount": 8000},
            "sz300750": {"name": "Stock-300", "price": 120.0, "pre_close": 115.0, "change_pct": 4.35, "volume": 6000, "amount": 12000},
        }
        with patch("modules.http_client.session") as ms, \
             patch("time.time", return_value=0):
            ms.get.side_effect = [_make_eastmoney_resp(rows), _make_tencent_resp(quotes)]
            from routes.api import api_cb_arbitrage
            cache.delete("cb_arbitrage")
            resp = client.get("/api/cb_arbitrage")

        assert resp.status_code == 200
        tencent_call = ms.get.call_args_list[1]
        tencent_url = tencent_call.args[0] if tencent_call.args else tencent_call.kwargs.get("url", "")
        assert "sz127001" in tencent_url, f"13 开头 CB 应走 sz 前缀（关键回归点），实际 URL: {tencent_url}"
        assert "sz300750" in tencent_url, f"3 开头正股应走 sz 前缀，实际 URL: {tencent_url}"

    def test_invalid_stock_prefix_filtered(self, client):
        """非 0/3/6 开头正股代码被过滤（api.py:1087-1091）"""
        rows = [
            _make_cb_row("127100", "900001", tp=10.0, name="Invalid-9-prefix"),
            _make_cb_row("127101", "", tp=10.0, name="Empty-stock-code"),
        ]
        with patch("modules.http_client.session") as ms, \
             patch("time.time", return_value=0):
            ms.get.side_effect = [_make_eastmoney_resp(rows), _make_tencent_resp({})]
            from routes.api import api_cb_arbitrage
            cache.delete("cb_arbitrage")
            resp = client.get("/api/cb_arbitrage")
            data = resp.get_json()

        assert resp.status_code == 200
        items = data.get("data", [])
        # 两条都被过滤：1) 9 开头 2) 空 stock_code
        assert len(items) == 0, f"应被全部过滤，实际 {items}"

    def test_no_duplicate_startswith_call(self):
        """回归保护：检查源码中无重复 startswith("12") 模式"""
        import re
        from pathlib import Path
        src = Path("routes/api.py").read_text(encoding="utf-8")
        # 找所有 startswith("12") 出现次数
        count_12 = len(re.findall(r'startswith\(["\']12["\']\)', src))
        # 应当 = startswith("11") 的数量（成对的 if/elif 块）
        count_11 = len(re.findall(r'startswith\(["\']11["\']\)', src))
        # 验证不出现 "startswith("12") or startswith("12")" 这种重复
        assert not re.search(r'startswith\(["\']12["\']\)\s*or\s*startswith\(["\']12["\']\)', src), \
            "发现重复 startswith('12') 调用，bug 复发"
        # 12 应与 11 各出现 2 次（api.py:1106-1108 块 + 1164-1166 块）
        assert count_12 == 2, f"startswith('12') 应出现 2 次，实际 {count_12}"
        assert count_11 == 2, f"startswith('11') 应出现 2 次，实际 {count_11}"


class TestNoDuplicateStartswithPattern:
    """静态扫描：全仓无 c.startswith("12") 重复相邻行"""

    def test_duplicate_pattern_absent(self):
        from pathlib import Path
        for py in Path("routes").rglob("*.py"):
            src = py.read_text(encoding="utf-8")
            # 单行内重复检测
            assert 'startswith("12") or c.startswith("12")' not in src, \
                f"{py}: 出现重复 startswith('12')"
            assert 'startswith("12") or bc.startswith("12")' not in src, \
                f"{py}: 出现重复 startswith('12')"
