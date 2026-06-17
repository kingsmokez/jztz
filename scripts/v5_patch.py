# V5.2 patch script
import re

path = r"D:\UI\jztz_v17\modules\scoring.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Change 1: V5 weights + momentum penalty
old1 = "    total = v * 0.36 + q * 0.11 + g * 0.08 + m * 0.12 + s * 0.33"
new1 = """    # V5.2: lower sentiment(33->18%), raise quality(11->18%), growth(8->17%)
    total = v * 0.35 + q * 0.18 + g * 0.17 + m * 0.12 + s * 0.18
    # Momentum penalty: avoid value traps
    _m20 = 0
    if tech_data:
        _m20 = tech_data.get("momentum_20", 0)
    if _m20 < -10:
        total -= 5
    elif _m20 < -5:
        total -= 2"""
assert old1 in content, "old1 not found"
content = content.replace(old1, new1)
print("Change 1 applied")

# Change 2: Trend gate
old3 = """    if ma5 and ma20 and ma5 > 0 and ma20 > 0:
        if ma5 > ma20:    tech_adj += 0.01
        else:              tech_adj -= 0.02"""
new3 = """    if ma5 and ma20 and ma5 > 0 and ma20 > 0:
        if ma5 > ma20:    tech_adj += 0.01
        else:              tech_adj -= 0.02
    # V5.2 trend gate: death cross = deeper 5pct discount
    if ma5 and ma20 and ma60 and ma5 > 0 and ma20 > 0 and ma60 > 0:
        if ma5 < ma20 < ma60:
            tech_adj -= 0.05
        elif ma5 > ma20 > ma60:
            tech_adj += 0.01"""
assert old3 in content, "old3 not found"
content = content.replace(old3, new3)
print("Change 2 applied")

# Change 3: Lower upside caps
pattern = r"    if v5_score >= 75:\s+upside = min\(upside, 0\.45\)\n    elif v5_score >= 60:\s+upside = min\(upside, 0\.35\)\n    else:\s+upside = min\(upside, 0\.25\)"
replacement = """    # V5.2: realistic upside caps by star rating
    if star_rating >= 5:   upside = min(upside, 0.30)
    elif star_rating >= 4: upside = min(upside, 0.25)
    elif star_rating >= 3: upside = min(upside, 0.20)
    elif star_rating >= 2: upside = min(upside, 0.15)
    else:                  upside = min(upside, 0.12)"""
match = re.search(pattern, content)
assert match, "upside cap pattern not found"
content = content[:match.start()] + replacement + content[match.end():]
print("Change 3 applied")

# Change 4: Wider stop loss floor
old5 = "    stop_floor = round(buy_point * 0.92, 2)"
new5 = "    stop_floor = round(buy_point * 0.90, 2)  # V5.2: 10pct floor (was 8pct)"
assert old5 in content, "old5 not found"
content = content.replace(old5, new5)
print("Change 4 applied")

# Change 5: First sell target + position trend adjustment
old6 = "    risk_reward_ratio = 0.0"
new6 = """    # V5.2: first sell target (60pct of upside)
    sell_first = round(price * (1 + upside * 0.6), 2)
    if sell_first <= buy_point * 1.05:
        sell_first = round(buy_point * 1.08, 2)

    # V5.2: position trend adjustment
    if ma5 and ma20 and ma60 and ma5 > 0 and ma20 > 0 and ma60 > 0:
        if ma5 < ma20 < ma60:
            position_pct = max(5, int(position_pct * 0.7))
        elif ma5 > ma20 > ma60:
            position_pct = min(25, int(position_pct * 1.1))

    risk_reward_ratio = 0.0"""
assert old6 in content, "old6 not found"
content = content.replace(old6, new6)
print("Change 5 applied")

# Change 6: Add sell_first to return dict
old7 = chr(34) + "sell" + chr(34) + ": sell_point,"
new7 = chr(34) + "sell" + chr(34) + ": sell_point," + chr(10) + "        " + chr(34) + "sell_first" + chr(34) + ": sell_first,"
assert old7 in content, "old7 not found: " + repr(old7)
content = content.replace(old7, new7, 1)
print("Change 6 applied")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("All 6 V5.2 changes applied successfully!")
