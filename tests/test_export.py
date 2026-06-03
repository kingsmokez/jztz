"""Tests for modules.exporter (CSV + XLSX) and routes.export."""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
SAMPLE_ROWS: List[Dict[str, Any]] = [
    {
        "code": "000001", "name": "平安银行", "score": 88.5,
        "pe": 5.2, "pb": 0.6, "roe": 12.0,
        "change_pct": 1.23, "debt_ratio": 0.92,
        "industry": "银行", "turnover_rate": 0.45,
    },
    {
        "code": "600519", "name": "贵州茅台", "score": 95.1,
        "pe": 28.0, "pb": 8.5, "roe": 30.0,
        "change_pct": -0.45, "debt_ratio": 0.18,
        "industry": "白酒", "turnover_rate": 0.31,
    },
    {
        "code": "300750", "name": "宁德时代", "score": 91.0,
        "pe": 22.0, "pb": 4.3, "roe": 19.5,
        "change_pct": 2.10, "debt_ratio": 0.68,
        "industry": "新能源", "turnover_rate": 1.25,
    },
]


# ---------------------------------------------------------------------------
# pick_columns
# ---------------------------------------------------------------------------
class TestPickColumns:
    def test_first_seen_order(self):
        from modules.exporter import pick_columns
        rows = [
            {"a": 1, "b": 2, "c": 3},
            {"b": 20, "d": 40},
        ]
        assert pick_columns(rows) == ["a", "b", "c", "d"]

    def test_prefer_used_when_all_present(self):
        from modules.exporter import pick_columns
        rows = [{"a": 1, "b": 2, "c": 3}]
        assert pick_columns(rows, prefer=["c", "a"]) == ["c", "a"]

    def test_prefer_ignored_when_missing(self):
        from modules.exporter import pick_columns
        rows = [{"a": 1, "b": 2}]
        # 'x' is missing — prefer is dropped, default order is used
        assert pick_columns(rows, prefer=["x", "a"]) == ["a", "b"]


# ---------------------------------------------------------------------------
# is_numeric_key
# ---------------------------------------------------------------------------
class TestIsNumericKey:
    def test_known_numeric_keys(self):
        from modules.exporter import is_numeric_key
        assert is_numeric_key("score") is True
        assert is_numeric_key("pe") is True
        assert is_numeric_key("roe") is True

    def test_suffix_keys(self):
        from modules.exporter import is_numeric_key
        assert is_numeric_key("foo_pct") is True
        assert is_numeric_key("bar_rate") is True
        assert is_numeric_key("baz_ratio") is True

    def test_non_numeric_keys(self):
        from modules.exporter import is_numeric_key
        assert is_numeric_key("code") is False
        assert is_numeric_key("name") is False
        assert is_numeric_key("industry") is False


# ---------------------------------------------------------------------------
# to_csv
# ---------------------------------------------------------------------------
class TestToCSV:
    def test_basic_encoding(self):
        from modules.exporter import to_csv
        body = to_csv(SAMPLE_ROWS)
        # UTF-8 BOM
        assert body.startswith(b"\xef\xbb\xbf")
        text = body.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        # header + 3 data rows
        assert len(rows) == 4
        assert rows[0] == list(SAMPLE_ROWS[0].keys())
        assert rows[1][0] == "000001"
        assert rows[1][1] == "平安银行"

    def test_empty_rows_returns_bom_only(self):
        from modules.exporter import to_csv
        body = to_csv([])
        assert body == b"\xef\xbb\xbf"

    def test_explicit_columns(self):
        from modules.exporter import to_csv
        body = to_csv(SAMPLE_ROWS, columns=["code", "name"]).decode("utf-8-sig")
        reader = csv.reader(io.StringIO(body))
        rows = list(reader)
        assert rows[0] == ["code", "name"]
        assert all(len(r) == 2 for r in rows)

    def test_handles_none_and_bools(self):
        from modules.exporter import to_csv
        rows = [{"a": None, "b": True, "c": False, "d": 0, "e": "x"}]
        body = to_csv(rows).decode("utf-8-sig")
        reader = csv.reader(io.StringIO(body))
        out = list(reader)
        assert out[1] == ["", "true", "false", "0", "x"]

    def test_handles_nested_list_value(self):
        from modules.exporter import to_csv
        rows = [{"tags": ["a", "b", "c"]}]
        body = to_csv(rows).decode("utf-8-sig")
        reader = csv.reader(io.StringIO(body))
        out = list(reader)
        assert out[1] == ["a,b,c"]

    def test_formula_injection_prefixed(self):
        """Values starting with =, +, -, @, tab or CR are escaped so
        Excel doesn't interpret them as formulas (CVE-class issue)."""
        from modules.exporter import to_csv
        rows = [
            {"a": "=cmd|'/c calc'!A0"},
            {"a": "+1+1"},
            {"a": "-2+3"},
            {"a": "@SUM(A1:A9)"},
            {"a": "\tTAB"},
            {"a": "normal text"},
        ]
        body = to_csv(rows).decode("utf-8-sig")
        reader = csv.reader(io.StringIO(body))
        out = list(reader)
        # Header row + 6 data rows
        assert len(out) == 7
        # Each formula-like value is prefixed with a single quote
        assert out[1] == ["'=cmd|'/c calc'!A0"]
        assert out[2] == ["'+1+1"]
        assert out[3] == ["'-2+3"]
        assert out[4] == ["'@SUM(A1:A9)"]
        assert out[5] == ["'\tTAB"]
        # Plain text is untouched
        assert out[6] == ["normal text"]


