"""Tests for modules.notifier — WeCom + console adapters."""
from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from modules.notifier import (
    ConsoleNotifier,
    WeComNotifier,
    get_notifier,
    reset_default,
    notify_daily_pick,
)


# ---------------------------------------------------------------------------
# ConsoleNotifier — no env, prints to log
# ---------------------------------------------------------------------------
class TestConsoleNotifier:
    def test_send_text_returns_true(self):
        c = ConsoleNotifier()
        assert c.send_text("hello") is True

    def test_send_text_with_mention(self):
        c = ConsoleNotifier()
        assert c.send_text("hi", mentioned=["@all"]) is True

    def test_send_markdown_returns_true(self):
        c = ConsoleNotifier()
        assert c.send_markdown("**bold**") is True

    def test_send_news_returns_true(self):
        c = ConsoleNotifier()
        articles = [{"title": "t", "description": "d", "url": "https://x", "picurl": ""}]
        assert c.send_news(articles) is True

    def test_send_news_empty_returns_false(self):
        c = ConsoleNotifier()
        assert c.send_news([]) is False


# ---------------------------------------------------------------------------
# WeComNotifier — input validation, truncation, payload shape
# ---------------------------------------------------------------------------
VALID_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcd-1234"


class TestWeComValidation:
    def test_rejects_empty_url(self):
        with pytest.raises(ValueError):
            WeComNotifier("")

    def test_rejects_url_without_key(self):
        with pytest.raises(ValueError):
            WeComNotifier("https://example.com/webhook")

    def test_accepts_valid_url(self):
        n = WeComNotifier(VALID_WEBHOOK)
        assert n.webhook_url == VALID_WEBHOOK


class TestWeComTruncation:
    def test_short_text_unchanged(self):
        n = WeComNotifier(VALID_WEBHOOK)
        assert n._truncate("hi", 100) == "hi"

    def test_long_text_truncated(self):
        n = WeComNotifier(VALID_WEBHOOK)
        s = "x" * 5000
        out = n._truncate(s, 100)
        assert len(out.encode("utf-8")) <= 100 + 30  # +suffix
        assert out.endswith("…(truncated)")

    def test_chinese_boundary_respected(self):
        n = WeComNotifier(VALID_WEBHOOK)
        # Each Chinese char is 3 bytes in UTF-8.
        s = "中" * 100
        out = n._truncate(s, 50)  # ~16 chars + suffix
        # The truncated output must decode as valid UTF-8.
        out.encode("utf-8").decode("utf-8")
        assert len(out.encode("utf-8")) <= 50 + 30


class TestWeComPayload:
    def _mock_post_ok(self, **kwargs):
        m = MagicMock()
        m.status_code = 200
        m.headers = {"Content-Type": "application/json"}
        m.json.return_value = {"errcode": 0, "errmsg": "ok"}
        return m

    def _mock_post_err(self, errcode: int, errmsg: str = "err"):
        m = MagicMock()
        m.status_code = 200
        m.headers = {"Content-Type": "application/json"}
        m.json.return_value = {"errcode": errcode, "errmsg": errmsg}
        return m

    def _mock_post_http_fail(self, code: int):
        m = MagicMock()
        m.status_code = code
        m.text = "boom"
        return m

    def test_send_text_payload_shape(self):
        n = WeComNotifier(VALID_WEBHOOK)
        with patch.object(n._session, "post", return_value=self._mock_post_ok()) as p:
            ok = n.send_text("hello world")
        assert ok is True
        p.assert_called_once()
        payload = p.call_args.kwargs["json"]
        assert payload["msgtype"] == "text"
        assert payload["text"]["content"] == "hello world"
        assert "mentioned_list" not in payload["text"]

    def test_send_text_with_mention(self):
        n = WeComNotifier(VALID_WEBHOOK)
        with patch.object(n._session, "post", return_value=self._mock_post_ok()) as p:
            n.send_text("ping", mentioned=["@all", "13800138000"])
        payload = p.call_args.kwargs["json"]
        assert payload["text"]["mentioned_list"] == ["@all", "13800138000"]

    def test_send_markdown_payload_shape(self):
        n = WeComNotifier(VALID_WEBHOOK)
        with patch.object(n._session, "post", return_value=self._mock_post_ok()) as p:
            ok = n.send_markdown("**bold**")
        assert ok is True
        payload = p.call_args.kwargs["json"]
        assert payload["msgtype"] == "markdown"
        assert payload["markdown"]["content"] == "**bold**"

    def test_send_news_clips_to_8(self):
        n = WeComNotifier(VALID_WEBHOOK)
        articles = [
            {"title": f"t{i}", "description": "d", "url": "https://x", "picurl": ""}
            for i in range(20)
        ]
        with patch.object(n._session, "post", return_value=self._mock_post_ok()) as p:
            ok = n.send_news(articles)
        assert ok is True
        payload = p.call_args.kwargs["json"]
        assert payload["msgtype"] == "news"
        assert len(payload["news"]["articles"]) == 8

    def test_send_news_empty_returns_false(self):
        n = WeComNotifier(VALID_WEBHOOK)
        assert n.send_news([]) is False

    def test_returns_false_on_api_error(self):
        n = WeComNotifier(VALID_WEBHOOK)
        with patch.object(n._session, "post", return_value=self._mock_post_err(40001)):
            assert n.send_text("x") is False

    def test_returns_false_on_http_error(self):
        n = WeComNotifier(VALID_WEBHOOK)
        with patch.object(n._session, "post", return_value=self._mock_post_http_fail(500)):
            assert n.send_text("x") is False

    def test_returns_false_on_network_error(self):
        import requests
        n = WeComNotifier(VALID_WEBHOOK)
        with patch.object(
            n._session, "post", side_effect=requests.ConnectionError("nope")
        ):
            assert n.send_text("x") is False

    def test_long_text_truncated_before_send(self):
        n = WeComNotifier(VALID_WEBHOOK)
        big = "x" * (WeComNotifier.MAX_TEXT_BYTES + 1000)
        with patch.object(n._session, "post", return_value=self._mock_post_ok()) as p:
            n.send_text(big)
        payload = p.call_args.kwargs["json"]
        # The payload content should respect the 2048-byte limit
        # (truncation suffix may push slightly over, hence the +20).
        assert len(payload["text"]["content"].encode("utf-8")) <= (
            WeComNotifier.MAX_TEXT_BYTES + 20
        )


