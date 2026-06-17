"""分析新旧策略选股差异，找出各自独有的优质选股"""

import json
import os
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
detail_path = os.path.join(PROJECT_ROOT, "data", "wp2_backtest_result_detail.json")

with open(detail_path, "r", encoding="utf-8") as f:
    data = json.load(f)

old_detail = data["old_strategy"]["detail"]
new_detail = data["new_strategy"]["detail"]

# 建立索引: (date, code) -> item
old_map = {(d["date"], d["code"]): d for d in old_detail}
new_map = {(d["date"], d["code"]): d for d in new_detail}

# 找出三个集合
both_keys = set(old_map.keys()) & set(new_map.keys())
old_only_keys = set(old_map.keys()) - set(new_map.keys())
new_only_keys = set(new_map.keys()) - set(old_map.keys())

print("=" * 80)
print("新旧策略选股差异分析")
print("=" * 80)
print(f"旧策略选股: {len(old_detail)} 只")
print(f"新策略选股: {len(new_detail)} 只")
print(f"共同选股: {len(both_keys)} 只")
print(f"旧策略独有: {len(old_only_keys)} 只")
print(f"新策略独有: {len(new_only_keys)} 只")

def analyze_set(keys, label, source_map):
    if not keys:
        print(f"\n{label}: 无数据")
        return
    returns = [source_map[k]["next_return"] for k in keys if k in source_map]
    wins = sum(1 for r in returns if r > 0)
    losses = sum(1 for r in returns if r <= 0)
    avg_ret = sum(returns) / len(returns) if returns else 0
    win_rate = wins / len(returns) * 100 if returns else 0

    print(f"\n{label} ({len(keys)} 只):")
    print(f"  胜率: {win_rate:.1f}% ({wins}胜/{losses}负)")
    print(f"  平均收益: {avg_ret:.2f}%")

    # 列出收益最高的5只
    sorted_items = sorted([(k, source_map[k]) for k in keys if k in source_map],
                          key=lambda x: x[1]["next_return"], reverse=True)
    print(f"  收益TOP5:")
    for k, v in sorted_items[:5]:
        print(f"    {v['date']} {v['name']}({v['code']}) 收益:{v['next_return']}% 买入价:{v['price']} 涨幅:{v.get('pct',0)}% 评分:{v.get('score',0)}")
    print(f"  亏损TOP5:")
    for k, v in sorted_items[-5:]:
        print(f"    {v['date']} {v['name']}({v['code']}) 收益:{v['next_return']}% 买入价:{v['price']} 涨幅:{v.get('pct',0)}% 评分:{v.get('score',0)}")

analyze_set(both_keys, "共同选股", old_map)
analyze_set(old_only_keys, "旧策略独有", old_map)
analyze_set(new_only_keys, "新策略独有", new_map)

# 分析新策略独有选股的因子特征
print("\n" + "=" * 80)
print("新策略独有选股的因子分析")
print("=" * 80)
if new_only_keys:
    new_only_items = [new_map[k] for k in new_only_keys if k in new_map]
    # 按收益分组
    winners = [i for i in new_only_items if i["next_return"] > 0]
    losers = [i for i in new_only_items if i["next_return"] <= 0]

    print(f"\n盈利组 ({len(winners)} 只):")
    if winners:
        avg_pct = sum(i.get("pct", 0) for i in winners) / len(winners)
        avg_score = sum(i.get("score", 0) for i in winners) / len(winners)
        print(f"  平均当日涨幅: {avg_pct:.2f}%")
        print(f"  平均评分: {avg_score:.1f}")
        # 收盘位置分析
        for i in winners[:10]:
            print(f"    {i['date']} {i['name']}({i['code']}) 当日涨:{i.get('pct',0)}% 次日:{i['next_return']}% 评分:{i.get('score',0)} bonus:{i.get('bonus',0)}")

    print(f"\n亏损组 ({len(losers)} 只):")
    if losers:
        avg_pct = sum(i.get("pct", 0) for i in losers) / len(losers)
        avg_score = sum(i.get("score", 0) for i in losers) / len(losers)
        print(f"  平均当日涨幅: {avg_pct:.2f}%")
        print(f"  平均评分: {avg_score:.1f}")
        for i in losers[:10]:
            print(f"    {i['date']} {i['name']}({i['code']}) 当日涨:{i.get('pct',0)}% 次日:{i['next_return']}% 评分:{i.get('score',0)} bonus:{i.get('bonus',0)}")

# 按日对比
print("\n" + "=" * 80)
print("按日对比: 新旧策略当日平均收益")
print("=" * 80)
daily_old = defaultdict(list)
daily_new = defaultdict(list)
for d in old_detail:
    daily_old[d["date"]].append(d["next_return"])
for d in new_detail:
    daily_new[d["date"]].append(d["next_return"])

all_dates = sorted(set(daily_old.keys()) | set(daily_new.keys()))
print(f"{'日期':>12} {'旧策略均收益':>12} {'新策略均收益':>12} {'差异':>8}")
print("-" * 50)
for date in all_dates:
    old_avg = sum(daily_old.get(date, [])) / max(len(daily_old.get(date, [])), 1)
    new_avg = sum(daily_new.get(date, [])) / max(len(daily_new.get(date, [])), 1)
    diff = new_avg - old_avg
    marker = " ***" if abs(diff) > 1.0 else ""
    print(f"{date:>12} {old_avg:>11.2f}% {new_avg:>11.2f}% {diff:>+7.2f}%{marker}")

# 总结
print("\n" + "=" * 80)
print("总结")
print("=" * 80)

# 共同选股的胜率
both_returns = [old_map[k]["next_return"] for k in both_keys if k in old_map]
both_wins = sum(1 for r in both_returns if r > 0)
both_wr = both_wins / len(both_returns) * 100 if both_returns else 0

old_only_returns = [old_map[k]["next_return"] for k in old_only_keys if k in old_map]
old_only_wins = sum(1 for r in old_only_returns if r > 0)
old_only_wr = old_only_wins / len(old_only_returns) * 100 if old_only_returns else 0

new_only_returns = [new_map[k]["next_return"] for k in new_only_keys if k in new_map]
new_only_wins = sum(1 for r in new_only_returns if r > 0)
new_only_wr = new_only_wins / len(new_only_returns) * 100 if new_only_returns else 0

print(f"共同选股胜率: {both_wr:.1f}% (平均收益: {sum(both_returns)/len(both_returns):.2f}%)" if both_returns else "共同选股: 无")
print(f"旧策略独有胜率: {old_only_wr:.1f}% (平均收益: {sum(old_only_returns)/len(old_only_returns):.2f}%)" if old_only_returns else "旧策略独有: 无")
print(f"新策略独有胜率: {new_only_wr:.1f}% (平均收益: {sum(new_only_returns)/len(new_only_returns):.2f}%)" if new_only_returns else "新策略独有: 无")

if new_only_returns and old_only_returns:
    if new_only_wr > old_only_wr:
        print(f"\n>>> 新策略独有选股胜率更高 (+{new_only_wr - old_only_wr:.1f}%)")
    else:
        print(f"\n>>> 旧策略独有选股胜率更高 (+{old_only_wr - new_only_wr:.1f}%)")