# ---------------------------------------------------------------------------
# to_xlsx
# ---------------------------------------------------------------------------
class TestToXLSX:
    def test_returns_bytes(self):
        from modules.exporter import to_xlsx
        body = to_xlsx(SAMPLE_ROWS)
        assert isinstance(body, bytes)
        # XLSX is a zip archive
        assert body[:2] == b"PK"

    def test_contains_openpyxl_signature(self):
        from modules.exporter import to_xlsx
        body = to_xlsx(SAMPLE_ROWS)
        # Workbook contains the sheet name in the xl/ folder
        assert b"xl/" in body

    def test_explicit_columns(self):
        from modules.exporter import to_xlsx
        body = to_xlsx(SAMPLE_ROWS, columns=["code", "name"])
        assert isinstance(body, bytes)

    def test_with_title(self):
        from modules.exporter import to_xlsx
        body = to_xlsx(SAMPLE_ROWS, title="Test Report")
        assert isinstance(body, bytes)
        assert len(body) > 0

    def test_safe_sheet_name(self):
        from modules.exporter import to_xlsx
        # Sheet name with special chars + long string
        body = to_xlsx(SAMPLE_ROWS, sheet_name="Sheet[1]/* 测试 " * 5)
        assert isinstance(body, bytes)


# ---------------------------------------------------------------------------
# collect_* — wire to daily_pick state
# ---------------------------------------------------------------------------
class TestCollectDailyQuotes:
    def test_empty_data(self, monkeypatch):
        import routes.daily as daily_mod
        monkeypatch.setattr(daily_mod, "DAILY_PICK_DATA", {
            "date": "2026-06-02",
            "morning": None,
            "afternoon": None,
        })
        from modules.exporter import collect_daily_quotes
        assert collect_daily_quotes() == []

    def test_both_sessions(self, monkeypatch):
        import routes.daily as daily_mod
        monkeypatch.setattr(daily_mod, "DAILY_PICK_DATA", {
            "date": "2026-06-02",
            "morning": {
                "results": [{"code": "000001", "name": "A", "score": 80}],
                "session_type": "早盘选股",
                "pick_time": "2026-06-02 09:30",
            },
            "afternoon": {
                "results": [{"code": "600000", "name": "B", "score": 90}],
                "session_type": "午盘选股",
                "pick_time": "2026-06-02 14:30",
            },
        })
        from modules.exporter import collect_daily_quotes
        rows = collect_daily_quotes()
        assert len(rows) == 2
        assert rows[0]["session"] == "morning"
        assert rows[1]["session"] == "afternoon"
        assert rows[0]["session_label"] == "早盘选股"
        assert rows[0]["pick_time"] == "2026-06-02 09:30"

    def test_session_filter(self, monkeypatch):
        import routes.daily as daily_mod
        monkeypatch.setattr(daily_mod, "DAILY_PICK_DATA", {
            "date": "2026-06-02",
            "morning": {
                "results": [{"code": "000001", "name": "A"}],
                "session_type": "早盘选股",
                "pick_time": "t1",
            },
            "afternoon": {
                "results": [{"code": "600000", "name": "B"}],
                "session_type": "午盘选股",
                "pick_time": "t2",
            },
        })
        from modules.exporter import collect_daily_quotes
        morning = collect_daily_quotes(session="morning")
        assert len(morning) == 1
        assert morning[0]["code"] == "000001"
        afternoon = collect_daily_quotes(session="afternoon")
        assert len(afternoon) == 1
        assert afternoon[0]["code"] == "600000"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    from web_app import create_app
    app = create_app()
    return app.test_client()


