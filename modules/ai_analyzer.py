"""AI分析模块"""

from __future__ import annotations

import json
from typing import Optional

from modules.data_fetcher import get_realtime_quotes, get_financial_data, search_stock
from modules.logger import log
from modules.models import StockQuote
from modules.scoring import full_score
from modules.news import analyze_sentiment, get_sentiment_label


def analyze_stock(code: str) -> dict:
    """AI综合分析单只股票"""
    try:
        quotes = get_realtime_quotes([code])
        if code not in quotes:
            return {"success": False, "error": f"未找到股票: {code}"}

        quote = quotes[code]
        financials = get_financial_data([code])
        f = financials.get(code)

        # 评分
        score_result = full_score(quote, f)

        # 情感分析
        name = quote.name
        sentiment = analyze_sentiment(name)
        sentiment_label = get_sentiment_label(sentiment)

        return {
            "success": True,
            "code": code,
            "name": name,
            "price": quote.price,
            "change_pct": quote.change_pct,
            "scores": score_result,
            "sentiment": {
                "score": round(sentiment, 2),
                "label": sentiment_label,
            },
            "financial": {
                "pe": f.pe if f else quote.pe,
                "pb": f.pb if f else quote.pb,
                "roe": f.roe if f else 0,
                "market_cap": f.market_cap if f else quote.market_cap,
                "revenue_growth": f.revenue_growth if f else 0,
                "profit_growth": f.profit_growth if f else 0,
                "debt_ratio": f.debt_ratio if f else 0,
                "gross_margin": f.gross_margin if f else 0,
            } if f else None,
        }
    except Exception as e:
        log.error(f"AI分析失败: {code}, {e}", exc_info=True)
        return {"success": False, "error": str(e)}
