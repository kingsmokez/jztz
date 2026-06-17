import re

path = r"D:\UI\jztz_v17\modules\scoring.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

func_code = '''

def industry_concentration_limit(
    results: list[dict],
    max_per_industry: int = 2,
    min_count: int = 5,
) -> list[dict]:
    """Limit the number of stocks from the same industry in the results."""
    if not results:
        return results
    industry_counts = {}
    filtered = []
    for stock in results:
        industry = stock.get("industry", "unknown")
        count = industry_counts.get(industry, 0)
        if count < max_per_industry:
            filtered.append(stock)
            industry_counts[industry] = count + 1
        elif len(filtered) < min_count:
            filtered.append(stock)
            industry_counts[industry] = count + 1
    return filtered

'''

marker = "def calculate_buy_sell(stock, v5_score, tech_data=None):"
idx = content.find(marker)
if idx > 0:
    content = content[:idx] + func_code + content[idx:]
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Added industry_concentration_limit function")
else:
    print("Marker not found!")
