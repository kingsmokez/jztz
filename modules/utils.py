"""公共工具函数 — 消除各选股模块的重复代码"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from modules.data_fetcher import get_stock_industry
from modules.logger import log


def fetch_industry_for_results(results: List[Dict[str, Any]], max_workers: int = 10) -> None:
    """为结果列表批量获取行业信息（线程池并发）

    在每个 stock dict 中添加 industry 和 sector 字段。
    失败时默认为 "未知" / "default"。
    """
    def _fetch(stock: Dict[str, Any]) -> None:
        try:
            info = get_stock_industry(stock["code"])
            stock["industry"] = info.get("industry", "未知")
            stock["sector"] = info.get("sector_type", "default")
        except Exception:
            stock["industry"] = "未知"
            stock["sector"] = "default"

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_fetch, results))


def batch_get_industry_map(codes: List[str], max_workers: int = 10) -> Dict[str, Dict[str, str]]:
    """批量获取行业映射 {code: {"industry": ..., "sector_type": ...}}

    用于评分前一次性获取所有候选股的行业信息，避免评分函数逐只调 API。
    """
    result: Dict[str, Dict[str, str]] = {}

    def _fetch(code: str) -> None:
        try:
            info = get_stock_industry(code)
            result[code] = {
                "industry": info.get("industry", "未知"),
                "sector_type": info.get("sector_type", "default"),
            }
        except Exception:
            result[code] = {"industry": "未知", "sector_type": "default"}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_fetch, codes))

    return result


__all__ = ["fetch_industry_for_results", "batch_get_industry_map"]