class TestExportRoute:
    def test_missing_type_returns_400(self, client):
        r = client.get("/api/export?format=csv")
        assert r.status_code == 400
        assert b"type" in r.data.lower()

    def test_missing_format_returns_400(self, client):
        r = client.get("/api/export?type=daily_quotes")
        assert r.status_code == 400
        assert b"format" in r.data.lower()

    def test_unknown_type_returns_400(self, client):
        r = client.get("/api/export?type=banana&format=csv")
        assert r.status_code == 400
        assert b"banana" in r.data.lower()

    def test_unknown_format_returns_400(self, client):
        r = client.get("/api/export?type=daily_quotes&format=pdf")
        assert r.status_code == 400

    def test_invalid_session_returns_400(self, client, monkeypatch):
        import routes.daily as daily_mod
        monkeypatch.setattr(daily_mod, "DAILY_PICK_DATA", {
            "date": "2026-06-02",
            "morning": {
                "results": [{"code": "000001", "name": "A"}],
                "session_type": "早盘选股",
                "pick_time": "t",
            },
            "afternoon": None,
        })
        r = client.get(
            "/api/export?type=daily_quotes&format=csv&session=evening"
        )
        assert r.status_code == 400

    def test_empty_data_returns_404(self, client, monkeypatch):
        import routes.daily as daily_mod
        monkeypatch.setattr(daily_mod, "DAILY_PICK_DATA", {
            "date": "2026-06-02",
            "morning": None,
            "afternoon": None,
        })
        r = client.get("/api/export?type=daily_quotes&format=csv")
        assert r.status_code == 404
        assert b"no data" in r.data.lower() or b"\xe6\x97\xa0" in r.data

    def test_csv_download(self, client, monkeypatch):
        import routes.daily as daily_mod
        monkeypatch.setattr(daily_mod, "DAILY_PICK_DATA", {
            "date": "2026-06-02",
            "morning": {
                "results": [
                    {"code": "000001", "name": "平安银行", "score": 88.5,
                     "pe": 5.2, "pb": 0.6, "roe": 12.0},
                    {"code": "600519", "name": "贵州茅台", "score": 95.1,
                     "pe": 28.0, "pb": 8.5, "roe": 30.0},
                ],
                "session_type": "早盘选股",
                "pick_time": "2026-06-02 09:30",
            },
            "afternoon": None,
        })
        r = client.get(
            "/api/export?type=daily_quotes&format=csv&session=morning"
        )
        assert r.status_code == 200
        assert r.headers.get("Content-Type", "").startswith("text/csv")
        assert "attachment" in r.headers.get("Content-Disposition", "")
        body = r.data
        assert body.startswith(b"\xef\xbb\xbf")
        # Chinese characters must be present
        assert "平安银行".encode("utf-8") in body
        assert "000001".encode("utf-8") in body
        assert r.headers.get("X-Row-Count") == "2"

    def test_xlsx_download(self, client, monkeypatch):
        import routes.daily as daily_mod
        monkeypatch.setattr(daily_mod, "DAILY_PICK_DATA", {
            "date": "2026-06-02",
            "morning": {
                "results": [
                    {"code": "000001", "name": "平安银行", "score": 88.5},
                ],
                "session_type": "早盘选股",
                "pick_time": "t",
            },
            "afternoon": None,
        })
        r = client.get(
            "/api/export?type=daily_quotes&format=xlsx&session=morning"
        )
        assert r.status_code == 200
        assert "spreadsheetml" in r.headers.get("Content-Type", "")
        assert r.data[:2] == b"PK"
        assert r.headers.get("X-Row-Count") == "1"

    def test_content_disposition_supports_utf8(self, client, monkeypatch):
        import routes.daily as daily_mod
        monkeypatch.setattr(daily_mod, "DAILY_PICK_DATA", {
            "date": "2026-06-02",
            "morning": {
                "results": [{"code": "000001", "name": "A"}],
                "session_type": "早盘选股",
                "pick_time": "t",
            },
            "afternoon": None,
        })
        r = client.get("/api/export?type=daily_quotes&format=csv")
        cd = r.headers.get("Content-Disposition", "")
        # RFC 5987 UTF-8 marker must be present for non-ASCII filenames
        assert "filename*=UTF-8''" in cd

    def test_both_sessions_no_filter(self, client, monkeypatch):
        import routes.daily as daily_mod
        monkeypatch.setattr(daily_mod, "DAILY_PICK_DATA", {
            "date": "2026-06-02",
            "morning": {
                "results": [{"code": "000001", "name": "A", "score": 80}],
                "session_type": "早盘选股",
                "pick_time": "t1",
            },
            "afternoon": {
                "results": [{"code": "600000", "name": "B", "score": 90}],
                "session_type": "午盘选股",
                "pick_time": "t2",
            },
        })
        r = client.get("/api/export?type=daily_quotes&format=csv")
        assert r.status_code == 200
        assert r.headers.get("X-Row-Count") == "2"


class TestExportTypesEndpoint:
    def test_types_metadata(self, client):
        r = client.get("/api/export/types")
        assert r.status_code == 200
        body = json.loads(r.data)
        assert body["success"] is True
        types = {t["id"] for t in body["types"]}
        assert "daily_quotes" in types
        assert "live_quotes" in types
        assert "auction_quotes" in types
        for t in body["types"]:
            assert "csv" in t["formats"]
            assert "xlsx" in t["formats"]
        assert body["default_format"] == "csv"
