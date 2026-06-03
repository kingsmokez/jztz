"""统一数据模型 - 消除腾讯API解析重复"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StockQuote:
    """统一股票行情数据模型"""

    code: str
    name: str
    price: float
    change_pct: float
    volume: float
    amount: float
    turnover: float
    pe: float
    pb: float
    market_cap: float  # 统一为亿元
    high: float
    low: float
    open: float
    prev_close: float
    volume_ratio: float = 0.0

    @property
    def is_st(self) -> bool:
        return "ST" in self.name or "st" in self.name

    @property
    def is_beijing(self) -> bool:
        return self.code.startswith("8") or self.code.startswith("4")

    @property
    def is_b_share(self) -> bool:
        return self.code.startswith("200") or self.code.startswith("900")

    @property
    def is_eligible(self) -> bool:
        """是否为可选股范围（排除ST/北交所/B股）"""
        return not self.is_st and not self.is_beijing and not self.is_b_share

    @classmethod
    def from_tencent_parts(cls, code: str, parts: list[str]) -> Optional["StockQuote"]:
        """从腾讯API返回的parts列表解析 - 统一解析入口

        腾讯行情API字段映射:
        parts[1]  = 股票名
        parts[3]  = 当前价
        parts[4]  = 昨收
        parts[5]  = 今开
        parts[6]  = 成交量(手)
        parts[32] = 涨跌幅(%)
        parts[33] = 最高价
        parts[34] = 最低价
        parts[37] = 成交额(万)
        parts[38] = 换手率(%)
        parts[39] = 市盈率(动)
        parts[44] = 总市值(亿)
        parts[46] = 市净率
        """
        try:
            if len(parts) < 48:
                return None

            name = parts[1].strip()
            price = _safe_float(parts[3])
            prev_close = _safe_float(parts[4])
            open_price = _safe_float(parts[5])
            volume = _safe_float(parts[6])
            change_pct = _safe_float(parts[32])
            amount = _safe_float(parts[37]) * 10000 if _safe_float(parts[37]) > 0 else 0  # 万→元
            turnover = _safe_float(parts[38])  # 换手率%
            pe = _safe_float(parts[39])
            pb = _safe_float(parts[46])  # 市净率
            market_cap = _safe_float(parts[44])  # 总市值(亿)

            # 高低价
            high = _safe_float(parts[33]) if len(parts) > 33 and _safe_float(parts[33]) > 0 else price
            low = _safe_float(parts[34]) if len(parts) > 34 and _safe_float(parts[34]) > 0 else price
            volume_ratio = _safe_float(parts[49]) if len(parts) > 49 else 0.0
            if pb <= 0 and pe > 0 and prev_close > 0:
                pb = 0

            return cls(
                code=code,
                name=name,
                price=price,
                change_pct=change_pct,
                volume=volume,
                amount=amount,
                turnover=turnover,
                pe=pe,
                pb=pb,
                market_cap=market_cap,
                high=high,
                low=low,
                open=open_price,
                prev_close=prev_close,
                volume_ratio=volume_ratio,
            )
        except (IndexError, ValueError, TypeError):
            return None


@dataclass(frozen=True)
class FinancialData:
    """财务数据模型"""

    code: str
    name: str
    pe: float
    pb: float
    roe: float
    market_cap: float  # 亿元
    revenue_growth: float
    profit_growth: float
    debt_ratio: float
    gross_margin: float
    net_margin: float = 0.0


@dataclass(frozen=True)
class StockScore:
    """评分结果模型"""

    code: str
    name: str
    total_score: float
    value_score: float
    growth_score: float
    quality_score: float
    tech_score: float
    momentum_score: float
    rank: int = 0


def _safe_float(value: str, default: float = 0.0) -> float:
    """安全转换为float"""
    try:
        cleaned = re.sub(r"[^\d.\-]", "", str(value))
        return float(cleaned) if cleaned else default
    except (ValueError, TypeError):
        return default


def filter_eligible_stocks(quotes: dict[str, StockQuote]) -> dict[str, StockQuote]:
    """统一过滤函数 - 排除ST/北交所/B股/停牌/零价"""
    return {
        code: q
        for code, q in quotes.items()
        if q.is_eligible and q.price > 0
    }
