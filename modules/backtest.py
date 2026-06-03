"""Backtest engine — equal-weight top-N rebalance simulator.

Pure-Python, deterministic given inputs. No I/O, no global state.
Suitable for both the HTTP API and ad-hoc analysis.

Inputs
------
The engine is data-shape-agnostic. Callers must pre-build two
mappings (see :class:`BacktestInput`):

* ``price_history: {date_str → {code: close_price}}`` — daily closes
* ``picks_by_date: {date_str → [code, ...]}`` — top-N picks per
  rebalance date (sorted, no duplicates)

If a date is missing from ``price_history`` the simulator falls
forward to the next available trading day; if there is no later
day the run stops.

Output
------
:class:`BacktestResult` contains:

* ``equity_curve``        — list of ``{date, value}``
* ``trades``              — list of executed trades
* ``metrics``             — total / annualized return, Sharpe, max DD, win rate, turnover
* ``config``              — the input config (echoed back)

Performance model
-----------------
* All-in / all-out: each rebalance sells 100 % of current positions
  then re-allocates equally across the new top-N.
* Cash is held between rebalances.
* No transaction costs, slippage, taxes, or dividends. Add these
  in v20 by extending :class:`BacktestConfig` with cost fields.

Why pure-Python?
    The HTTP layer can swap in any price source at the
    boundary (Eastmoney, AKShare, local CSV) without the engine
    knowing. Tests run in milliseconds because there's no I/O.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class BacktestError(Exception):
    """Base class for backtest errors."""


class ConfigError(BacktestError):
    """Invalid backtest configuration."""


class DataError(BacktestError):
    """Empty / inconsistent input data."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    top_n: int = 5
    rebalance_every: int = 5            # trading days between rebalances
    risk_free_rate: float = 0.02        # annual
    trading_days_per_year: int = 252
    min_history: int = 1                # min price-history length per code
    name: str = ""                      # human label (optional)

    def validate(self) -> None:
        if self.initial_capital <= 0:
            raise ConfigError("initial_capital must be > 0")
        if self.top_n < 1:
            raise ConfigError("top_n must be >= 1")
        if self.rebalance_every < 1:
            raise ConfigError("rebalance_every must be >= 1")
        if not (0.0 <= self.risk_free_rate < 1.0):
            raise ConfigError("risk_free_rate must be in [0, 1)")
        if self.trading_days_per_year < 1:
            raise ConfigError("trading_days_per_year must be >= 1")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
@dataclass
class BacktestInput:
    price_history: Mapping[str, Mapping[str, float]]
    picks_by_date: Mapping[str, Sequence[str]]
    config: BacktestConfig = field(default_factory=BacktestConfig)

    def validate(self) -> None:
        self.config.validate()
        if not self.price_history:
            raise DataError("price_history is empty")
        if not self.picks_by_date:
            raise DataError("picks_by_date is empty")


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    date: str
    side: str                # "buy" | "sell"
    code: str
    shares: int
    price: float
    amount: float            # shares * price
    cash_after: float


@dataclass
class BacktestResult:
    id: str
    name: str
    config: Dict[str, Any]
    started_at: str          # ISO date when first rebalance ran
    ended_at: str            # ISO date of last equity point
    equity_curve: List[Dict[str, Any]]     # {date, value, cash, positions}
    trades: List[Dict[str, Any]]
    metrics: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "config": self.config,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "equity_curve": self.equity_curve,
            "trades": self.trades,
            "metrics": self.metrics,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def _sorted_dates(history: Mapping[str, Any]) -> List[str]:
    return sorted(history.keys())


def _days_between(a: str, b: str) -> int:
    """Calendar-day gap between two ISO date strings. Negative if reversed."""
    from datetime import date
    ya, ma, da = (int(x) for x in a.split("-"))
    yb, mb, db = (int(x) for x in b.split("-"))
    return (date(yb, mb, db) - date(ya, ma, da)).days


def _next_date(
    history: Mapping[str, Mapping[str, float]], after: str
) -> Optional[str]:
    """Return the next trading date strictly after ``after``."""
    candidates = [d for d in history if d > after]
    return min(candidates) if candidates else None


def _portfolio_value(
    cash: float,
    holdings: Dict[str, int],
    prices: Mapping[str, float],
) -> float:
    v = cash
    for code, shares in holdings.items():
        p = prices.get(code)
        if p is not None:
            v += shares * p
    return v


