"""异步 HTTP 客户端 — aiohttp 单例 + 并发 fetch 辅助.

设计要点:
  1. 全局 ClientSession 单例 (线程安全, 惰性初始化)
  2. fetch_all_async() 用 asyncio.gather 并发请求
  3. 同步入口 fetch_all() 让 Flask 路由无需重写也能用 (asyncio.run)
  4. close_session() 在应用关闭时调用 (atexit 注册)
"""
from __future__ import annotations

import asyncio
import atexit
import threading
from typing import Any, Optional

import aiohttp

from modules.logger import log


_session: Optional[aiohttp.ClientSession] = None
_session_lock = threading.Lock()
_default_timeout = aiohttp.ClientTimeout(total=10)
_atexit_registered = False


def get_session() -> aiohttp.ClientSession:
    """获取全局 aiohttp session 单例 (线程安全, 惰性初始化)."""
    global _session, _atexit_registered
    with _session_lock:
        if _session is None or _session.closed:
            _session = aiohttp.ClientSession(timeout=_default_timeout)
            if not _atexit_registered:
                atexit.register(_close_session_sync)
                _atexit_registered = True
        return _session


def _close_session_sync() -> None:
    """atexit 同步关闭入口. 若有 running loop 则 new_loop."""
    global _session
    with _session_lock:
        if _session is None or _session.closed:
            return
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_session.close())
            loop.close()
        except Exception as e:
            log.warning(f"关闭 aiohttp session 失败: {e}")
        finally:
            _session = None


async def close_session() -> None:
    """异步关闭入口 (在已有 event loop 的上下文中调用)."""
    global _session
    with _session_lock:
        if _session is None or _session.closed:
            return
        await _session.close()
        _session = None


async def _fetch_one(
    url: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> dict[str, Any]:
    """单次 GET. 返回 {"status": int|None, "json": Any|None, "error": str|None}."""
    session = get_session()
    result: dict[str, Any] = {"status": None, "json": None, "error": None, "url": url}
    try:
        async with session.get(url, params=params, headers=headers) as resp:
            result["status"] = resp.status
            text = await resp.text()
            if text:
                try:
                    result["json"] = await resp.json() if hasattr(resp, "json") else None
                    if result["json"] is None:
                        # 兜底: 手动 json.loads
                        import json
                        try:
                            result["json"] = json.loads(text)
                        except Exception:
                            result["json"] = {"_raw": text[:500]}
                except Exception:
                    import json
                    try:
                        result["json"] = json.loads(text)
                    except Exception:
                        result["json"] = {"_raw": text[:500]}
    except asyncio.TimeoutError as e:
        result["error"] = f"timeout: {e}"
        log.warning(f"async fetch timeout: {url}")
    except aiohttp.ClientError as e:
        result["error"] = f"client: {e}"
        log.warning(f"async fetch client error: {url}, {e}")
    except Exception as e:
        result["error"] = f"unknown: {e}"
        log.warning(f"async fetch failed: {url}, {e}")
    return result


async def fetch_all_async(
    urls_with_params: list[tuple[str, Optional[dict]]],
    headers: Optional[dict] = None,
) -> list[dict[str, Any]]:
    """并发 fetch 多个 URL. 返回顺序与输入一致."""
    tasks = [_fetch_one(url, params, headers) for url, params in urls_with_params]
    return await asyncio.gather(*tasks)


def fetch_all(
    urls_with_params: list[tuple[str, Optional[dict]]],
    headers: Optional[dict] = None,
) -> list[dict[str, Any]]:
    """同步入口: 在 Flask 同步路由中用 asyncio.run 并发 fetch.

    用法:
        results = fetch_all([
            ("https://api1.com/x", {"k": "v"}),
            ("https://api2.com/y", None),
        ])
    """
    return asyncio.run(fetch_all_async(urls_with_params, headers))
