import requests
import json

print("=" * 70)
print("最终全面测试 - 所有选股模块API")
print("=" * 70)

def test_api(name, url, key='stocks'):
    try:
        r = requests.get(url, timeout=30)
        d = r.json()
        stocks = d.get(key, d.get('results', d.get('top5', [])))
        success = d.get('success', False)
        print(f"\n{'─'*50}")
        print(f"✅ {name}: success={success} 数量={len(stocks)}")
        if stocks:
            s = stocks[0]
            print(f"   第1只: {s.get('code')} {s.get('name')}")
            for k, v in s.items():
                if v is not None and v != 0 and v != '' and v != [] and k not in ['dimensions', 'v5_factors', 'buy_sell', 'v5_reasons', 'reasons', 'phase1_details', 'phase2_details']:
                    print(f"     {k}={v}")
        return stocks
    except Exception as e:
        print(f"❌ {name}: 错误 {e}")
        return []

test_api("每日选股 /api/pick", "http://127.0.0.1:5559/api/pick", 'results')
test_api("每日选股V5 /api/pick_v5", "http://127.0.0.1:5559/api/pick_v5", 'top5')
test_api("竞价选股 /api/auction_pick", "http://127.0.0.1:5559/api/auction_pick")
test_api("尾盘选股 /api/wp2_pick", "http://127.0.0.1:5559/api/wp2_pick")
test_api("强势选股 /api/strong_pick", "http://127.0.0.1:5559/api/strong_pick")

print(f"\n{'─'*50}")
print("每日选股汇总 /api/daily_pick")
r = requests.get("http://127.0.0.1:5559/api/daily_pick", timeout=30)
d = r.json()
for period in ['morning', 'afternoon']:
    pd = d.get(period, {})
    stocks = pd.get('results', []) if pd else []
    print(f"  {period}: {len(stocks)}只", end="")
    if stocks:
        s = stocks[0]
        print(f" | 第1只: {s.get('code')} {s.get('name')} ROE={s.get('roe')} gross_margin={s.get('gross_margin')}", end="")
    print()

print(f"\n{'='*70}")
print("测试完成！")
