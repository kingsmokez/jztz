"""Fix WP2 picker double-counting of RSI and volume_ratio.

The combined_score = tech_score * 0.6 + base_score * 0.4
tech_score already includes: volume (up to 15 pts), RSI (up to 15 pts), MACD (10 pts), 
MA alignment (20 pts), breakout (10 pts), body ratio (5 pts)

base_score (_wp2_calc_score) previously had: vr 30% + ch 25% + rsi 25% + cap 20%
This means RSI and volume were double-counted!

Fix: Replace vr and rsi in _wp2_calc_score with fundamentals NOT in tech_score:
- 涨跌幅 (ch): keep — tech_score only uses momentum_20/60, not daily change
- 市值 (cap): keep — not in tech_score  
- 新增: 价格合理度 (price level) — not in tech_score
- 新增: 成交额 (amount) — only volume ratio in tech_score, not absolute amount
"""
import os

path = os.path.join(r'D:\UI\jztz_v17\modules', 'wp2_picker.py')
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace _wp2_calc_score entirely
old_fn = '''def _wp2_calc_score(stock: dict) -> float:
    """WP2 自定义评分 (0-100)

    Score components:
    - vr (量比): weight 30%
    - ch (涨跌幅): weight 25%
    - rsi (RSI): weight 25%
    - cap (市值): weight 20%
    """
    sc = 0
    vr = stock.get("vr", 0)
    ch = stock.get("ch", 0)
    rsi = stock.get("rsi", 0)
    cap = stock.get("cap", 0) / 1e8

    # 量比评分 (0-30)
    if vr >= 2.5:
        sc += 30
    elif vr >= 2:
        sc += 25
    elif vr >= 1.5:
        sc += 18
    else:
        sc += 10

    # 涨跌幅评分 (0-25)
    if 3 <= ch <= 6:
        sc += 25
    elif ch >= 2:
        sc += 20
    elif ch >= 1:
        sc += 15
    else:
        sc += 5

    # RSI 评分 (0-25)
    if 55 <= rsi <= 65:
        sc += 25
    elif 50 <= rsi <= 70:
        sc += 20
    else:
        sc += 10

    # 市值评分 (0-20)
    if 50 <= cap <= 200:
        sc += 20
    elif 30 <= cap <= 300:
        sc += 15
    else:
        sc += 8

    return min(sc, 100)'''

new_fn = '''def _wp2_calc_score(stock: dict) -> float:
    """WP2 基础评分 (0-100) — 与 tech_score 不重叠的维度

    tech_score 已包含: 量能(量比)、RSI、MACD、MA对齐、突破、实体比
    base_score 应只包含 tech_score 没有的维度:
    - ch (涨跌幅): 25% — 日涨幅，tech_score 用 momentum_20/60
    - cap (市值): 25% — 市值偏好，tech_score 不涉及
    - amt (成交额): 25% — 绝对成交额，tech_score 只有量比
    - to (换手率): 25% — 活跃度，tech_score 不涉及
    """
    sc = 0
    ch = stock.get("ch", 0)
    cap = stock.get("cap", 0) / 1e8
    amt = stock.get("amt", 0) / 1e8  # 成交额(亿)
    to = stock.get("to", 0)          # 换手率

    # 涨跌幅评分 (0-25): 日涨幅 2-6% 最佳区间
    if 2 <= ch <= 6:
        sc += 25
    elif 1 <= ch < 2:
        sc += 20
    elif ch >= 1 or -2 <= ch < 0:
        sc += 15  # 微涨或小回调
    elif ch > 6:
        sc += 10  # 涨幅过大，谨慎
    else:
        sc += 5

    # 市值评分 (0-25): 30-500亿最佳区间（中盘股弹性好）
    if 30 <= cap <= 200:
        sc += 25
    elif 200 < cap <= 500:
        sc += 20
    elif 20 <= cap < 30 or 500 < cap <= 1000:
        sc += 15
    else:
        sc += 8

    # 成交额评分 (0-25): 日成交额 3-20亿 最佳（有足够流动性）
    if 3 <= amt <= 20:
        sc += 25
    elif 1 <= amt < 3:
        sc += 18  # 流动性偏低
    elif 20 < amt <= 50:
        sc += 15  # 流动性充足
    elif amt > 50:
        sc += 10  # 超大额，可能过热
    else:
        sc += 5

    # 换手率评分 (0-25): 1-5% 最佳活跃区间
    if 1 <= to <= 5:
        sc += 25
    elif 5 < to <= 10:
        sc += 20  # 高度活跃
    elif 0.5 <= to < 1:
        sc += 15  # 低活跃
    elif to > 10:
        sc += 8   # 过热
    else:
        sc += 5

    return min(sc, 100)'''

content = content.replace(old_fn, new_fn, 1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print('wp2_picker.py: fixed double-counting in _wp2_calc_score')