def _metric_block(
    equity: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    cfg: BacktestConfig,
) -> Dict[str, Any]:
    """Compute summary metrics from the equity curve and trade log."""
    if len(equity) < 2:
        return {
            "total_return_pct": 0.0,
            "annualized_return_pct": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "win_rate_pct": 0.0,
            "trading_days": 0,
            "trades": 0,
            "turnover_per_rebalance": 0.0,
        }

    start = equity[0]["value"]
    end = equity[-1]["value"]
    total_return = (end / start - 1.0) if start > 0 else 0.0

    days = len(equity) - 1
    years = days / cfg.trading_days_per_year if cfg.trading_days_per_year else 0
    if years > 0 and end > 0 and start > 0:
        annualized = (end / start) ** (1.0 / years) - 1.0
    else:
        annualized = 0.0

    # Daily log returns
    rets: List[float] = []
    for a, b in zip(equity, equity[1:]):
        prev = a["value"]
        cur = b["value"]
        if prev > 0 and cur > 0:
            rets.append(math.log(cur / prev))

    # Sharpe (excess over risk-free, annualised)
    if rets:
        n = len(rets)
        mean = sum(rets) / n
        # population variance
        if n > 1:
            var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        else:
            var = 0.0
        std = math.sqrt(var) if var > 0 else 0.0
        daily_rf = cfg.risk_free_rate / cfg.trading_days_per_year
        sharpe = (
            (mean - daily_rf) / std * math.sqrt(cfg.trading_days_per_year)
            if std > 0
            else 0.0
        )
    else:
        sharpe = 0.0

    # Max drawdown
    peak = equity[0]["value"]
    max_dd = 0.0
    for p in equity:
        v = p["value"]
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v / peak) - 1.0
            if dd < max_dd:
                max_dd = dd

    # Win rate — fraction of positive daily returns
    positive = sum(1 for r in rets if r > 0)
    win_rate = (positive / len(rets) * 100.0) if rets else 0.0

    # Turnover per rebalance — fraction of portfolio traded
    rebalances = [
        t for t in trades
        if t["side"] == "buy" and t["date"] >= equity[0]["date"]
    ]
    if rebalances:
        # Sum of buy amounts at each rebalance, divided by portfolio value
        by_date: Dict[str, float] = {}
        for t in rebalances:
            by_date[t["date"]] = by_date.get(t["date"], 0.0) + t["amount"]
        first_rebal = next(iter(by_date))
        # Value just before first rebalance
        pre_value = next(
            (p["value"] for p in equity if p["date"] >= first_rebal),
            start,
        )
        if pre_value > 0:
            avg_turnover = sum(by_date.values()) / pre_value / max(
                1, len(by_date)
            )
        else:
            avg_turnover = 0.0
    else:
        avg_turnover = 0.0

    return {
        "total_return_pct": round(total_return * 100.0, 4),
        "annualized_return_pct": round(annualized * 100.0, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "win_rate_pct": round(win_rate, 2),
        "trading_days": days,
        "trades": len(trades),
        "turnover_per_rebalance": round(avg_turnover, 4),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run(input_: BacktestInput) -> BacktestResult:
    """Execute a backtest. Pure function — no I/O."""
    input_.validate()

    cfg = input_.config
    history = input_.price_history
    picks = input_.picks_by_date

    sorted_dates = _sorted_dates(history)
    if not sorted_dates:
        raise DataError("price_history has no dates")

    # Sort rebalance dates and filter by the minimum-day gap.
    # ``rebalance_every`` is a cadence constraint (min trading days between
    # rebalances), not a list index — so the previous index-based filter
    # silently dropped all but the first date when few candidates were
    # supplied.  Now we walk the sorted list and keep a date only if it is
    # at least ``rebalance_every`` days past the last kept one.
    rebal_dates_all = sorted(p for p in picks if p in history)
    if not rebal_dates_all:
        # Fallback: rebalance on every trading day using any provided pick set.
        rebal_dates_all = sorted_dates
    rebal_set: set = set()
    last_kept: Optional[str] = None
    for d in rebal_dates_all:
        if last_kept is None or _days_between(last_kept, d) >= cfg.rebalance_every:
            rebal_set.add(d)
            last_kept = d
    # Always include the first available date as the run's start.
    start_date = sorted_dates[0]
    if start_date not in rebal_set:
        rebal_set.add(start_date)

    cash = float(cfg.initial_capital)
    holdings: Dict[str, int] = {}
    equity: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    started_at = ""
    ended_at = ""

    rebal_set_sorted = sorted(rebal_set)

    for d in sorted_dates:
        prices = history[d]

        # Mark-to-market at every date.
        value = _portfolio_value(cash, holdings, prices)
        equity.append({
            "date": d,
            "value": round(value, 2),
            "cash": round(cash, 2),
            "positions": dict(holdings),
        })
        if not started_at:
            started_at = d
        ended_at = d

        if d in rebal_set:
            # Liquidate
            for code, shares in list(holdings.items()):
                p = prices.get(code)
                if p is None or p <= 0 or shares <= 0:
                    continue
                proceeds = shares * p
                cash += proceeds
                trades.append({
                    "date": d, "side": "sell", "code": code,
                    "shares": shares, "price": round(p, 4),
                    "amount": round(proceeds, 2),
                    "cash_after": round(cash, 2),
                })
            holdings = {}

            # Re-pick — fall forward if the exact date has no picks.
            pick_codes: Sequence[str] = []
            pdate = d
            while pick_codes == [] and pdate is not None:
                pick_codes = picks.get(pdate, []) or []
                if pick_codes:
                    break
                pdate = _next_date(picks, pdate)
            target = list(pick_codes[: cfg.top_n])

            if target and cash > 0:
                per = cash / len(target)
                for code in target:
                    p = prices.get(code)
                    if p is None or p <= 0:
                        continue
                    shares = int(per // p)
                    if shares <= 0:
                        continue
                    cost = shares * p
                    cash -= cost
                    holdings[code] = shares
                    trades.append({
                        "date": d, "side": "buy", "code": code,
                        "shares": shares, "price": round(p, 4),
                        "amount": round(cost, 2),
                        "cash_after": round(cash, 2),
                    })

    metrics = _metric_block(equity, trades, cfg)
    return BacktestResult(
        id=f"bt_{int(time.time())}_{uuid.uuid4().hex[:8]}",
        name=cfg.name,
        config=asdict(cfg),
        started_at=started_at,
        ended_at=ended_at,
        equity_curve=equity,
        trades=trades,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def save_result(result: BacktestResult, directory: str) -> str:
    """Persist a result to ``<directory>/<id>.json``. Returns the path."""
    import json
    import os

    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{result.id}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def load_result(path: str) -> Dict[str, Any]:
    """Load a previously-saved result from disk."""
    import json
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


__all__ = [
    "BacktestConfig",
    "BacktestInput",
    "BacktestResult",
    "BacktestError",
    "ConfigError",
    "DataError",
    "run",
    "save_result",
    "load_result",
]
