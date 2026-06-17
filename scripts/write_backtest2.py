import os
target = os.path.join('D:', os.sep, 'UI', 'jztz_v17', 'scripts', 'backtest_buy_sell.py')
code = r'''"""
Backtest: Verify buy/sell price recommendations with real data
Uses Sina Finance API for K-line data (more reliable than akshare)
"""
import sys, os, json, time
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import numpy as np

from modules.scoring import evaluate_stock
from modules.data_fetcher import get_realtime_quotes, get_financial_data, get_preset_financials
from modules.technical import calculate_technical_indicators
from modules.logger import log


def get_history_kline_sina(code, datalen=60):
    """Get K-line data from Sina Finance API"""
    try:
        if code.startswith(('0', '3')):
            symbol = 'sz' + code
        else:
            symbol = 'sh' + code
        url = 'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData'
        params = {'symbol': symbol, 'scale': '240', 'ma': 'no', 'datalen': str(dalen)}
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        data = json.loads(r.text)
        return data
    except Exception as e:
        return []


def backtest_current_picks(top_n=20):
    quotes = get_realtime_quotes()
    if not quotes:
        print('Cannot get quotes')
        return {}

    candidates = {}
    for code, q in quotes.items():
        name = q.name or ''
        if 'ST' in name or '*' in name:
            continue
        if code.startswith('9') or code.startswith('8') or code.startswith('4'):
            continue
        if q.price <= 1:
            continue
        candidates[code] = q

    codes = list(candidates.keys())[:800]
    financials = get_financial_data(codes)
    preset_financials = get_preset_financials()

    scored = []
    for code in codes:
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
            pf = preset_financials.get(code, {})
            if pf.get('pb', 0) > 0:
                pb = pf['pb']
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
        try:
            tech_data = calculate_technical_indicators(code, days=30)
        except:
            tech_data = None
        eval_result = evaluate_stock(stock_dict, tech_data=tech_data)
        if not eval_result or eval_result.get('score', 0) < 50:
            continue
        buy_sell = eval_result.get('buy_sell')
        if not buy_sell:
            continue
        scored.append({
            'code': code, 'name': q.name, 'price': q.price,
            'score': eval_result['score'],
            'v5_score': eval_result.get('v5_score', 0),
            'buy_sell': buy_sell, 'pe': q.pe, 'roe': roe,
        })

    scored.sort(key=lambda x: x['v5_score'], reverse=True)
    stocks = scored[:top_n]
    print(f'\n=== Selected {len(stocks)} stocks for backtest ===')

    stats = {
        'total': 0, 'buy_reached': 0, 'sell_reached': 0, 'stop_reached': 0,
        'buy_then_sell': 0, 'buy_then_stop': 0,
        'max_gain_pct': [], 'max_loss_pct': [],
        'upside_pred': [], 'upside_actual': [],
        'rr_pred': [], 'rr_actual': [],
    }

    results = []
    for i, stock in enumerate(stocks):
        code = stock['code']
        name = stock['name']
        price = stock['price']
        bs = stock['buy_sell']
        buy = bs['buy']
        sell = bs['sell']
        stop = bs['stop_loss']
        upside_pred = bs['upside']
        rr_pred = bs['risk_reward_ratio']
        rec = bs['recommendation']
        star = bs['star_rating']
        print(f'\n[{i+1}/{len(stocks)}] {code} {name}')
        print(f'  Price={price:.2f} Buy={buy:.2f} Sell={sell:.2f} Stop={stop:.2f}')
        print(f'  Upside={upside_pred}% RR={rr_pred} Rec={rec} Star={star}')

        # Use Sina API to get 60 days of K-line data
        klines = get_history_kline_sina(code, datalen=60)
        if not klines or len(klines) < 5:
            print('  [Skip] No history data')
            continue

        # Use last 30 trading days
        recent = klines[-30:]
        highs = [float(k['high']) for k in recent]
        lows = [float(k['low']) for k in recent]
        closes = [float(k['close']) for k in recent]

        stats['total'] += 1
        buy_reached = any(low <= buy for low in lows)
        sell_reached = any(high >= sell for high in highs)
        stop_reached = any(low <= stop for low in lows)
        if buy_reached: stats['buy_reached'] += 1
        if sell_reached: stats['sell_reached'] += 1
        if stop_reached: stats['stop_reached'] += 1

        if buy_reached:
            buy_day = None
            for j, low in enumerate(lows):
                if low <= buy:
                    buy_day = j
                    break
            if buy_day is not None:
                sell_after = any(highs[k] >= sell for k in range(buy_day, len(highs)))
                stop_after = any(lows[k] <= stop for k in range(buy_day, len(lows)))
                if sell_after: stats['buy_then_sell'] += 1
                if stop_after: stats['buy_then_stop'] += 1

        max_high = max(highs)
        min_low = min(lows)
        max_gain = (max_high - price) / price * 100
        max_loss = (min_low - price) / price * 100
        stats['max_gain_pct'].append(max_gain)
        stats['max_loss_pct'].append(max_loss)
        stats['upside_pred'].append(upside_pred)
        stats['upside_actual'].append(max_gain)
        stats['rr_pred'].append(rr_pred)
        rr_actual = max_gain / abs(max_loss) if abs(max_loss) > 0.01 else 0
        stats['rr_actual'].append(rr_actual)
        print(f'  30d: High={max_high:.2f} Low={min_low:.2f}')
        print(f'  MaxGain={max_gain:.1f}% MaxLoss={max_loss:.1f}%')
        print(f'  BuyHit={buy_reached} SellHit={sell_reached} StopHit={stop_reached}')
        results.append({
            'code': code, 'name': name, 'price': price,
            'buy': buy, 'sell': sell, 'stop': stop,
            'buy_reached': buy_reached, 'sell_reached': sell_reached,
            'stop_reached': stop_reached,
            'max_gain': round(max_gain, 2), 'max_loss': round(max_loss, 2),
            'upside_pred': upside_pred, 'upside_actual': round(max_gain, 2),
            'rr_pred': rr_pred, 'v5_score': stock['v5_score'],
        })
        time.sleep(0.1)  # Rate limit

    total = stats['total']
    if total == 0:
        print('No valid data')
        return {}
    br = stats['buy_reached']
    sr = stats['sell_reached']
    strr = stats['stop_reached']
    bts = stats['buy_then_sell']
    btst = stats['buy_then_stop']
    buy_rate = br / total * 100
    sell_rate = sr / total * 100
    stop_rate = strr / total * 100
    buy_sell_rate = bts / max(br, 1) * 100
    buy_stop_rate = btst / max(br, 1) * 100
    avg_max_gain = np.mean(stats['max_gain_pct'])
    avg_max_loss = np.mean(stats['max_loss_pct'])
    avg_upside_pred = np.mean(stats['upside_pred'])
    avg_upside_actual = np.mean(stats['upside_actual'])
    avg_rr_pred = np.mean(stats['rr_pred'])
    rr_valid = [x for x in stats['rr_actual'] if x > 0]
    avg_rr_actual = np.mean(rr_valid) if rr_valid else 0

    print('\n' + '=' * 60)
    print('=== BUY/SELL BACKTEST SUMMARY ===')
    print('=' * 60)
    print(f'Stocks tested: {total}')
    print()
    print('--- Hit Rate ---')
    print('Buy price hit rate: {:.1f}% ({}/{})'.format(buy_rate, br, total))
    print('Sell price hit rate: {:.1f}% ({}/{})'.format(sell_rate, sr, total))
    print('Stop loss hit rate: {:.1f}% ({}/{})'.format(stop_rate, strr, total))
    print()
    print('--- Trade Path ---')
    print('Buy->Sell: {:.1f}% ({}/{})'.format(buy_sell_rate, bts, br))
    print('Buy->Stop: {:.1f}% ({}/{})'.format(buy_stop_rate, btst, br))
    print()
    print('--- Magnitude ---')
    print('Avg predicted upside: {:.1f}%'.format(avg_upside_pred))
    print('Avg actual max gain: {:.1f}%'.format(avg_upside_actual))
    gap = abs(avg_upside_pred - avg_upside_actual)
    direction = 'above' if avg_upside_pred > avg_upside_actual else 'below'
    print('Gap: {:.1f}% (pred {} actual)'.format(gap, direction))
    print('Avg max gain: {:.1f}%'.format(avg_max_gain))
    print('Avg max loss: {:.1f}%'.format(avg_max_loss))
    print()
    print('--- Risk/Reward ---')
    print('Avg predicted RR: {:.2f}'.format(avg_rr_pred))
    print('Avg actual RR: {:.2f}'.format(avg_rr_actual))

    print('\n--- Diagnosis ---')
    if buy_rate > 80:
        print('!! Buy discount too small! >80% hit rate means buy price too easy to reach')
        print('   Suggestion: reduce buy discount (e.g. from 0.92 to 0.95)')
    elif buy_rate < 30:
        print('!! Buy discount too large! <30% hit rate means buy price set too low')
        print('   Suggestion: increase buy discount (e.g. from 0.92 to 0.90)')
    else:
        print('OK Buy discount reasonable (30-80% hit rate)')
    if sell_rate < 20:
        print('!! Sell target too high! <20% hit rate, consider lowering target price')
    elif sell_rate > 60:
        print('!! Sell target too low! >60% hit rate, can raise target price')
    else:
        print('OK Sell target reasonable (20-60% hit rate)')
    if avg_upside_pred > avg_upside_actual * 1.5:
        print('!! Predicted upside ({:.1f}%) far above actual ({:.1f}%)'.format(avg_upside_pred, avg_upside_actual))
        print('   Suggestion: reduce upside cap or adjust fair_pe formula')
    if avg_rr_pred > avg_rr_actual * 1.5 and avg_rr_actual > 0:
        print('!! Predicted RR ({:.2f}) far above actual ({:.2f})'.format(avg_rr_pred, avg_rr_actual))
        print('   Suggestion: model is too optimistic, reduce predicted upside or increase stop loss')

    return {
        'results': results,
        'summary': {
            'total': total, 'buy_rate': round(buy_rate, 2), 'sell_rate': round(sell_rate, 2),
            'stop_rate': round(stop_rate, 2),
            'avg_max_gain': round(avg_max_gain, 2), 'avg_max_loss': round(avg_max_loss, 2),
            'avg_upside_pred': round(avg_upside_pred, 2), 'avg_upside_actual': round(avg_upside_actual, 2),
            'avg_rr_pred': round(avg_rr_pred, 2), 'avg_rr_actual': round(avg_rr_actual, 2),
        }
    }


if __name__ == '__main__':
    print('Starting buy/sell backtest...')
    result = backtest_current_picks(top_n=20)
    if result:
        output_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'backtest_buy_sell_result.json')
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'\nResult saved to: {output_path}')
'''
with open(target, 'w', encoding='utf-8') as f:
    f.write(code)
print('Written to', target)
