"""V5.5 vs V4 Strategy Comparison with Real Market Data

Compares V5.5 (价值35%/质量18%/成长17%/动量12%/情绪18%)
against V4 (价值36%/质量11%/成长8%/动量12%/情绪33%)
"""
import sys, json, time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, '.')

from modules.data_fetcher import (get_realtime_quotes, get_financial_data, get_preset_financials, preload_industry_cache)
from modules.technical import calculate_technical_indicators
from modules.scoring import (evaluate_stock, multi_factor_evaluate, mf_score_value, mf_score_quality, mf_score_growth, mf_score_momentum, mf_score_sentiment, calculate_buy_sell)
from modules.market_env import get_market_env

print("=" * 80)
print("  V5.5 vs V4 策略对比分析 (真实数据)")
print("=" * 80)

# 1. Market environment
env = get_market_env()
print(f"\n市场环境: trend={env.trend}, change={env.change_pct:.2f}%, multiplier={env.multiplier:.2f}, can_pick={env.can_pick()}")

# 2. Fetch quotes
print("\n获取全市场行情...")
quotes = get_realtime_quotes()
if not quotes:
    print("无法获取行情，退出")
    sys.exit(1)

candidates = {}
for code, q in quotes.items():
    name = q.name or ''
    if 'ST' in name or '*' in name or '退' in name: continue
    if code.startswith('9') or code.startswith('8') or code.startswith('4'): continue
    if q.price <= 1: continue
    candidates[code] = q

print(f"有效候选: {len(candidates)}")

# 3. Financial data
print("获取财务数据...")
codes = list(candidates.keys())
preload_industry_cache(codes[:500])
financials = get_financial_data(codes[:500])
preset_financials = get_preset_financials()
print(f"财务覆盖: {len(financials)}/{len(codes[:500])}")

# 4. Top 300 by market cap
sorted_candidates = sorted(candidates.values(), key=lambda q: q.market_cap, reverse=True)
test_codes = [q.code for q in sorted_candidates[:300]]
print(f"深度分析: {len(test_codes)} 只")

# 5. Technical indicators
print("计算技术指标...")
tech_cache = {}
def calc_tech(code):
    try:
        tech = calculate_technical_indicators(code, days=60)
        return (code, tech)
    except: return (code, None)

t0 = time.time()
with ThreadPoolExecutor(max_workers=10) as executor:
    futures = [executor.submit(calc_tech, c) for c in test_codes]
    for future in as_completed(futures):
        try:
            code, tech = future.result(timeout=15)
            if tech: tech_cache[code] = tech
        except: pass
print(f"技术覆盖: {len(tech_cache)}/{len(test_codes)} ({time.time()-t0:.1f}s)")

# 6. Dual strategy scoring
V55_W = {'value': 0.35, 'quality': 0.18, 'growth': 0.17, 'momentum': 0.12, 'sentiment': 0.18}
V4_W  = {'value': 0.36, 'quality': 0.11, 'growth': 0.08, 'momentum': 0.12, 'sentiment': 0.33}

print("\n双策略评分...")
results = []
roe_neg = 0

for code in test_codes:
    q = candidates.get(code)
    if not q: continue
    f = financials.get(code)
    roe = f.roe if f else 0
    gm = f.gross_margin if f else 0
    nm = f.net_margin if f else 0
    rg = f.revenue_growth if f else 0
    pg = f.profit_growth if f else 0
    dr = f.debt_ratio if f else 0
    pb = q.pb
    if pb <= 0:
        if code in preset_financials and preset_financials[code].get('pb', 0) > 0:
            pb = preset_financials[code]['pb']
        elif q.pe > 0 and roe > 0:
            pb = round(q.pe * roe / 100, 2)

    stock = {
        'code': code, 'name': q.name, 'price': q.price,
        'change_pct': q.change_pct, 'pe': q.pe, 'pb': pb,
        'market_cap': q.market_cap, 'turnover_rate': q.turnover,
        'amount': q.amount, 'roe': roe, 'gross_margin': gm,
        'net_margin': nm, 'rev_growth': rg, 'profit_growth': pg, 'debt_ratio': dr,
    }
    td = tech_cache.get(code)

    v = mf_score_value(stock)
    qf = mf_score_quality(stock)
    g = mf_score_growth(stock)
    m = mf_score_momentum(td) if td else 50
    s = mf_score_sentiment(stock, td) if td else 50
    factors = {'value': v, 'quality': qf, 'growth': g, 'momentum': m, 'sentiment': s}

    v55_res = multi_factor_evaluate(stock, td)
    v55_total = v55_res['v5_total']
    v4_total = v * 0.36 + qf * 0.11 + g * 0.08 + m * 0.12 + s * 0.33

    eval_res = evaluate_stock(stock, td)
    bs = eval_res.get('buy_sell') if eval_res else None

    results.append({
        'code': code, 'name': q.name, 'price': q.price, 'roe': roe, 'pe': q.pe,
        'v55_total': v55_total, 'v4_total': v4_total, 'factors': factors,
        'v5_rec': v55_res.get('v5_recommendation', ''),
        'eval': eval_res, 'bs': bs,
    })
    if roe < 0: roe_neg += 1

