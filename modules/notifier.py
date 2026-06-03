"""Notification adapters — WeCom (企业微信) and console (default).

Usage
-----
::

    from modules.notifier import get_notifier

    notifier = get_notifier()  # auto-picks based on env vars
    notifier.send_text("开盘 Top 5 选股已就绪")
    notifier.send_markdown("**jztz_v17** 早盘选股结果\\n\\n| 代码 | 名称 | 评分 |\\n|---|---|---|\\n...")

Configuration
-------------
* ``WECOM_WEBHOOK_URL``  — full webhook URL (must include the ``key=`` token).
  When unset, the ``ConsoleNotifier`` is used (prints to stdout).
* ``WECOM_TIMEOUT``      — HTTP timeout in seconds (default 5).
* ``WECOM_MENTIONED``    — comma-separated mobile list to @-mention.

Trigger scenarios
-----------------
1. Daily pick results ready (9:30 + 14:30)
2. Position signal change (stop-loss / take-profit)
3. External-API fully down (degraded mode)
4. Critical app errors (uncaught exception)

WeCom message types
-------------------
* ``text``    — plain text (max 2048 bytes)
* ``markdown`` — WeCom-flavored markdown (max 4096 bytes)
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Protocol

import requests

from modules.logger import log


# ---------------------------------------------------------------------------
# Protocol — both adapters share this surface
# ---------------------------------------------------------------------------
class Notifier(Protocol):
    def send_text(self, text: str, mentioned: Optional[List[str]] = None) -> bool: ...
    def send_markdown(self, content: str, mentioned: Optional[List[str]] = None) -> bool: ...
    def send_news(self, articles: List[Dict[str, str]]) -> bool: ...


# ---------------------------------------------------------------------------
# Console (no-op fallback)
# ---------------------------------------------------------------------------
class ConsoleNotifier:
    """Prints notifications to stdout. Used when WECOM_WEBHOOK_URL is unset."""

    def send_text(
        self, text: str, mentioned: Optional[List[str]] = None
    ) -> bool:
        log.info(f"[notify/console] text: {text}")
        if mentioned:
            log.info(f"[notify/console] mentioned: {mentioned}")
        return True

    def send_markdown(
        self, content: str, mentioned: Optional[List[str]] = None
    ) -> bool:
        log.info(f"[notify/console] markdown:\n{content}")
        return True

    def send_news(self, articles: List[Dict[str, str]]) -> bool:
        if not articles:
            return False
        log.info(f"[notify/console] news: {len(articles)} articles")
        for a in articles:
            log.info(f"  - {a.get('title', '')}: {a.get('url', '')}")
        return True


# ---------------------------------------------------------------------------
# WeCom (企业微信 group-bot webhook)
# ---------------------------------------------------------------------------
class WeComNotifier:
    """Sends messages to a WeCom group via the robot webhook.

    Reference: https://developer.work.weixin.qq.com/document/path/91770
    """

    MAX_TEXT_BYTES = 2048
    MAX_MARKDOWN_BYTES = 4096

    def __init__(self, webhook_url: str, timeout: float = 5.0) -> None:
        if not webhook_url or "key=" not in webhook_url:
            raise ValueError(
                "Invalid WeCom webhook URL. Expected format: "
                "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<TOKEN>"
            )
        self.webhook_url = webhook_url
        self.timeout = timeout
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    def _truncate(self, content: str, limit: int) -> str:
        """Trim a UTF-8 string to ``limit`` bytes without splitting a char."""
        encoded = content.encode("utf-8")
        if len(encoded) <= limit:
            return content
        # Binary-search the largest prefix that fits.
        lo, hi = 0, len(content)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(content[:mid].encode("utf-8")) <= limit:
                lo = mid
            else:
                hi = mid - 1
        return content[:lo] + "\n…(truncated)"

    def _post(self, payload: Dict[str, Any]) -> bool:
        try:
            with self._lock:
                r = self._session.post(
                    self.webhook_url, json=payload, timeout=self.timeout
                )
            if r.status_code != 200:
                log.error(
                    f"WeCom HTTP {r.status_code}: {r.text[:200]}"
                )
                return False
            data = r.json() if r.headers.get("Content-Type", "").startswith(
                "application/json"
            ) else {}
            if data.get("errcode", 0) != 0:
                log.error(
                    f"WeCom API error errcode={data.get('errcode')} "
                    f"errmsg={data.get('errmsg')}"
                )
                return False
            return True
        except requests.RequestException as e:
            log.error(f"WeCom request failed: {e}")
            return False
        except Exception as e:
            log.error(f"WeCom unexpected error: {e}")
            return False

    def send_text(
        self, text: str, mentioned: Optional[List[str]] = None
    ) -> bool:
        payload: Dict[str, Any] = {
            "msgtype": "text",
            "text": {
                "content": self._truncate(text, self.MAX_TEXT_BYTES),
            },
        }
        if mentioned:
            payload["text"]["mentioned_list"] = mentioned
        return self._post(payload)

    def send_markdown(
        self, content: str, mentioned: Optional[List[str]] = None
    ) -> bool:
        payload: Dict[str, Any] = {
            "msgtype": "markdown",
            "markdown": {
                "content": self._truncate(content, self.MAX_MARKDOWN_BYTES),
            },
        }
        if mentioned:
            payload["markdown"]["mentioned_list"] = mentioned
        return self._post(payload)

    def send_news(self, articles: List[Dict[str, str]]) -> bool:
        if not articles:
            return False
        # WeCom only supports up to 8 articles per message; also each
        # title ≤ 128 bytes, description ≤ 512 bytes, url ≤ 2048.
        clipped = articles[:8]
        for a in clipped:
            a["title"] = self._truncate(a.get("title", ""), 128)
            a["description"] = self._truncate(a.get("description", ""), 512)
            a["url"] = self._truncate(a.get("url", ""), 2048)
            a.setdefault("picurl", "")
        payload = {"msgtype": "news", "news": {"articles": clipped}}
        return self._post(payload)


# ---------------------------------------------------------------------------
# Factory + module-level singleton
# ---------------------------------------------------------------------------
_default_lock = threading.Lock()
_default: Optional[Notifier] = None


def get_notifier() -> Notifier:
    """Return the module-level notifier (singleton).

    Picks ``WeComNotifier`` when ``WECOM_WEBHOOK_URL`` is set, otherwise
    falls back to ``ConsoleNotifier`` so local development / tests
    don't need a real webhook.
    """
    global _default
    with _default_lock:
        if _default is not None:
            return _default
        url = os.environ.get("WECOM_WEBHOOK_URL", "").strip()
        if url:
            try:
                timeout = float(os.environ.get("WECOM_TIMEOUT", "5"))
                _default = WeComNotifier(url, timeout=timeout)
                log.info("[notifier] using WeCom webhook")
            except ValueError as e:
                log.warning(f"[notifier] WeCom init failed ({e}); falling back to console")
                _default = ConsoleNotifier()
        else:
            log.debug("[notifier] WECOM_WEBHOOK_URL not set; using console notifier")
            _default = ConsoleNotifier()
    return _default


def reset_default() -> None:
    """Drop the cached notifier. Test-only."""
    global _default
    with _default_lock:
        _default = None


# ---------------------------------------------------------------------------
# Convenience helpers — used by the daily picker / scheduler
# ---------------------------------------------------------------------------
def notify_daily_pick(picks: List[Dict[str, Any]]) -> bool:
    """Send the daily-pick summary to the configured notifier.

    ``picks`` is a list of dicts with at least ``code`` / ``name`` /
    ``score`` keys.  Returns True on success (or no-op).
    """
    notifier = get_notifier()
    if not picks:
        return notifier.send_text("jztz_v17 今日无符合条件的标的")

    lines = ["**jztz_v17 每日选股 Top 5**", ""]
    lines.append("| # | 代码 | 名称 | 评分 | PE | ROE |")
    lines.append("|---|---|---|---|---|---|")
    for i, p in enumerate(picks[:5], 1):
        lines.append(
            f"| {i} | {p.get('code', '')} | {p.get('name', '')} | "
            f"{p.get('score', 0):.1f} | {p.get('pe', '-')} | "
            f"{p.get('roe', '-')} |"
        )
    lines.append("")
    lines.append(f"_生成时间: {picks[0].get('generated_at', '')}_")
    return notifier.send_markdown("\n".join(lines))


__all__ = [
    "Notifier",
    "ConsoleNotifier",
    "WeComNotifier",
    "get_notifier",
    "reset_default",
    "notify_daily_pick",
]
