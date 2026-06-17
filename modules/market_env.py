"""Market environment detection for dynamic strategy adjustment."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from modules.http_client import session
from modules.logger import log


class MarketEnv:
    """Detects market regime and returns safe multipliers for strategies."""

    def __init__(self) -> None:
        self.status: str = "unknown"
        self.change_pct: float = 0.0
        self.trend: str = "unknown"
        self.volatility: str = "normal"
        self.multiplier: float = 1.0
        self.updated_at: Optional[str] = None
        self._refresh()

    def _refresh(self) -> None:
        """Fetch and analyze current market state."""
        try:
            import re
            url = "https://hq.sinajs.cn/list=sh000300"
            resp = session.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}, timeout=10)
            resp.encoding = "gbk"
            match = re.search(r'"([^"]+)"', resp.text.strip())
            if not match:
                return

            parts = match.group(1).split(",")
            if len(parts) < 5:
                return

            prev_close = float(parts[2]) if parts[2] else 0
            current = float(parts[3]) if parts[3] else 0
            if prev_close <= 0:
                return

            self.change_pct = round((current - prev_close) / prev_close * 100, 2)

            if self.change_pct > 2:
                self.status = "strong_up"
            elif self.change_pct > 0.5:
                self.status = "up"
            elif self.change_pct > -0.5:
                self.status = "range"
            elif self.change_pct > -2:
                self.status = "down"
            else:
                self.status = "strong_down"

            trend, vol = self._analyze_trend_and_volatility()
            self.trend = trend
            self.volatility = vol
            self._calc_multiplier()
            self.updated_at = datetime.now().strftime("%H:%M:%S")

        except Exception as e:
            log.debug(f"MarketEnv refresh failed: {e}")

    def _analyze_trend_and_volatility(self) -> Tuple[str, str]:
        """Analyze CSI300 K-line to determine trend and volatility."""
        try:
            r = session.get(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                params={"param": "sz399300,day,,,30,qfq"},
                timeout=8,
            )
            data = r.json()
            stock_data = data.get("data", {}).get("sz399300", {})
            klines = stock_data.get("qfqday", stock_data.get("day", []))
            if not klines or len(klines) < 20:
                return "unknown", "normal"

            closes = [float(k[2]) for k in klines if k[2]]
            if len(closes) < 20:
                return "unknown", "normal"

            ma5 = sum(closes[-5:]) / 5
            ma10 = sum(closes[-10:]) / 10
            ma20 = sum(closes[-20:]) / 20
            cur = closes[-1]

            if cur > ma5 > ma10 > ma20:
                trend = "bull"
            elif cur < ma5 < ma10 < ma20:
                trend = "bear"
            else:
                trend = "range"

            returns = [abs((closes[i] - closes[i-1]) / closes[i-1]) for i in range(1, len(closes))]
            avg_vol = sum(returns) / len(returns) if returns else 0
            if avg_vol > 0.025:
                volatility = "high"
            elif avg_vol < 0.01:
                volatility = "low"
            else:
                volatility = "normal"
            return trend, volatility
        except Exception:
            return "unknown", "normal"

    def _calc_multiplier(self) -> None:
        m = 1.0
        # 趋势影响
        if self.trend == "bear":
            m -= 0.4
        elif self.trend == "range":
            m -= 0.15
        # 当日涨跌影响
        if self.status == "strong_down":
            m -= 0.3
        elif self.status == "down":
            m -= 0.15
        elif self.status == "strong_up":
            m += 0.1
        # 波动率影响
        if self.volatility == "high":
            m -= 0.15
        # 过渡期额外保守：趋势和当日行情矛盾时减分
        # 熊市中的上涨日（可能是反弹陷阱）
        if self.trend == "bear" and self.status in ("up", "strong_up"):
            m -= 0.1  # 反弹可能是诱多，需谨慎
        # 牛市中的下跌日（可能是回调，不需要太悲观）
        # (no penalty - bull market dips are buying opportunities)
        self.multiplier = max(0.3, min(1.3, m))

    def can_pick(self) -> bool:
        """Whether it's safe to run stock picking at all.
        
        Stop conditions (any one triggers pause):
        1. Bear trend + strong down day (original)
        2. Bear trend + down day + high volatility (恐慌性下跌)
        3. Strong down day regardless of trend (暴跌日)
        4. Bear trend + consecutive down days (连续下跌)
        """
        # Condition 1: 熊市 + 大跌
        if self.trend == "bear" and self.status == "strong_down":
            return False
        # Condition 2: 熊市 + 下跌 + 高波动 (恐慌蔓延)
        if self.trend == "bear" and self.status == "down" and self.volatility == "high":
            return False
        # Condition 3: 暴跌日 (跌幅 > 3%)，无论趋势
        if self.change_pct < -3.0:
            return False
        # Condition 4: 熊市 + 高波动 (市场极不稳定)
        if self.trend == "bear" and self.volatility == "high":
            return False
        return True

    def adjusted_top_n(self, base: int) -> int:
        """Return reduced/increased pick count based on market."""
        return max(3, int(base * self.multiplier))

    def score_multiplier(self) -> float:
        """Return multiplier for scoring thresholds."""
        if self.trend == "bear":
            return 1.15
        return max(0.85, self.multiplier)

    def position_size_multiplier(self) -> float:
        return max(0.3, self.multiplier)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "change_pct": self.change_pct,
            "trend": self.trend,
            "volatility": self.volatility,
            "multiplier": self.multiplier,
        }


_env_cache: Optional[MarketEnv] = None
_env_cache_time: float = 0


def get_market_env(force_refresh: bool = False) -> MarketEnv:
    """Get cached MarketEnv (60s cache)."""
    global _env_cache, _env_cache_time
    import time
    now = time.time()
    if _env_cache is None or force_refresh or (now - _env_cache_time > 60):
        _env_cache = MarketEnv()
        _env_cache_time = now
        log.info(
            f"MarketEnv: {_env_cache.status}, trend={_env_cache.trend}, "
            f"mult={_env_cache.multiplier:.2f}"
        )
    return _env_cache
