import re

path = r"D:\UI\jztz_v17\scripts\backtest_v4.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Add 60d and 90d hold periods
old1 = "trade_30 = simulate_trade(recent, buy, sell, stop, hold_days=30)"
content = content.replace(old1, "trade_30 = simulate_trade(recent, buy, sell, stop, hold_days=30)\n        trade_60 = simulate_trade(recent, buy, sell, stop, hold_days=60)\n        trade_90 = simulate_trade(recent, buy, sell, stop, hold_days=90)")

# Add sell_first extraction
old2 = "        stop = bs[chr(34) + chr(115) + chr(116) + chr(111) + chr(112) + chr(95) + chr(108) + chr(111) + chr(115) + chr(115) + chr(34)]"
print("Searching for stop_loss line...")
idx = content.find("stop_loss")
print(f"Found stop_loss at index {idx}")

# Use regex to find and add sell_first
content = re.sub(r"(        stop = bs\[\"stop_loss\"\])", r"        sell_first = bs.get(\"sell_first\", sell)  # V5.2 first target\n\1", content)

# Add 60d/90d to result dict
content = content.replace("\"trade_10d\": trade_10,", "\"trade_10d\": trade_10,\n            \"trade_60d\": trade_60,\n            \"trade_90d\": trade_90,")

# Add 60d/90d to comparison section
content = content.replace("(\"10d\", \"trade_10d\"), (\"20d\", \"trade_20d\"), (\"30d\", \"trade_30d\")", "(\"10d\", \"trade_10d\"), (\"20d\", \"trade_20d\"), (\"30d\", \"trade_30d\"), (\"60d\", \"trade_60d\"), (\"90d\", \"trade_90d\")")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Backtest script updated successfully")
