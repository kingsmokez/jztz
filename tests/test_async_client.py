"""Tests for modules.async_client — session lifecycle + concurrent fetch.

Note: 不依赖 pytest-asyncio (其 0.23.3 与本环境 pytest 7.4 存在 collection 兼容问题),
直接用 asyncio.run() 包装协程调用. 在 pytest.ini 中已禁用 asyncio 插件.
"""
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from modules import async_client


@pytest.fixture(autouse=True)
def _reset_session():
    """每个测试前重置全局 session, 避免污染."""
    async_client._session = None
    async_client._atexit_registered = False
    yield
    async_client._session = None
    async_client._atexit_registered = False


@pytest.fixture
def mock_aiohttp_session():
    """mock aiohttp.ClientSession, 避免 get_session() 需 running loop."""
    fake = MagicMock()
    fake.closed = False
    fake._connector = MagicMock()
    fake.close = AsyncMock()  # close() 必须返回 awaitable
    with patch("modules.async_client.aiohttp.ClientSession", return_value=fake):
        yield fake


# --- get_session / close_session ---

def test_get_session_creates_session(mock_aiohttp_session):
    s = async_client.get_session()
    assert s is mock_aiohttp_session
    assert not s.closed


def test_get_session_returns_same_instance(mock_aiohttp_session):
    a = async_client.get_session()
    b = async_client.get_session()
    assert a is b


def test_get_session_registers_atexit_once(mock_aiohttp_session):
    async_client.get_session()
    async_client.get_session()
    async_client.get_session()
    assert async_client._atexit_registered is True


def test_close_session_sync_clears_global(mock_aiohttp_session):
    async_client.get_session()
    assert async_client._session is mock_aiohttp_session
    async_client._close_session_sync()
    # 验证: close() 被调, 全局 _session 被 finally 清空
    assert mock_aiohttp_session.close.called
    assert async_client._session is None
    # 重新获取应创建新 session
    s2 = async_client.get_session()
    assert s2 is not None


# --- _fetch_one (包装成同步) ---

def test_fetch_one_success():
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.text = AsyncMock(return_value='{"ok": true, "v": 42}')
    fake_resp.__aenter__ = AsyncMock(return_value=fake_resp)
    fake_resp.__aexit__ = AsyncMock(return_value=False)

    fake_session = MagicMock()
    fake_session.get = MagicMock(return_value=fake_resp)
    fake_session.closed = False

    with patch.object(async_client, "get_session", return_value=fake_session):
        result = asyncio.run(async_client._fetch_one("https://example.com/api"))

    assert result["status"] == 200
    # 解析 _raw 兜底 (resp.json() mock 不被 await, 走 _raw 分支)
    assert result["json"] is not None
    assert "ok" in str(result["json"])
    assert result["error"] is None
    assert result["url"] == "https://example.com/api"


def test_fetch_one_timeout():
    fake_session = MagicMock()
    fake_session.get.side_effect = asyncio.TimeoutError()
    fake_session.closed = False

    with patch.object(async_client, "get_session", return_value=fake_session):
        result = asyncio.run(async_client._fetch_one("https://slow.example.com/api"))

    assert result["status"] is None
    assert "timeout" in result["error"]


def test_fetch_one_client_error():
    import aiohttp
    fake_session = MagicMock()
    fake_session.get.side_effect = aiohttp.ClientError("network down")
    fake_session.closed = False

    with patch.object(async_client, "get_session", return_value=fake_session):
        result = asyncio.run(async_client._fetch_one("https://bad.example.com/api"))

    assert result["status"] is None
    assert "client" in result["error"]


def test_fetch_one_empty_body():
    fake_resp = MagicMock()
    fake_resp.status = 204
    fake_resp.text = AsyncMock(return_value="")
    fake_resp.__aenter__ = AsyncMock(return_value=fake_resp)
    fake_resp.__aexit__ = AsyncMock(return_value=False)

    fake_session = MagicMock()
    fake_session.get = MagicMock(return_value=fake_resp)
    fake_session.closed = False

    with patch.object(async_client, "get_session", return_value=fake_session):
        result = asyncio.run(async_client._fetch_one("https://example.com/empty"))

    assert result["status"] == 204
    assert result["json"] is None
    assert result["error"] is None


# --- fetch_all_async (并发) ---

def test_fetch_all_async_returns_in_input_order():
    """并发 fetch 但返回顺序与输入一致 (gather 保证顺序)."""
    async def fake_fetch_one(url, params=None, headers=None):
        import re
        m = re.search(r"/(\d+)", url)
        n = int(m.group(1)) if m else 0
        await asyncio.sleep(0.01 * (10 - n))
        return {"status": 200, "json": {"url": url, "n": n}, "error": None, "url": url}

    urls_in = [
        ("https://example.com/1", None),
        ("https://example.com/5", None),
        ("https://example.com/3", None),
        ("https://example.com/8", None),
    ]
    with patch.object(async_client, "_fetch_one", side_effect=fake_fetch_one):
        results = asyncio.run(async_client.fetch_all_async(urls_in))
    assert len(results) == 4
    # 顺序与输入一致 (gather 保证)
    for expected, r in zip(urls_in, results):
        assert r["url"] == expected[0]


def test_fetch_all_async_runs_concurrently():
    """验证 fetch_all_async 真的并发 (5 个 0.1s 串行 0.5s, 并发应 < 0.3s)."""
    import time
    async def slow_fetch(url, params=None, headers=None):
        await asyncio.sleep(0.1)
        return {"status": 200, "json": {}, "error": None, "url": url}

    with patch.object(async_client, "_fetch_one", side_effect=slow_fetch):
        start = time.time()
        results = asyncio.run(async_client.fetch_all_async([
            (f"https://example.com/{i}", None) for i in range(5)
        ]))
        elapsed = time.time() - start

    assert elapsed < 0.3, f"expected concurrent execution, got {elapsed:.3f}s"
    for i, r in enumerate(results):
        assert r["url"] == f"https://example.com/{i}"


# --- fetch_all (同步入口) ---

def test_fetch_all_sync_runs_asyncio():
    """同步入口应能成功完成一次并发 fetch."""
    fake_results = [
        {"status": 200, "json": {"id": 1}, "error": None, "url": "u1"},
        {"status": 200, "json": {"id": 2}, "error": None, "url": "u2"},
    ]
    with patch.object(async_client, "asyncio") as mock_aio:
        mock_aio.run.return_value = fake_results
        results = async_client.fetch_all([("u1", None), ("u2", None)])
    assert results == fake_results
    assert mock_aio.run.called


def test_fetch_all_sync_empty_list():
    """空输入应返回空列表."""
    results = async_client.fetch_all([])
    assert results == []
