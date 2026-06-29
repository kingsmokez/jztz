"""V5 实盘表现持续监测脚本

功能：
1. 读取 auction_history.jsonl 中 source=live 的记录
2. 对每只选出股票，根据 K 线数据回填实际收益（含 V5 止损+移动止盈）
3. 累计统计：胜率、平均收益、最大回撤、夏普、盈亏比
4. 按策略分支分组统计
5. 与 V5 回测基准对比（3日胜率88.9%、平均+7.10%）
6. 触发预警（连续亏损、胜率下滑、回撤扩大）
7. 输出监测报告并保存到 auction_monitor_report.json

调度：建议每日收盘后 16:00 运行一次
"""
import json
import os
import sys
import math
from datetime import datetime
from typing import Optional

# 添加项目根目录到 path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from modules.kline_fetcher import KlineFetcher
from modules.logger import log
from scripts.auction_backtest import compute_future_returns

# V5 回测基准（3日持有）
V5_BENCHMARK = {
    "sample": 9,
    "win_rate_3d": 88.9,
    "avg_return_3d": 7.10,
    "cum_return_3d": 63.92,
    "sharpe_3d": 15.65,
    "max_drawdown_3d": 3.17,
    "pl_ratio_3d": 2.65,
}

# 预警阈值
ALERT_THRESHOLDS = {
    "min_win_rate": 70.0,          # 胜率低于 70% 预警
    "max_drawdown": 5.0,            # 最大回撤大于 5% 预警
    "consecutive_losses": 2,        # 连续 2 次亏损预警
    "min_avg_return": 3.0,          # 平均收益低于 3% 预警
    "min_sample_for_alert": 5,      # 样本数 >=5 才触发预警
}

HISTORY_FILE = os.path.join(PROJECT_ROOT, "auction_history.jsonl")
REPORT_FILE = os.path.join(PROJECT_ROOT, "auction_monitor_report.json")


def load_live_picks() -> list:
    """加载实盘选出记录"""
    if not os.path.exists(HISTORY_FILE):
        return []
    records = []
    seen_keys = set()  # 去重：(date, code)
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line.strip())
                if rec.get("source") != "live":
                    continue
                date = rec.get("date", "")
                for s in rec.get("stocks", []):
                    key = (date, s.get("code", ""))
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    s["pick_date"] = date
                    s["market_info"] = {
                        "regime": rec.get("market_info", {}).get("regime", "unknown"),
                        "idx_5d_pct": rec.get("market_info", {}).get("idx_5d_change_pct", 0),
                    }
                    records.append(s)
            except Exception:
                continue
    return records


def evaluate_picks(picks: list) -> list:
    """回填每只股票的实际收益"""
    evaluated = []
    fetcher = KlineFetcher()
    for p in picks:
        code = p.get("code", "")
        date = p.get("pick_date", "")
        if not code or not date:
            continue
        # 检查是否已过持有期（至少 5 个交易日）
        klines = fetcher.get_kline(code, count=80)
        if not klines:
            continue
        pick_idx = None
        for i, k in enumerate(klines):
            if k.get("date", "") == date:
                pick_idx = i
                break
        if pick_idx is None:
            continue
        # 需要至少 5 个未来交易日数据
        if pick_idx + 5 >= len(klines):
            # 持有期不足，跳过
            continue
        rets = compute_future_returns(code, date, [1, 3, 5])
        p["returns"] = rets
        p["evaluable"] = True
        evaluated.append(p)
    return evaluated


def compute_stats(samples: list, key: str = "3d") -> Optional[dict]:
    """计算统计指标"""
    valid = [s for s in samples if s.get("returns", {}).get(key) is not None]
    if not valid:
        return None
    returns = [s["returns"][key] for s in valid]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    n = len(valid)
    avg = sum(returns) / n
    win_rate = len(wins) / n * 100
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    pl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    cum = sum(returns)
    # 夏普比率（简化：假设无风险利率=0，日波动率=std）
    if n >= 2:
        mean = avg
        std = math.sqrt(sum((r - mean) ** 2 for r in returns) / (n - 1))
        sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0
    else:
        sharpe = 0
    # 最大回撤（基于累计收益序列）
    cum_series = []
    running = 0
    for r in returns:
        running += r
        cum_series.append(running)
    peak = -float("inf")
    max_dd = 0
    for v in cum_series:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    return {
        "n": n,
        "win_rate": round(win_rate, 1),
        "avg_return": round(avg, 2),
        "cum_return": round(cum, 2),
        "avg_win": round(avg_win, 2) if wins else 0,
        "avg_loss": round(avg_loss, 2) if losses else 0,
        "pl_ratio": round(pl_ratio, 2) if pl_ratio != float("inf") else None,
        "sharpe": round(sharpe, 2),
        "max_drawdown": round(max_dd, 2),
    }