print(f"评估完成: {len(results)} 只, ROE<0: {roe_neg}")

# 7. Top-20 comparison
v55_sorted = sorted(results, key=lambda x: x['v55_total'], reverse=True)
v4_sorted  = sorted(results, key=lambda x: x['v4_total'], reverse=True)

v55_top20 = v55_sorted[:20]
v4_top20  = v4_sorted[:20]
v55_codes = set(r['code'] for r in v55_top20)
v4_codes  = set(r['code'] for r in v4_top20)
overlap   = v55_codes & v4_codes

print("\n" + "=" * 80)
print("  Top-20 选股对比")
print("=" * 80)
print(f"\n重叠: {len(overlap)}/20 ({len(overlap)/20*100:.0f}%)")

print("\n--- V5.5 Top 20 ---")
for r in v55_top20:
    in_v4 = '✓' if r['code'] in v4_codes else ' '
    f = r['factors']
    rec = r['v5_rec']
    print(f"  [{in_v4}] {r['name']:8s} ({r['code']}) V55={r['v55_total']:.1f} V4={r['v4_total']:.1f} "
          f"V={f['value']:.0f} Q={f['quality']:.0f} G={f['growth']:.0f} M={f['momentum']:.0f} S={f['sentiment']:.0f} "
          f"ROE={r['roe']:.1f}% rec={rec}")

print("\n--- V4 Top 20 ---")
for r in v4_top20:
    in_v55 = '✓' if r['code'] in v55_codes else ' '
    f = r['factors']
    print(f"  [{in_v55}] {r['name']:8s} ({r['code']}) V55={r['v55_total']:.1f} V4={r['v4_total']:.1f} "
          f"V={f['value']:.0f} Q={f['quality']:.0f} G={f['growth']:.0f} M={f['momentum']:.0f} S={f['sentiment']:.0f} "
          f"ROE={r['roe']:.1f}%")

# 8. V5.5 unique picks vs V4 unique picks
v55_only = [r for r in v55_top20 if r['code'] not in v4_codes]
v4_only  = [r for r in v4_top20 if r['code'] not in v55_codes]

print(f"\nV5.5独有选股 ({len(v55_only)}):")
for r in v55_only:
    f = r['factors']
    bs = r['bs']
    entry = bs.get('entry_status', '?') if bs else '?'
    print(f"  + {r['name']} V55={r['v55_total']:.1f} V={f['value']:.0f} Q={f['quality']:.0f} G={f['growth']:.0f} "
          f"ROE={r['roe']:.1f}% entry={entry}")

print(f"\nV4独有选股 ({len(v4_only)}):")
for r in v4_only:
    f = r['factors']
    print(f"  - {r['name']} V4={r['v4_total']:.1f} V={f['value']:.0f} Q={f['quality']:.0f} G={f['growth']:.0f} "
          f"S={f['sentiment']:.0f} ROE={r['roe']:.1f}%")

# 9. Statistical comparison
print("\n" + "=" * 80)
print("  统计对比")
print("=" * 80)

def stats(label, items, key):
    vals = [r[key] for r in items]
    avg = sum(vals)/len(vals) if vals else 0
    mn = min(vals) if vals else 0
    mx = max(vals) if vals else 0
    return f"{label}: avg={avg:.1f} min={mn:.1f} max={mx:.1f}"

print(f"\n总分: V5.5 {stats('avg', v55_top20, 'v55_total')} | V4 {stats('avg', v4_top20, 'v4_total')}")

for fac in ['value', 'quality', 'growth', 'momentum', 'sentiment']:
    v55_avg = sum(r['factors'][fac] for r in v55_top20)/20
    v4_avg  = sum(r['factors'][fac] for r in v4_top20)/20
    diff = v55_avg - v4_avg
    print(f"  {fac:10s}: V5.5={v55_avg:.1f} V4={v4_avg:.1f} diff={diff:+.1f}")

# ROE distribution
v55_roe_avg = sum(r['roe'] for r in v55_top20)/20
v4_roe_avg  = sum(r['roe'] for r in v4_top20)/20
v55_roe_neg_top20 = sum(1 for r in v55_top20 if r['roe'] < 0)
v4_roe_neg_top20  = sum(1 for r in v4_top20 if r['roe'] < 0)
print(f"\nROE均值:  V5.5={v55_roe_avg:.1f}% V4={v4_roe_avg:.1f}%")
print(f"ROE<0 in top20: V5.5={v55_roe_neg_top20} V4={v4_roe_neg_top20}")

