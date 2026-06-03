"""异步数据获取模块 - aiohttp实现，性能2-5x提升"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

from modules.config import Config
from modules.logger import log
from modules.models import StockQuote, _safe_float

_config = Config().data


async def async_get_realtime_quotes(
    codes: Optional[list[str]] = None,
    batch_size: int = 50,
) -> dict[str, StockQuote]:
    """异步批量获取实时行情"""
    if not HAS_AIOHTTP:
        log.warning("aiohttp未安装，回退同步模式")
        from modules.data_fetcher import get_realtime_quotes
        return get_realtime_quotes(codes)

    all_codes = codes or await _async_get_all_stock_codes()
    if not all_codes:
        return {}

    quotes: dict[str, StockQuote] = {}
    semaphore = asyncio.Semaphore(10)  # 并发限制

    async with aiohttp.ClientSession(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/",
        },
        timeout=aiohttp.ClientTimeout(total=_config.timeout),
    ) as session:
        tasks = [
            _fetch_batch(session, semaphore, all_codes[i:i + batch_size])
            for i in range(0, len(all_codes), batch_size)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, dict):
                quotes.update(result)
            elif isinstance(result, Exception):
                log.warning(f"批量获取异常: {result}")

    log.info(f"异步获取行情完成: {len(quotes)} 只")
    return quotes


async def _fetch_batch(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    codes: list[str],
) -> dict[str, StockQuote]:
    """获取一批行情数据"""
    async with semaphore:
        url = _config.tencent_api_url + ",".join(codes)
        try:
            async with session.get(url) as resp:
                text = await resp.text()
                return _parse_tencent_response(text)
        except asyncio.TimeoutError:
            log.warning(f"异步获取超时: {len(codes)} 只")
            return {}
        except aiohttp.ClientError as e:
            log.error(f"异步请求失败: {e}")
            return {}


def _parse_tencent_response(text: str) -> dict[str, StockQuote]:
    """解析腾讯API响应"""
    quotes: dict[str, StockQuote] = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "~" not in line:
            continue
        parts = line.split("~")
        if len(parts) < 48:
            continue
        code = parts[2] if len(parts) > 2 else ""
        quote = StockQuote.from_tencent_parts(code, parts)
        if quote:
            quotes[code] = quote
    return quotes


async def _async_get_all_stock_codes() -> list[str]:
    """异步获取全部股票代码"""
    if not HAS_AIOHTTP:
        return []

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_config.timeout)
        ) as session:
            url = _config.datacenter_url
            params = {
                "reportName": "RPT_LICO_FN_CPD",
                "columns": "SECURITY_CODE",
                "pageNumber": 1,
                "pageSize": 5000,
                "sortTypes": -1,
                "sortColumns": "SECURITY_CODE",
            }
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                if data.get("success") and data.get("result", {}).get("data"):
                    return [row["SECURITY_CODE"] for row in data["result"]["data"]]
    except asyncio.TimeoutError:
        log.warning("异步获取股票代码超时")
    except aiohttp.ClientError as e:
        log.error(f"异步获取股票代码失败: {e}")
    except (KeyError, json.JSONDecodeError) as e:
        log.error(f"解析股票代码失败: {e}")
    return []
