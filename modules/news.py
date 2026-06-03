"""新闻情感分析模块"""

from __future__ import annotations

from modules.logger import log

_POSITIVE_WORDS: tuple[str, ...] = (
    "涨停", "大涨", "利好", "突破", "新高", "反弹", "强势", "上涨",
    "增持", "回购", "分红", "业绩增长", "超预期", "创新高", "龙头",
)

_NEGATIVE_WORDS: tuple[str, ...] = (
    "跌停", "大跌", "利空", "破位", "新低", "暴跌", "弱势", "下跌",
    "减持", "亏损", "退市", "违规", "处罚", "暴雷", "违约",
)


def analyze_sentiment(text: str) -> float:
    """分析文本情感倾向，返回 -1.0 ~ 1.0"""
    if not text:
        return 0.0

    positive = sum(1 for w in _POSITIVE_WORDS if w in text)
    negative = sum(1 for w in _NEGATIVE_WORDS if w in text)
    total = positive + negative

    if total == 0:
        return 0.0

    return (positive - negative) / total


def get_sentiment_label(score: float) -> str:
    """情感标签"""
    if score > 0.3:
        return "利好"
    if score < -0.3:
        return "利空"
    return "中性"