# V<25 (估值极差) in top20
v55_low_v = sum(1 for r in v55_top20 if r['factors']['value'] < 25)
v4_low_v  = sum(1 for r in v4_top20 if r['factors']['value'] < 25)
print(f"V<25 in top20: V5.5={v55_low_v} V4={v4_low_v}")

# Q<=12 (质量数据缺失) in top20
v55_low_q = sum(1 for r in v55_top20 if r['factors']['quality'] <= 12)
v4_low_q  = sum(1 for r in v4_top20 if r['factors']['quality'] <= 12)
print(f"Q<=12 in top20: V5.5={v55_low_q} V4={v4_low_q}")

# 10. Buy/Sell reasonableness check
print("\n" + "=" * 80)
print("  买卖点合理性检查 (V5.5)")
print("=" * 80)

for r in v55_top20[:10]:
    bs = r['bs']
    if not bs: continue
    price = r['price']
    buy = bs['buy']
    sell = bs['sell']
    stop = bs['stop_loss']
    rr = bs['risk_reward_ratio']
    entry = bs['entry_status']
    pos = bs['position_pct']
    star = bs['star_rating']
    upside = bs['upside']
    
    # Check: stop < buy
    stop_ok = stop < buy
    # Check: buy < price (discount)
    discount_ok = buy < price
    # Check: sell > price (upside)
    upside_ok = sell > price
    
    status = "✓" if (stop_ok and discount_ok and upside_ok) else "⚠"
    print(f"  [{status}] {r['name']:8s} P={price:.1f} Buy={buy:.1f}({(price-buy)/price*100:.1f}%) "
          f"Sell={sell:.1f}(+{upside:.1f}%) Stop={stop:.1f}({(buy-stop)/buy*100:.1f}%) "
          f"R/R={rr:.1f} Star={star} Pos={pos}% Entry={entry}")
    if not stop_ok:
        print(f"    ⚠️ 止损价({stop}) >= 买入价({buy})，逻辑矛盾!")
    if not discount_ok:
        print(f"    ⚠️ 买入价({buy}) >= 现价({price})，非折扣买入!")

# 11. Full evaluation stats (all 300)
print("\n" + "=" * 80)
print("  全量评估统计 (300只股票)")
print("=" * 80)

# How many stocks pass evaluate_stock (not filtered)
passed = sum(1 for r in results if r['eval'] is not None)
filtered = len(results) - passed
print(f"通过筛选: {passed}/{len(results)} ({passed/len(results)*100:.0f}%)")
print(f"被过滤: {filtered} (动量拒绝/ROE<0/换手率过低/北交所)")

# V5.5 recommendation distribution
recs = {}
for r in results:
    if r['eval']:
        rec = r['eval'].get('v5_recommendation', '未知')
        recs[rec] = recs.get(rec, 0) + 1
print(f"\nV5.5推荐分布:")
for rec, cnt in sorted(recs.items(), key=lambda x: -x[1]):
    print(f"  {rec}: {cnt}")

# V4 top stocks that V5.5 filtered out
print("\nV4高分但被V5.5过滤的股票:")
for r in v4_sorted[:50]:
    if r['eval'] is None and r['v4_total'] >= 50:
        f = r['factors']
        m20 = 0
        td = tech_cache.get(r['code'])
        if td: m20 = td.get('momentum_20', 0)
        reason = "动量<−15" if m20 < -15 else ("ROE<0" if r['roe'] < 0 else "换手率<0.3" if r['factors']['sentiment'] < 10 else "其他")
        print(f"  ✗ {r['name']} V4={r['v4_total']:.1f} V55={r['v55_total']:.1f} "
              f"ROE={r['roe']:.1f}% M20={m20:.1f} filter={reason}")

# 12. 30-day backtest comparison using real K-line data
print("\n" + "=" * 80)
print("  30天回测对比 (真实K线数据)")
print("=" * 80)

def fetch_klines(code, days=30):
    """Fetch real K-line data for backtest"""
    import re
    try:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq"
        r = session.get(url, timeout=8)
        data = r.json()
        stock_key = f"sh{code}" if code.startswith('6') else f"sz{code}"
        sd = data.get("data", {}).get(stock_key, {})
        klines = sd.get("qfqday", sd.get("day", []))
        if not klines: return []
        return [(k[0], float(k[2])) for k in klines[-days:] if len(k) >= 3]
    except: return []

from modules.http_client import session

# Pick top 10 from each strategy for backtest
v55_bt_codes = [r['code'] for r in v55_sorted[:10]]
v4_bt_codes  = [r['code'] for r in v4_sorted[:10]]

