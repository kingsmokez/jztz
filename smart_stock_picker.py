"""智能选股CLI版本 - 使用统一模块"""

from __future__ import annotations

import json
import sys
import time

from modules.config import load_config
from modules.data_fetcher import get_realtime_quotes, get_financial_data
from modules.logger import log
from modules.models import StockQuote, filter_eligible_stocks
from modules.scoring import quick_score, full_score, rank_stocks
from modules.technical import evaluate_technical
from modules.stock_picker import run_picker


def main() -> None:
    config = load_config()
    log.info("=" * 60)
    log.info("价值投资之王智能选股系统 v20")
    log.info("=" * 60)

    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "daily":
            result = run_picker()
        elif command == "test":
            log.info("测试模式: 获取10只股票行情")
            quotes = get_realtime_quotes(["600519", "000858", "601318"])
            for code, q in quotes.items():
                log.info(f"  {q.name}({code}): {q.price} 涨跌{q.change_pct}% PE={q.pe}")
            return
        else:
            log.info(f"未知命令: {command}")
            log.info("用法: python smart_stock_picker.py [daily|test]")
            return
    else:
        result = run_picker()

    if result:
        log.info(f"\n选股结果 ({len(result)} 只):")
        log.info("-" * 80)
        for stock in result[:20]:
            log.info(
                f"  #{stock.get('rank',0):2d} {stock.get('name',''):6s}({stock.get('code','')}) "
                f"总分:{stock.get('total_score',0):5.1f} "
                f"价值:{stock.get('value_score',0):5.1f} "
                f"成长:{stock.get('growth_score',0):5.1f} "
                f"质量:{stock.get('quality_score',0):5.1f} "
                f"技术:{stock.get('tech_score',0):5.1f}"
            )
    else:
        log.info("未选出任何股票")


if __name__ == "__main__":
    main()
