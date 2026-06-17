import sys
sys.path.insert(0, '.')
from scripts.backtest_wp2_strategy import get_kline, old_strategy_score, calc_ma, calc_rsi, calc_macd, get_market_cap

# 测试市值获取
test_codes = ['000001', '600519', '000858', '601318', '002475', '300750', '600036']
caps = get_market_cap(test_codes)
print(f"市值数据: {caps}")

# 测试评分
for code in test_codes:
    kline = get_kline(code, 120)
    if not kline or len(kline) < 30:
        print(f"{code}: K线不足")
        continue
    last = kline[-1]
    prev = kline[-2]
    pct = round((last['close'] - prev['close']) / prev['close'] * 100, 2)
    cap = caps.get(code, 0)
    stock = {
        'code': code, 'name': 'test', 'price': last['close'], 'pct': pct,
        'amt': last['vol'] * last['close'], 'vol': last['vol'], 'cap': cap,
        'turnover': 0, 'high': last['high'], 'low': last['low'], 'open': last['open'],
    }
    result = old_strategy_score(stock, kline)
    print(f"{code}: cap={cap}亿 pct={pct}% score={result}")
