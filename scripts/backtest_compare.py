"""回测对比脚本 — 对4个选股策略进行历史回测并生成对比报告。

用法:  python scripts/backtest_compare.py
数据源: 腾讯财经K线API (无需额外依赖)
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.backtest import (
    BacktestConfig,
    BacktestInput,
    run as run_backtest,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
LOOKBACK_DAYS = 60           # 回看交易日数
TOP_N = 10                   # 每期选股数
REBALANCE_EVERY = 5          # 每N个交易日调仓
INITIAL_CAPITAL = 100_000.0

KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com/",
}

# 代表性A股池 (覆盖主要行业, 100只)
REPRESENTATIVE_STOCKS = [
    # 金融
    "000001", "002142", "600000", "600016", "600036", "601318", "601166",
    "601328", "601398", "601939", "601288", "600030", "000776",
    # 白酒/食品
    "600519", "000858", "002304", "000568", "600887", "603288", "600809",
    # 医药
    "600276", "000538", "300015", "300122", "300347", "002007", "600196",
    "300760", "000661",
    # 新能源/电池
    "300750", "002594", "601012", "002460", "002466", "300014", "600438",
    "300274", "688981",
    # 科技/TMT
    "002415", "002230", "300059", "000725", "002475", "300124", "601138",
    "603501", "002049", "300782",
    # 家电
    "000651", "002032", "000333", "600690",
    # 汽车
    "600104", "000625", "002625", "601633", "300124",
    # 地产/基建
    "000002", "600048", "001979", "600585", "601668", "601800",
    # 有色/化工
    "600111", "002460", "000792", "603799", "002709", "000426",
    # 煤炭/能源
    "601088", "600028", "601857", "600188",
    # 电子/半导体
    "002371", "603986", "300408", "300433", "002049",
    # 交通运输
    "601111", "600029", "600009", "601006",
    # 军工
    "600760", "000768", "600893", "002013",
    # 其他大市值
    "601899", "600900", "601628", "600050", "002120", "300498",
    "000063", "002352", "601888", "600570", "600309",
]

_session: requests.Session | None = None


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
    return _session


# ---------------------------------------------------------------------------
# Fetch K-line data from Tencent
# ---------------------------------------------------------------------------
def _code_to_param(code: str) -> str:
    """Convert stock code to Tencent API param prefix."""
    if code.startswith(("6", "9")):
        return f"sh{code}"
    else:
        return f"sz{code}"


def fetch_all_kline(
    codes: List[str], lookback: int = 60
) -> Dict[str, Dict[str, float]]:
    """批量获取日K线收盘价 (前复权)。

    Returns: {date_str: {code: close_price}}
    """
    print(f"[1/3] 获取K线数据 ({len(codes)} 只股票) ...")
    price_history: Dict[str, Dict[str, float]] = defaultdict(dict)
    success = 0

    for i, code in enumerate(codes):
        param = f"{_code_to_param(code)},day,,,{lookback + 10},qfq"
        try:
            r = get_session().get(KLINE_URL, params={"param": param}, timeout=10)
            data = r.json()
            stock_data = data.get("data", {}).get(_code_to_param(code), {})
            klines = stock_data.get("qfqday", stock_data.get("day", []))
            if klines:
                for k in klines:
                    # Format: [date, open, close, high, low, volume]
                    date_str = k[0]
                    close = float(k[2])
                    if close > 0:
                        price_history[date_str][code] = close
                success += 1
        except Exception:
            continue

        # Progress every 20 stocks
        if (i + 1) % 20 == 0:
            print(f"  已处理 {i+1}/{len(codes)}, 成功 {success}")

        time.sleep(0.08)  # Rate limit

    # Trim to last `lookback` dates
    sorted_dates = sorted(price_history.keys())[-lookback:]
    result = {d: price_history[d] for d in sorted_dates}
    print(f"  完成: {len(result)} 个交易日, {success} 只股票有数据")
    return result


def fetch_benchmark_kline(lookback: int = 60) -> Dict[str, Dict[str, float]]:
    """获取沪深300指数K线作为基准。"""
    print(f"  获取沪深300基准 ...")
    price_history: Dict[str, Dict[str, float]] = defaultdict(dict)
    param = f"sz399300,day,,,{lookback + 10},qfq"
    try:
        r = get_session().get(KLINE_URL, params={"param": param}, timeout=10)
        data = r.json()
        stock_data = data.get("data", {}).get("sz399300", {})
        klines = stock_data.get("qfqday", stock_data.get("day", []))
        if klines:
            for k in klines:
                date_str = k[0]
                close = float(k[2])
                if close > 0:
                    price_history[date_str]["CSI300"] = close
    except Exception as e:
        print(f"  WARNING: 获取基准失败: {e}")

    sorted_dates = sorted(price_history.keys())[-lookback:]
    return {d: price_history[d] for d in sorted_dates}


# ---------------------------------------------------------------------------
# Strategy proxy picking logic
# ---------------------------------------------------------------------------
def _rank_by_momentum(
    price_history: Dict[str, Dict[str, float]], dates: List[str], top_n: int,
    momentum_days: int = 5,
) -> List[str]:
    """动量排名：选最近N日涨幅最大的股票 (对应强势选股)。"""
    if len(dates) < momentum_days:
        return []
    today_idx = len(dates) - 1
    start_idx = max(0, today_idx - momentum_days)

    codes_set = set()
    for d in dates[start_idx : today_idx + 1]:
        codes_set.update(price_history.get(d, {}).keys())

    scores = []
    for code in codes_set:
        p_today = price_history.get(dates[today_idx], {}).get(code, 0)
        p_start = price_history.get(dates[start_idx], {}).get(code, 0)
        if p_today > 0 and p_start > 0:
            chg = (p_today - p_start) / p_start
            scores.append((code, chg))

    scores.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scores[:top_n]]


def _rank_by_breakout(
    price_history: Dict[str, Dict[str, float]], dates: List[str], top_n: int,
) -> List[str]:
    """突破选股：选近期接近N日高点的股票 (对应WP2选股)。"""
    if len(dates) < 20:
        return _rank_by_momentum(price_history, dates, top_n)

    today_idx = len(dates) - 1
    lookback_high = 20
    start_idx = max(0, today_idx - lookback_high)
    today = dates[today_idx]

    codes_set = set()
    for d in dates[start_idx : today_idx + 1]:
        codes_set.update(price_history.get(d, {}).keys())

    scores = []
    for code in codes_set:
        p_today = price_history.get(today, {}).get(code, 0)
        if p_today <= 0:
            continue
        max_past = max(
            (price_history.get(d, {}).get(code, 0) for d in dates[start_idx:today_idx]),
            default=0,
        )
        if max_past > 0:
            near_high = p_today / max_past
            scores.append((code, near_high))

    scores.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scores[:top_n]]


def _rank_by_multi_factor(
    price_history: Dict[str, Dict[str, float]], dates: List[str], top_n: int,
) -> List[str]:
    """多因子排名：动量+突破 (对应每日选股)。"""
    if len(dates) < 10:
        return _rank_by_momentum(price_history, dates, top_n)

    today_idx = len(dates) - 1
    today = dates[today_idx]

    # 5-day momentum
    mom_start = max(0, today_idx - 5)
    mom_codes = {}
    all_codes = set()
    for d in dates[mom_start : today_idx + 1]:
        all_codes.update(price_history.get(d, {}).keys())
    for code in all_codes:
        p_t = price_history.get(today, {}).get(code, 0)
        p_s = price_history.get(dates[mom_start], {}).get(code, 0)
        if p_t > 0 and p_s > 0:
            mom_codes[code] = (p_t - p_s) / p_s

    # 20-day near-high
    high_start = max(0, today_idx - 20)
    breakout_scores = {}
    all_codes2 = set()
    for d in dates[high_start : today_idx + 1]:
        all_codes2.update(price_history.get(d, {}).keys())
    for code in all_codes2:
        p_t = price_history.get(today, {}).get(code, 0)
        if p_t <= 0:
            continue
        max_past = max(
            (price_history.get(d, {}).get(code, 0) for d in dates[high_start:today_idx]),
            default=0,
        )
        if max_past > 0:
            breakout_scores[code] = p_t / max_past

    # Combined score
    all_codes = set(mom_codes) | set(breakout_scores)
    combined = []
    for code in all_codes:
        mom = mom_codes.get(code, 0)
        brk = breakout_scores.get(code, 0)
        score = mom * 0.6 + max(0, (brk - 0.95)) * 0.4
        combined.append((code, score))

    combined.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in combined[:top_n]]


def _rank_by_trend(
    price_history: Dict[str, Dict[str, float]], dates: List[str], top_n: int,
) -> List[str]:
    """趋势选股：选持续上涨+近期加速的股票 (对应集合竞价选股逻辑)。"""
    if len(dates) < 10:
        return _rank_by_momentum(price_history, dates, top_n)

    today_idx = len(dates) - 1

    # 计算3日和10日动量
    scores = []
    all_codes = set()
    for d in dates[max(0, today_idx - 10) : today_idx + 1]:
        all_codes.update(price_history.get(d, {}).keys())

    for code in all_codes:
        p_today = price_history.get(dates[today_idx], {}).get(code, 0)
        if p_today <= 0:
            continue

        # Short-term momentum (3-day)
        s3_idx = max(0, today_idx - 3)
        p_s3 = price_history.get(dates[s3_idx], {}).get(code, 0)

        # Medium-term momentum (10-day)
        s10_idx = max(0, today_idx - 10)
        p_s10 = price_history.get(dates[s10_idx], {}).get(code, 0)

        if p_s3 > 0 and p_s10 > 0:
            mom3 = (p_today - p_s3) / p_s3
            mom10 = (p_today - p_s10) / p_s10
            # Reward acceleration (3-day > 10-day)
            accel = mom3 - mom10
            score = mom3 * 0.5 + mom10 * 0.3 + max(0, accel) * 0.2
            scores.append((code, score))

    scores.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scores[:top_n]]


# ---------------------------------------------------------------------------
# Generate picks and run backtest
# ---------------------------------------------------------------------------
STRATEGY_RANKERS = {
    "daily": _rank_by_multi_factor,
    "strong": _rank_by_momentum,
    "auction": _rank_by_trend,
    "wp2": _rank_by_breakout,
}


def run_strategy_backtest(
    price_history: Dict[str, Dict[str, float]],
    dates: List[str],
    strategy_name: str,
) -> Dict[str, Any] | None:
    """执行单策略回测。"""
    rank_fn = STRATEGY_RANKERS.get(strategy_name, _rank_by_multi_factor)

    picks_by_date: Dict[str, List[str]] = {}
    for i in range(0, len(dates), REBALANCE_EVERY):
        date = dates[i]
        period_dates = dates[: i + 1]
        picked = rank_fn(price_history, period_dates, TOP_N)
        if picked:
            picks_by_date[date] = picked

    if not picks_by_date:
        return None

    config = BacktestConfig(
        initial_capital=INITIAL_CAPITAL,
        top_n=TOP_N,
        rebalance_every=REBALANCE_EVERY,
        name=strategy_name,
    )
    inp = BacktestInput(
        price_history=price_history,
        picks_by_date=picks_by_date,
        config=config,
    )
    result = run_backtest(inp)
    return result.to_dict()


def run_benchmark(
    benchmark_kline: Dict[str, Dict[str, float]],
    price_history: Dict[str, Dict[str, float]],
    dates: List[str],
) -> Dict[str, Any] | None:
    """运行沪深300基准回测。"""
    picks_by_date = {}
    for i in range(0, len(dates), REBALANCE_EVERY):
        if dates[i] in benchmark_kline:
            picks_by_date[dates[i]] = ["CSI300"]

    if not picks_by_date:
        return None

    combined = {}
    for d in dates:
        combined[d] = {}
        if d in price_history:
            combined[d].update(price_history[d])
        if d in benchmark_kline:
            combined[d].update(benchmark_kline[d])

    config = BacktestConfig(
        initial_capital=INITIAL_CAPITAL,
        top_n=1,
        rebalance_every=REBALANCE_EVERY,
        name="CSI300基准",
    )
    inp = BacktestInput(
        price_history=combined,
        picks_by_date=picks_by_date,
        config=config,
    )
    result = run_backtest(inp)
    return result.to_dict()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def print_comparison_table(results: Dict[str, Dict[str, Any]]):
    """打印对比表格。"""
    print("\n" + "=" * 100)
    print("  A股选股策略回测对比报告")
    print("=" * 100)

    name_map = {
        "CSI300基准": "沪深300基准",
        "daily": "每日选股",
        "strong": "强势选股",
        "auction": "集合竞价",
        "wp2": "WP2选股",
    }

    # Find max for highlighting best
    all_strategies = ["daily", "strong", "auction", "wp2"]
    active = [s for s in all_strategies if s in results]

    best_total = max(results[s]["metrics"]["total_return_pct"] for s in active) if active else 0
    best_sharpe = max(results[s]["metrics"]["sharpe"] for s in active) if active else 0
    best_annual = max(results[s]["metrics"]["annualized_return_pct"] for s in active) if active else 0
    best_dd = min(results[s]["metrics"]["max_drawdown_pct"] for s in active) if active else 1

    header = f"{'指标':<20}"
    for sid in ["CSI300基准"] + active:
        display = name_map.get(sid, sid)
        header += f" {display:<16}"
    print(header)
    print("-" * 100)

    metric_defs = [
        ("total_return_pct", "\u603b\u6536\u76ca\u7387(%)", "total_return_pct", 1, best_total),
        ("annualized_return_pct", "\u5e74\u5316\u6536\u76ca\u7387(%)", "annualized_return_pct", 1, best_annual),
        ("sharpe", "\u590f\u666e\u6bd4\u7387", "sharpe", 1, best_sharpe),
        ("max_drawdown_pct", "\u6700\u5927\u56de\u64a4(%)", "max_drawdown_pct", 1, best_dd, True),
        ("win_rate_pct", "\u80dc\u7387(%)", "win_rate_pct", 1, None),
        ("final_value", "\u6700\u7ec8\u8d44\u4ea7(\u5143)", "final_value", 1, None),
        ("trades", "\u4ea4\u6613\u6b21\u6570", "trades", 1, None),
        ("turnover_per_rebalance", "\u6362\u624b\u7387(%)", "turnover_per_rebalance", 1, None),
    ]
    for i, mdef in enumerate(metric_defs):
        label = mdef[1]
        row = f"{label:<20}"
        for sid in ["CSI300基准"] + active:
            r = results.get(sid, {})
            metrics = r.get("metrics", {})
            key = mdef[2]
            multiplier = mdef[3]
            best_val = mdef[4] if len(mdef) > 4 else None
            lower_is_better = mdef[5] if len(mdef) > 5 else False

            val = metrics.get(key, 0) * multiplier

            # Mark best with * (except for benchmark)
            is_best = False
            if sid != "CSI300基准" and best_val is not None:
                if lower_is_better:
                    is_best = abs(val - best_val) < 0.001
                else:
                    is_best = abs(val - best_val) < 0.001

            if key == "final_value":
                formatted = f" {val:>11,.0f}    "
            elif key == "trades":
                formatted = f" {val:>11.0f}    "
            else:
                marker = " *" if is_best else "  "
                formatted = f" {val:>10.2f}{marker}   "

            row += formatted
        print(row)

    print("-" * 100)
    print(f"  回测参数: 初始资金={INITIAL_CAPITAL:,.0f}, TOP_N={TOP_N}, 调仓周期={REBALANCE_EVERY}天")
    print(f"  股票池: {len(REPRESENTATIVE_STOCKS)}只代表性A股 | 数据源: 腾讯财经")
    print("=" * 100)

    # 诊断与优化建议
    print("\n  ◆ 诊断与优化建议:")
    print("  " + "-" * 80)

    suggestions = []
    for strategy_id in active:
        r = results.get(strategy_id, {})
        metrics = r.get("metrics", {})
        total_ret = metrics.get("total_return_pct", 0)
        sharpe = metrics.get("sharpe", 0)
        dd = metrics.get("max_drawdown_pct", 0) * 100
        annual = metrics.get("annualized_return_pct", 0)

        label = name_map.get(strategy_id, strategy_id)
        items = []

        # Performance assessment
        if total_ret == best_total:
            items.append("[OK] 总收益率最优")
        if sharpe == best_sharpe:
            items.append("[OK] 夏普比率最优")

        # Risk assessment
        if dd > 25:
            items.append(f"[!] 最大回撤 {dd:.1f}% 偏高, 建议加入止损/仓位控制")
        elif dd > 15:
            items.append(f"[*] 最大回撤 {dd:.1f}% 中等, 可考虑加入动态止盈")

        # Sharpe assessment
        if sharpe < 0:
            items.append("[X] 夏普为负, 策略当前可能不适合市场")
        elif sharpe < 0.5:
            items.append("○ 夏普偏低 (<0.5), 建议优化选股因子权重")
        elif sharpe >= 1.0:
            items.append("[OK] 夏普良好 (>=1.0)")

        # Return assessment
        bench_ret = results.get("CSI300基准", {}).get("metrics", {}).get("total_return_pct", 0)
        if bench_ret > 0 and total_ret > 0:
            alpha = total_ret - bench_ret
            if alpha > 5:
                items.append(f"^ 超额收益 {alpha:.1f}% (vs 沪深300)")
            elif alpha < -5:
                items.append(f"v 跑输基准 {abs(alpha):.1f}%")

        # Specific recommendations
        if annual < 5 and total_ret > 0:
            items.append("建议: 适当增加调仓频率, 捕捉更多波段机会")
        if metrics.get("turnover_per_rebalance", 0) > 2:
            items.append("建议: 降低换手率以减少交易损耗")

        result_str = "; ".join(items) if items else "表现一般, 无特殊建议"
        suggestions.append(f"  [{label}] {result_str}")

    for s in suggestions:
        print(s)

    # Summary
    print("\n  ◆ 综合建议:")
    best_strategy_name = name_map.get(active[0], active[0]) if active else "N/A"
    for s in active:
        if results[s]["metrics"]["total_return_pct"] == best_total:
            best_strategy_name = name_map.get(s, s)
            break

    if best_total > 0:
        print(f"    最优策略: {best_strategy_name}")
        print(f"    建议: 以此策略为主, 其他策略信号可作为辅助确认, 降低误判")
    print(f"    风险提示: 本回测基于{LOOKBACK_DAYS}个交易日数据, 样本有限。实盘前建议延长回测周期。")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    print("A股选股策略回测对比工具 (数据源: 腾讯财经)")
    print(f"参数: 回看{LOOKBACK_DAYS}日, TOP_{TOP_N}, 每{REBALANCE_EVERY}日调仓, 初始资金{INITIAL_CAPITAL:,.0f}")
    print()

    # 1. Fetch K-line
    price_history = fetch_all_kline(REPRESENTATIVE_STOCKS, LOOKBACK_DAYS)
    benchmark_kline = fetch_benchmark_kline(LOOKBACK_DAYS)

    dates = sorted(price_history.keys())
    if len(dates) < 15:
        print(f"ERROR: 数据不足 (只有{len(dates)}个交易日), 至少需要15天")
        sys.exit(1)

    benchmark_dates = sorted(benchmark_kline.keys())
    print(f"\n[2/3] 数据就绪: {len(dates)}日个股, {len(benchmark_dates)}日基准")
    print(f"  日期范围: {dates[0]} ~ {dates[-1]}")

    # 2. Run backtests
    print(f"\n[3/3] 运行回测 ...")
    strategies = ["daily", "strong", "auction", "wp2"]
    results: Dict[str, Dict[str, Any]] = {}

    for strategy_name in strategies:
        print(f"  {strategy_name} ... ", end="", flush=True)
        result = run_strategy_backtest(price_history, dates, strategy_name)
        if result:
            results[strategy_name] = result
            # Compute final_value from equity_curve
            ec = result.get("equity_curve", [])
            result["metrics"]["final_value"] = ec[-1]["value"] if ec else 0
            m = result["metrics"]
            ret = m.get("total_return_pct", 0)
            sh = m.get("sharpe", 0)
            dd = m.get("max_drawdown_pct", 0)
            print(f"收益 {ret:.1f}%, 夏普 {sh:.2f}, 回撤 {dd:.1f}%")
        else:
            print("无有效结果")

    # Benchmark
    print(f"  基准 ... ", end="", flush=True)
    bench_result = run_benchmark(benchmark_kline, price_history, dates)
    if bench_result:
        results["CSI300基准"] = bench_result
        ec = bench_result.get("equity_curve", [])
        bench_result["metrics"]["final_value"] = ec[-1]["value"] if ec else 0
        m = bench_result["metrics"]
        ret = m.get("total_return_pct", 0)
        sh = m.get("sharpe", 0)
        print(f"收益 {ret:.1f}%, 夏普 {sh:.2f}")
    else:
        print("无有效结果")

    # 3. Report
    print_comparison_table(results)

    # Save
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest_results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n完整结果已保存至: {out_path}")


if __name__ == "__main__":
    main()