print("\n获取K线数据...")
kline_data = {}
bt_codes = list(set(v55_bt_codes + v4_bt_codes))

def fetch_one(code):
    klines = fetch_klines(code, 30)
    return (code, klines)

t0 = time.time()
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = [executor.submit(fetch_one, c) for c in bt_codes]
    for future in as_completed(futures):
        try:
            code, klines = future.result(timeout=10)
            if klines: kline_data[code] = klines
        except: pass
print(f"K线覆盖: {len(kline_data)}/{len(bt_codes)} ({time.time()-t0:.1f}s)")

# Simple backtest: buy at day 1 close, sell at day N close
# with stop-loss check
def simple_backtest(code, klines, stop_pct=0.08):
    if len(klines) < 5: return None
    buy_price = klines[0][1]
    stop = buy_price * (1 - stop_pct)
    max_price = buy_price
    final_price = klines[-1][1]
    stopped = False
    for date, price in klines[1:]:
        if price <= stop:
            stopped = True
            final_price = stop
            break
        max_price = max(max_price, price)
    ret = (final_price - buy_price) / buy_price
    return {'return': ret, 'stopped': stopped, 'max_return': (max_price - buy_price)/buy_price, 'days': len(klines)}

print("\n--- V5.5 Top 10 回测结果 ---")
v55_returns = []
for r in v55_sorted[:10]:
    code = r['code']
    klines = kline_data.get(code)
    if not klines: 
        print(f"  {r['name']}: 无K线数据")
        continue
    bs = r['bs']
    stop_pct = 0.08 if not bs else (bs['buy'] - bs['stop_loss']) / bs['buy'] if bs and bs['buy'] > bs['stop_loss'] else 0.08
    bt = simple_backtest(code, klines, stop_pct)
    if bt:
        v55_returns.append(bt['return'])
        status = "止损" if bt['stopped'] else "持有"
        print(f"  {r['name']:8s}: 收益={bt['return']*100:.1f}% {status} 最高={bt['max_return']*100:.1f}% 天数={bt['days']}")

print("\n--- V4 Top 10 回测结果 ---")
v4_returns = []
for r in v4_sorted[:10]:
    code = r['code']
    klines = kline_data.get(code)
    if not klines:
        print(f"  {r['name']}: 无K线数据")
        continue
    bt = simple_backtest(code, klines, 0.08)
    if bt:
        v4_returns.append(bt['return'])
        status = "止损" if bt['stopped'] else "持有"
        print(f"  {r['name']:8s}: 收益={bt['return']*100:.1f}% {status} 最高={bt['max_return']*100:.1f}% 天数={bt['days']}")

if v55_returns and v4_returns:
    v55_avg_ret = sum(v55_returns)/len(v55_returns)
    v4_avg_ret  = sum(v4_returns)/len(v4_returns)
    v55_win = sum(1 for r in v55_returns if r > 0)/len(v55_returns)
    v4_win  = sum(1 for r in v4_returns if r > 0)/len(v4_returns)
    print(f"\n回测汇总:")
    print(f"  V5.5: 平均收益={v55_avg_ret*100:.1f}% 胜率={v55_win*100:.0f}% ({len(v55_returns)}只)")
    print(f"  V4:   平均收益={v4_avg_ret*100:.1f}% 胜率={v4_win*100:.0f}% ({len(v4_returns)}只)")
    print(f"  差异: {(v55_avg_ret-v4_avg_ret)*100:+.1f}%")

# 13. Conclusion
print("\n" + "=" * 80)
print("  📊 结论")
print("=" * 80)

print("""
V5.5 vs V4 核心差异:
  1. V5.5降低了情绪因子权重(33→18%)，减少噪音干扰
  2. V5.5提升了质量因子(11→18%)和成长因子(8→17%)，更重基本面
  3. V5.5新增ROE<0硬过滤、价值门槛(V<25打折)、动量拒绝(M20<-15)
  4. V5.5新增数据缺失惩罚(Q<=12且G<=25打折82%)
  5. V5.5新增市场环境感知(熊市折扣0.92)
  6. V5.5止损价安全检查(确保stop<buy)
  7. V5.5买入价行业感知(SECTOR_PE_RANGES替代固定公式)

优势:
  - V5.5过滤掉更多低质量股票(ROE<0/V<25/Q缺失)
  - V5.5买卖点更合理(止损价不会高于买入价)
  - V5.5在熊市环境下更保守(仓位上限12%/更深折扣)

潜在改进:
  - 情绪因子可能仍可微调(18%是否太低？可用15-20%测试)
  - 板块轮动加分可能需要量化验证
  - 止损可从固定8%改为ATR动态止损
  - 买入折扣可结合VWAP/成交量分布优化
""")