def check_alerts(stats_3d: Optional[dict], samples: list) -> list:
    """触发预警"""
    alerts = []
    if not stats_3d or stats_3d["n"] < ALERT_THRESHOLDS["min_sample_for_alert"]:
        return alerts

    if stats_3d["win_rate"] < ALERT_THRESHOLDS["min_win_rate"]:
        alerts.append({
            "level": "warning",
            "type": "win_rate_drop",
            "message": f"胜率下滑预警: {stats_3d['win_rate']}% < {ALERT_THRESHOLDS['min_win_rate']}%（基准88.9%）",
        })

    if stats_3d["avg_return"] < ALERT_THRESHOLDS["min_avg_return"]:
        alerts.append({
            "level": "warning",
            "type": "low_return",
            "message": f"平均收益偏低: {stats_3d['avg_return']}% < {ALERT_THRESHOLDS['min_avg_return']}%（基准7.10%）",
        })

    if stats_3d["max_drawdown"] > ALERT_THRESHOLDS["max_drawdown"]:
        alerts.append({
            "level": "danger",
            "type": "drawdown_expand",
            "message": f"最大回撤扩大: {stats_3d['max_drawdown']}% > {ALERT_THRESHOLDS['max_drawdown']}%（基准3.17%）",
        })

    # 连续亏损检查
    consecutive = 0
    for s in samples:
        r = s.get("returns", {}).get("3d")
        if r is None:
            continue
        if r < 0:
            consecutive += 1
        else:
            break
    if consecutive >= ALERT_THRESHOLDS["consecutive_losses"]:
        alerts.append({
            "level": "danger",
            "type": "consecutive_losses",
            "message": f"连续亏损预警: 最近{consecutive}次亏损（阈值{ALERT_THRESHOLDS['consecutive_losses']}）",
        })

    return alerts


def suggest_adjustments(stats_3d: Optional[dict], branch_stats: dict, alerts: list) -> list:
    """基于实盘表现建议参数优化"""
    suggestions = []
    if not stats_3d or stats_3d["n"] < ALERT_THRESHOLDS["min_sample_for_alert"]:
        suggestions.append("样本数不足，继续积累数据")
        return suggestions

    # 建议1：胜率下滑时收紧低开反转条件
    if stats_3d["win_rate"] < V5_BENCHMARK["win_rate_3d"] - 10:
        low_stats = branch_stats.get("low_open_reversal")
        if low_stats and low_stats["win_rate"] < 70:
            suggestions.append(
                "低开反转策略胜率下滑，建议收紧条件：量比≥1.8（原1.5）或前5日趋势≥3%（原2%）"
            )

    # 建议2：低开反转亏损样本多时增加大盘5日趋势门槛
    if stats_3d["win_rate"] < 70:
        suggestions.append(
            "整体胜率偏低，建议低开反转策略要求大盘5日趋势≥+1%（原≥0）"
        )

    # 建议3：平均收益下降时考虑调整持有期
    if stats_3d["avg_return"] < V5_BENCHMARK["avg_return_3d"] * 0.6:
        suggestions.append(
            "平均收益明显下降，建议尝试5日持有（V5回测5日平均+7.90%优于3日）"
        )

    # 建议4：移动止盈参数调整
    if stats_3d["avg_win"] < V5_BENCHMARK["avg_return_3d"]:
        suggestions.append(
            "盈利平均偏小，建议移动止盈激活阈值从8%降至6%（更早锁定利润）"
        )

    # 建议5：盈亏比恶化时调整止损
    if stats_3d.get("pl_ratio") and stats_3d["pl_ratio"] < 1.5:
        suggestions.append(
            "盈亏比偏低，建议收紧止损至-2.5%（原-3%）控制单笔亏损"
        )

    # 建议6：分支表现差异大时调整权重
    high_stats = branch_stats.get("high_open")
    low_stats = branch_stats.get("low_open_reversal")
    if high_stats and low_stats and high_stats["n"] >= 3 and low_stats["n"] >= 3:
        if high_stats["win_rate"] - low_stats["win_rate"] > 20:
            suggestions.append(
                f"高开延续胜率({high_stats['win_rate']}%)显著优于低开反转({low_stats['win_rate']}%)，"
                "建议提高低开反转评分门槛至 82 分（原 80 分）"
            )
        elif low_stats["win_rate"] - high_stats["win_rate"] > 20:
            suggestions.append(
                f"低开反转胜率({low_stats['win_rate']}%)显著优于高开延续({high_stats['win_rate']}%)，"
                "建议提高高开延续评分门槛至 82 分（原 80 分）"
            )

    # 建议7：无预警时维持现状
    if not alerts and not suggestions:
        suggestions.append("实盘表现符合预期，维持当前参数")

    return suggestions


