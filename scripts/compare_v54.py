import sys, json
sys.path.insert(0, '.')

from modules.data_fetcher import get_realtime_quotes, get_financial_data, get_preset_financials, preload_industry_cache
from modules.technical import calculate_technical_indicators
from modules.scoring import evaluate_stock, multi_factor_evaluate
from modules.market_env import get_market_env
from concurrent.futures import ThreadPoolExecutor, as_completed

env = get_market_env()
print(f'Market env: trend={env.trend}, multiplier={env.multiplier:.2f}, can_pick={env.can_pick()}')

quotes = get_realtime_quotes()
if not quotes:
    print('No quotes')
    sys.exit()

candidates = {}
for code, q in quotes.items():
    name = q.name or ''
    if 'ST' in name or '*' in name or '退' in name:
        continue
    if code.startswith('9') or code.startswith('8') or code.startswith('4'):
        continue
    if q.price <= 1:
        continue
    candidates[code] = q

print(f'Candidates: {len(candidates)}')

codes = list(candidates.keys())
preload_industry_cache(codes[:500])
financials = get_financial_data(codes[:500])
preset_financials = get_preset_financials()
print(f'Financials: {len(financials)}/{len(codes[:500])}')

sorted_candidates = sorted(candidates.values(), key=lambda q: q.market_cap, reverse=True)
test_codes = [q.code for q in sorted_candidates[:200]]

tech_cache = {}
def calc_tech(code):
    try:
        tech = calculate_technical_indicators(code, days=30)
        return (code, tech)
    except:
        return (code, None)

with ThreadPoolExecutor(max_workers=10) as executor:
    futures = [executor.submit(calc_tech, c) for c in test_codes]
    for future in as_completed(futures):
        try:
            code, tech = future.result(timeout=15)
            if tech:
                tech_cache[code] = tech
        except:
            pass

print(f'Tech data: {len(tech_cache)}/{len(test_codes)}')

v54_scores = []
v50_scores = []

for code in test_codes:
    q = candidates.get(code)
    if not q:
        continue
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
        'code': code, 'name': q.name, 'price': q.price,
        'change_pct': q.change_pct, 'pe': q.pe, 'pb': pb,
        'market_cap': q.market_cap, 'turnover_rate': q.turnover,
        'amount': q.amount, 'roe': roe, 'gross_margin': gross_margin,
        'net_margin': net_margin, 'rev_growth': rev_growth,
        'profit_growth': profit_growth, 'debt_ratio': debt_ratio,
    }
    
    tech_data = tech_cache.get(code)
    v5_result = multi_factor_evaluate(stock_dict, tech_data)
    v54_scores.append((code, q.name, v5_result['v5_total'], v5_result['v5_factors'], v5_result['v5_reasons']))
    
    factors = v5_result['v5_factors']
    v50_total = factors['value'] * 0.36 + factors['quality'] * 0.11 + factors['growth'] * 0.08 + factors['momentum'] * 0.12 + factors['sentiment'] * 0.33
    v50_scores.append((code, q.name, v50_total, factors))

v54_sorted = sorted(v54_scores, key=lambda x: x[2], reverse=True)
v50_sorted = sorted(v50_scores, key=lambda x: x[2], reverse=True)

print()
print('=== V5.4 (New) vs V5.0 (Original) Top 20 ===')
print()

v54_top20_codes = set(c for c, _, _, _, _ in v54_sorted[:20])
v50_top20_codes = set(c for c, _, _, _ in v50_sorted[:20])
overlap = v54_top20_codes & v50_top20_codes

print(f'Top 20 overlap: {len(overlap)}/20 ({len(overlap)/20*100:.0f}%)')
print()

print('--- V5.4 Top 20 ---')
for code, name, score, factors, reasons in v54_sorted[:20]:
    in_v50 = 'Y' if code in v50_top20_codes else ' '
    v = factors['value']
    q = factors['quality']
    g = factors['growth']
    m = factors['momentum']
    s = factors['sentiment']
    r = ' | '.join(reasons[:3])
    print(f'  [{in_v50}] {name:8s} ({code}) score={score:.1f} V={v:.0f} Q={q:.0f} G={g:.0f} M={m:.0f} S={s:.0f} | {r}')

print()
print('--- V5.0 Top 20 ---')
for code, name, score, factors in v50_sorted[:20]:
    in_v54 = 'Y' if code in v54_top20_codes else ' '
    v = factors['value']
    q = factors['quality']
    g = factors['growth']
    m = factors['momentum']
    s = factors['sentiment']
    print(f'  [{in_v54}] {name:8s} ({code}) score={score:.1f} V={v:.0f} Q={q:.0f} G={g:.0f} M={m:.0f} S={s:.0f}')

v54_only = [(c, n, s, f, r) for c, n, s, f, r in v54_sorted[:20] if c not in v50_top20_codes]
v50_only = [(c, n, s, f) for c, n, s, f in v50_sorted[:20] if c not in v54_top20_codes]

print()
print(f'V5.4 unique picks ({len(v54_only)}):')
for code, name, score, factors, reasons in v54_only:
    v = factors['value']
    r = ' | '.join(reasons[:3])
    print(f'  + {name} ({code}) score={score:.1f} V={v:.0f} | {r}')

print(f'V5.0 unique picks ({len(v50_only)}):')
for code, name, score, factors in v50_only:
    v = factors['value']
    q = factors['quality']
    g = factors['growth']
    print(f'  - {name} ({code}) score={score:.1f} V={v:.0f} Q={q:.0f} G={g:.0f}')

# Statistical comparison
print()
print('=== Statistical Comparison ===')
v54_avg = sum(s for _, _, s, _, _ in v54_sorted[:20]) / 20
v50_avg = sum(s for _, _, s, _ in v50_sorted[:20]) / 20
v54_avg_v = sum(f['value'] for _, _, _, f, _ in v54_sorted[:20]) / 20
v54_avg_q = sum(f['quality'] for _, _, _, f, _ in v54_sorted[:20]) / 20
v54_avg_g = sum(f['growth'] for _, _, _, f, _ in v54_sorted[:20]) / 20
v50_avg_q = sum(f['quality'] for _, _, _, f in v50_sorted[:20]) / 20
v50_avg_g = sum(f['growth'] for _, _, _, f in v50_sorted[:20]) / 20
v50_avg_v = sum(f['value'] for _, _, _, f in v50_sorted[:20]) / 20

print(f'Avg total score:  V5.4={v54_avg:.1f} V5.0={v50_avg:.1f}')
print(f'Avg value:        V5.4={v54_avg_v:.1f} V5.0={v50_avg_v:.1f}')
print(f'Avg quality:      V5.4={v54_avg_q:.1f} V5.0={v50_avg_q:.1f}')
print(f'Avg growth:       V5.4={v54_avg_g:.1f} V5.0={v50_avg_g:.1f}')

# Check if V<25 stocks are eliminated from V5.4 top 20
low_v_in_v54 = sum(1 for _, _, _, f, _ in v54_sorted[:20] if f['value'] < 25)
low_v_in_v50 = sum(1 for _, _, _, f in v50_sorted[:20] if f['value'] < 25)
print(f'Stocks with V<25 in top 20: V5.4={low_v_in_v54} V5.0={low_v_in_v50}')
low_q_in_v54 = sum(1 for _, _, _, f, _ in v54_sorted[:20] if f['quality'] <= 12)
low_q_in_v50 = sum(1 for _, _, _, f in v50_sorted[:20] if f['quality'] <= 12)
print(f'Stocks with Q<=12 in top 20: V5.4={low_q_in_v54} V5.0={low_q_in_v50}')