# ---------------------------------------------------------------------------
# Factory + env-based selection
# ---------------------------------------------------------------------------
class TestGetNotifier:
    def setup_method(self):
        reset_default()

    def teardown_method(self):
        reset_default()

    def test_no_env_returns_console(self, monkeypatch):
        monkeypatch.delenv("WECOM_WEBHOOK_URL", raising=False)
        n = get_notifier()
        assert isinstance(n, ConsoleNotifier)

    def test_empty_env_returns_console(self, monkeypatch):
        monkeypatch.setenv("WECOM_WEBHOOK_URL", "")
        n = get_notifier()
        assert isinstance(n, ConsoleNotifier)

    def test_valid_env_returns_wecom(self, monkeypatch):
        monkeypatch.setenv(
            "WECOM_WEBHOOK_URL", VALID_WEBHOOK
        )
        monkeypatch.setenv("WECOM_TIMEOUT", "3")
        n = get_notifier()
        assert isinstance(n, WeComNotifier)
        assert n.timeout == 3.0

    def test_invalid_env_falls_back_to_console(self, monkeypatch):
        monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://no-key.example.com")
        n = get_notifier()
        assert isinstance(n, ConsoleNotifier)

    def test_singleton(self, monkeypatch):
        monkeypatch.delenv("WECOM_WEBHOOK_URL", raising=False)
        a = get_notifier()
        b = get_notifier()
        assert a is b


# ---------------------------------------------------------------------------
# notify_daily_pick — convenience helper
# ---------------------------------------------------------------------------
class TestNotifyDailyPick:
    def test_empty_picks_sends_text(self, monkeypatch):
        monkeypatch.delenv("WECOM_WEBHOOK_URL", raising=False)
        reset_default()
        sent = []
        notifier = ConsoleNotifier()
        notifier.send_text = lambda t, mentioned=None: sent.append(t) or True  # type: ignore
        with patch("modules.notifier.get_notifier", return_value=notifier):
            ok = notify_daily_pick([])
        assert ok is True
        assert any("无符合" in t for t in sent)

    def test_full_picks_sends_markdown(self, monkeypatch):
        monkeypatch.delenv("WECOM_WEBHOOK_URL", raising=False)
        reset_default()
        sent_md = []
        notifier = ConsoleNotifier()
        notifier.send_markdown = lambda c, mentioned=None: sent_md.append(c) or True  # type: ignore
        with patch("modules.notifier.get_notifier", return_value=notifier):
            ok = notify_daily_pick(
                [
                    {"code": "000001", "name": "平安银行", "score": 88.5, "pe": 5.2, "roe": 12.0, "generated_at": "2026-06-02"},
                    {"code": "600519", "name": "贵州茅台", "score": 95.1, "pe": 28.0, "roe": 30.0, "generated_at": "2026-06-02"},
                ]
            )
        assert ok is True
        assert len(sent_md) == 1
        md = sent_md[0]
        assert "Top 5" in md
        assert "000001" in md
        assert "平安银行" in md
        assert "600519" in md
        assert "贵州茅台" in md