def generate_report() -> dict:
    """生成监测报告"""
    log.info("开始生成 V5 实盘监测报告...")

    picks = load_live_picks()
    log.info(f"加载实盘选出记录: {len(picks)} 条")

    if not picks:
        report = {
            "generated_at": datetime.now().isoformat(),
            "status": "no_data",
            "message": "暂无实盘选出记录",
            "benchmark": V5_BENCHMARK,
        }
        return report

    evaluated = evaluate_picks(picks)
    log.info(f"可评估记录: {len(evaluated)} 条（已过5日持有期）")

    if not evaluated:
        report = {
            "generated_at": datetime.now().isoformat(),
            "status": "pending",
            "message": f"共{len(picks)}只选出股票，均未过5日持有期，待后续评估",
            "total_picks": len(picks),
            "benchmark": V5_BENCHMARK,
        }
        return report

    # 整体统计
    stats_1d = compute_stats(evaluated, "1d")
    stats_3d = compute_stats(evaluated, "3d")
    stats_5d = compute_stats(evaluated, "5d")

    # 按策略分支分组
    high_open = [s for s in evaluated if s.get("strategy_branch") == "high_open"]
    low_open = [s for s in evaluated if s.get("strategy_branch") == "low_open_reversal"]
    branch_stats = {
        "high_open": compute_stats(high_open, "3d"),
        "low_open_reversal": compute_stats(low_open, "3d"),
    }

    # 预警
    alerts = check_alerts(stats_3d, evaluated)

    # 优化建议
    suggestions = suggest_adjustments(stats_3d, branch_stats, alerts)

    # 与基准对比
    benchmark_diff = None
    if stats_3d:
        benchmark_diff = {
            "win_rate_diff": round(stats_3d["win_rate"] - V5_BENCHMARK["win_rate_3d"], 1),
            "avg_return_diff": round(stats_3d["avg_return"] - V5_BENCHMARK["avg_return_3d"], 2),
            "sharpe_diff": round(stats_3d["sharpe"] - V5_BENCHMARK["sharpe_3d"], 2),
            "max_drawdown_diff": round(stats_3d["max_drawdown"] - V5_BENCHMARK["max_drawdown_3d"], 2),
        }

    report = {
        "generated_at": datetime.now().isoformat(),
        "status": "ok",
        "total_picks": len(picks),
        "evaluated_picks": len(evaluated),
        "pending_picks": len(picks) - len(evaluated),
        "overall_stats": {
            "1d": stats_1d,
            "3d": stats_3d,
            "5d": stats_5d,
        },
        "branch_stats": branch_stats,
        "benchmark": V5_BENCHMARK,
        "benchmark_diff": benchmark_diff,
        "alerts": alerts,
        "suggestions": suggestions,
        "details": [
            {
                "date": s.get("pick_date"),
                "code": s.get("code"),
                "name": s.get("name"),
                "strategy_branch": s.get("strategy_branch"),
                "gap_pct": round(s.get("gap_pct", 0) * 100, 2),
                "score": round(s.get("score", 0), 1),
                "returns": s.get("returns"),
                "regime": s.get("market_info", {}).get("regime"),
            }
            for s in evaluated
        ],
    }

    # 保存报告
    try:
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        log.info(f"监测报告已保存: {REPORT_FILE}")
    except Exception as e:
        log.warning(f"保存监测报告失败: {e}")

    return report


def print_report(report: dict):
    """打印监测报告摘要"""
    print("\n" + "=" * 60)
    print("V5 实盘监测报告")
    print("=" * 60)
    print(f"生成时间: {report.get('generated_at')}")

    if report.get("status") != "ok":
        print(f"状态: {report.get('status')} - {report.get('message', '')}")
        return

    print(f"\n实盘选出: {report.get('total_picks')}只 | 已评估: {report.get('evaluated_picks')}只 | 待评估: {report.get('pending_picks')}只")

    stats_3d = report.get("overall_stats", {}).get("3d")
    if stats_3d:
        print(f"\n3日持有整体表现:")
        print(f"  样本数: {stats_3d['n']}")
        print(f"  胜率: {stats_3d['win_rate']}% (基准 {V5_BENCHMARK['win_rate_3d']}%)")
        print(f"  平均收益: {stats_3d['avg_return']}% (基准 {V5_BENCHMARK['avg_return_3d']}%)")
        print(f"  累计收益: {stats_3d['cum_return']}%")
        print(f"  夏普比率: {stats_3d['sharpe']} (基准 {V5_BENCHMARK['sharpe_3d']})")
        print(f"  最大回撤: {stats_3d['max_drawdown']}% (基准 {V5_BENCHMARK['max_drawdown_3d']}%)")
        print(f"  盈亏比: {stats_3d.get('pl_ratio', '-')}")

    print("\n按策略分支分组（3日持有）:")
    for branch, st in report.get("branch_stats", {}).items():
        if st:
            print(f"  {branch}: 样本{st['n']} 胜率{st['win_rate']}% 平均{st['avg_return']}%")

    alerts = report.get("alerts", [])
    if alerts:
        print(f"\n⚠️  预警 ({len(alerts)} 条):")
        for a in alerts:
            print(f"  [{a['level']}] {a['message']}")
    else:
        print("\n✅ 无预警")

    suggestions = report.get("suggestions", [])
    if suggestions:
        print(f"\n💡 优化建议 ({len(suggestions)} 条):")
        for s in suggestions:
            print(f"  - {s}")


if __name__ == "__main__":
    report = generate_report()
    print_report(report)
