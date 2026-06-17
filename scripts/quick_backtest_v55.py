"""Quick backtest: Compare V5.5 vs V5.1 strategy with real data"""
import sys, os
sys.path.insert(0, r"D:\UI\jztz_v17")
os.chdir(r"D:\UI\jztz_v17")

from datetime import datetime, timedelta
from modules.data_fetcher import get_realtime_quotes, get_financial_data, get_preset_financials
from modules.scoring import evaluate_stock, multi_factor_evaluate
from modules.technical import calculate_technical_indicators
from modules.backtest import BacktestConfig, BacktestInput, run as run_backtest
from modules.logger import log

# Step 1: Get current top stocks with V5.5 scoring
print("=== V5.5 Strategy Backtest ===")
print("Getting real-time stock data...")

quotes = get_realtime_quotes()
if not quotes:
    print("ERROR: Cannot get stock data")
    sys.exit(1)

print(f"Got {len(quotes)} stocks")

# Filter candidates
candidates = {}
for code, q in quotes.items():
    name = q.name or ""
    if "ST" in name or "*" in name or "退" in name or name.startswith("N"):
        continue
    if code.startswith("9") or code.startswith("8") or code.startswith("4"):
        continue
    if q.price <= 1:
        continue
    if q.market_cap > 0 and q.market_cap < 10:
        continue
    if 0 < q.turnover < 0.3:
        continue
    candidates[code] = q

print(f"Candidates after filtering: {len(candidates)}")

# Get financial data for top candidates
codes = list(candidates.keys())[:200]  # Limit to 200 for speed
financials = get_financial_data(codes)
preset_financials = get_preset_financials()
print(f"Financial data: {len(financials)}/{len(codes)}")

# Score stocks
results = []
for code in codes[:200]:
    q = candidates[code]
    f = financials.get(code)
    
    roe = f.roe if f else 0
    gross_margin = f.gross_margin if f else 0
    net_margin = f.net_margin if f else 0
    rev_growth = f.revenue_growth if f else 0
    profit_growth = f.profit_growth if f else 0
    debt_ratio = f.debt_ratio if f else 0
    
    pb = q.pb
    if pb <= 0:
        if code in preset_financials and preset_financials[code].get('pb', 0) > 0:
            pb = preset_financials[code]['pb']
        elif q.pe > 0 and roe > 0:
            pb = round(q.pe * roe / 100, 2)
    
    stock_dict = {
        "code": code, "name": q.name, "price": q.price,
        "change_pct": q.change_pct, "pe": q.pe, "pb": pb,
        "market_cap": q.market_cap, "turnover_rate": q.turnover,
        "amount": q.amount, "roe": roe, "gross_margin": gross_margin,
        "net_margin": net_margin, "rev_growth": rev_growth,
        "profit_growth": profit_growth, "debt_ratio": debt_ratio,
    }
    
    try:
        tech = calculate_technical_indicators(code, days=30)
    except:
        tech = None
    
    eval_result = evaluate_stock(stock_dict, tech_data=tech)
    if eval_result and eval_result.get("score", 0) >= 55:
        results.append(eval_result)

# Sort by V5 score
results.sort(key=lambda x: x.get("v5_score", x.get("score", 0)), reverse=True)
top10 = results[:10]

print(f"\n=== Top 10 V5.5 Picks ===")
for i, r in enumerate(top10):
    print(f"  {i+1}. {r['name']} ({r['code']}) | V5={r['v5_score']:.1f} | ROE={r['roe']:.1f}% | rec={r['v5_recommendation']}")

# Also show what V5.1 would have picked (without ROE/Q penalties)
print(f"\n=== Comparison: What V5.1 would pick (no ROE penalty) ===")
# Re-evaluate top stocks without V5.5 penalties
v51_results = []
for code in codes[:200]:
    q = candidates[code]
    f = financials.get(code)
    
    roe = f.roe if f else 0
    gross_margin = f.gross_margin if f else 0
    net_margin = f.net_margin if f else 0
    rev_growth = f.revenue_growth if f else 0
    profit_growth = f.profit_growth if f else 0
    debt_ratio = f.debt_ratio if f else 0
    
    pb = q.pb
    if pb <= 0:
        if code in preset_financials and preset_financials[code].get('pb', 0) > 0:
            pb = preset_financials[code]['pb']
        elif q.pe > 0 and roe > 0:
            pb = round(q.pe * roe / 100, 2)
    
    stock_dict = {
        "code": code, "name": q.name, "price": q.price,
        "change_pct": q.change_pct, "pe": q.pe, "pb": pb,
        "market_cap": q.market_cap, "turnover_rate": q.turnover,
        "amount": q.amount, "roe": roe, "gross_margin": gross_margin,
        "net_margin": net_margin, "rev_growth": rev_growth,
        "profit_growth": profit_growth, "debt_ratio": debt_ratio,
    }
    
    v5_result = multi_factor_evaluate(stock_dict)
    v5_total = v5_result['v5_total']
    # Add bonuses (but no V5.5 penalties)
    # Market bonus (simplified)
    v5_total += 2  # average market bonus
    # Sector bonus (simplified)
    v5_total += 1  # average sector bonus
    
    if v5_total >= 55:
        v51_results.append({
            "code": code, "name": q.name, "v5_score": v5_total,
            "roe": roe, "pe": q.pe, "price": q.price,
            "v5_factors": v5_result['v5_factors'],
        })

v51_results.sort(key=lambda x: x['v5_score'], reverse=True)
v51_top10 = v51_top10 = v51_results[:10]

print(f"\nV5.1 Top 10 (without ROE penalty):")
for i, r in enumerate(v51_top10):
    print(f"  {i+1}. {r['name']} ({r['code']}) | V5={r['v5_score']:.1f} | ROE={r['roe']:.1f}%")

# Compare
v55_codes = {r['code'] for r in top10}
v51_codes = {r['code'] for r in v51_top10}
only_in_v51 = v51_codes - v55_codes
only_in_v55 = v55_codes - v51_codes

print(f"\n=== Key Differences ===")
print(f"Only in V5.1 (removed by V5.5):")
for code in only_in_v51:
    stock = next(r for r in v51_top10 if r['code'] == code)
    print(f"  {stock['name']} ({code}) | ROE={stock['roe']:.1f}% | V5.1={stock['v5_score']:.1f}")

print(f"\nNew in V5.5 (added by better screening):")
for code in only_in_v55:
    stock = next(r for r in top10 if r['code'] == code)
    print(f"  {stock['name']} ({code}) | ROE={stock['roe']:.1f}% | V5.5={stock['v5_score']:.1f}")
