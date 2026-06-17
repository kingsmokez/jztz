import sys, json
sys.path.insert(0, '.')

from modules.data_fetcher import get_realtime_quotes, get_financial_data, get_preset_financials, preload_industry_cache
from modules.technical import calculate_technical_indicators
from modules.scoring import evaluate_stock, multi_factor_evaluate
from concurrent.futures import ThreadPoolExecutor, as_completed

print('Getting market data...')
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

v53_scores = []
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
    v53_scores.append((code, q.name, v5_result['v5_total'], v5_result['v5_factors']))
    
    factors = v5_result['v5_factors']
    v50_total = factors['value'] * 0.36 + factors['quality'] * 0.11 + factors['growth'] * 0.08 + factors['momentum'] * 0.12 + factors['sentiment'] * 0.33
    v50_scores.append((code, q.name, v50_total, factors))

v53_sorted = sorted(v53_scores, key=lambda x: x[2], reverse=True)
v50_sorted = sorted(v50_scores, key=lambda x: x[2], reverse=True)

print()
print('=== V5.3 (Current) vs V5.0 (Original) Top 20 ===')
print()

v53_top20_codes = set(c for c, _, _, _ in v53_sorted[:20])
v50_top20_codes = set(c for c, _, _, _ in v50_sorted[:20])
overlap = v53_top20_codes & v50_top20_codes

print(f'Top 20 overlap: {len(overlap)}/20 ({len(overlap)/20*100:.0f}%)')
print()

print('--- V5.3 Top 20 (Value35/Quality18/Growth17/Momentum12/Sentiment18) ---')
for code, name, score, factors in v53_sorted[:20]:
    in_v50 = 'Y' if code in v50_top20_codes else ' '
    v = factors['value']
    q = factors['quality']
    g = factors['growth']
    m = factors['momentum']
    s = factors['sentiment']
    print(f'  [{in_v50}] {name:8s} ({code}) score={score:.1f} V={v:.0f} Q={q:.0f} G={g:.0f} M={m:.0f} S={s:.0f}')

print()
print('--- V5.0 Top 20 (Value36/Quality11/Growth8/Momentum12/Sentiment33) ---')
for code, name, score, factors in v50_sorted[:20]:
    in_v53 = 'Y' if code in v53_top20_codes else ' '
    v = factors['value']
    q = factors['quality']
    g = factors['growth']
    m = factors['momentum']
    s = factors['sentiment']
    print(f'  [{in_v53}] {name:8s} ({code}) score={score:.1f} V={v:.0f} Q={q:.0f} G={g:.0f} M={m:.0f} S={s:.0f}')

v53_only = [(c, n, s, f) for c, n, s, f in v53_sorted[:20] if c not in v50_top20_codes]
v50_only = [(c, n, s, f) for c, n, s, f in v50_sorted[:20] if c not in v53_top20_codes]

print()
print(f'V5.3 unique picks ({len(v53_only)}):')
for code, name, score, factors in v53_only:
    v = factors['value']
    q = factors['quality']
    g = factors['growth']
    m = factors['momentum']
    s = factors['sentiment']
    print(f'  + {name} ({code}) score={score:.1f} V={v:.0f} Q={q:.0f} G={g:.0f} M={m:.0f} S={s:.0f}')

print(f'V5.0 unique picks ({len(v50_only)}):')
for code, name, score, factors in v50_only:
    v = factors['value']
    q = factors['quality']
    g = factors['growth']
    m = factors['momentum']
    s = factors['sentiment']
    print(f'  - {name} ({code}) score={score:.1f} V={v:.0f} Q={q:.0f} G={g:.0f} M={m:.0f} S={s:.0f}')

# Statistical comparison
print()
print('=== Statistical Comparison ===')
v53_avg = sum(s for _, _, s, _ in v53_sorted[:20]) / 20
v50_avg = sum(s for _, _, s, _ in v50_sorted[:20]) / 20
v53_avg_quality = sum(f['quality'] for _, _, _, f in v53_sorted[:20]) / 20
v50_avg_quality = sum(f['quality'] for _, _, _, f in v50_sorted[:20]) / 20
v53_avg_growth = sum(f['growth'] for _, _, _, f in v53_sorted[:20]) / 20
v50_avg_growth = sum(f['growth'] for _, _, _, f in v50_sorted[:20]) / 20
v53_avg_sentiment = sum(f['sentiment'] for _, _, _, f in v53_sorted[:20]) / 20
v50_avg_sentiment = sum(f['sentiment'] for _, _, _, f in v50_sorted[:20]) / 20

print(f'Avg total score:  V5.3={v53_avg:.1f} V5.0={v50_avg:.1f}')
print(f'Avg quality:      V5.3={v53_avg_quality:.1f} V5.0={v50_avg_quality:.1f}')
print(f'Avg growth:       V5.3={v53_avg_growth:.1f} V5.0={v50_avg_growth:.1f}')
print(f'Avg sentiment:    V5.3={v53_avg_sentiment:.1f} V5.0={v50_avg_sentiment:.1f}')
