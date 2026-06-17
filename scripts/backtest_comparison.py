"""
对比回测：验证优化方案 vs 当前基线
不修改主代码，仅在脚本内实现优化逻辑并对比
"""
import sys, os, json, time, copy
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import numpy as np

from modules.scoring import evaluate_stock, SECTOR_PE_RANGES, calculate_buy_sell
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
        params = {'symbol': symbol, 'scale': '240', 'ma': 'no', 'datalen': str(datalen)}
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        data = json.loads(r.text)
        return data
    except Exception as e:
        return []


def get_top_stocks(top_n=30):
    """获取当前评分最高的股票列表"""
    quotes = get_realtime_quotes()
    if not quotes:
        return []

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
        # 把tech_data也保存下来，供优化版本使用
        scored.append({
            'code': code, 'name': q.name, 'price': q.price,
            'score': eval_result['score'],
            'v5_score': eval_result.get('v5_score', 0),
            'buy_sell_baseline': buy_sell,
            'pe': q.pe, 'roe': roe, 'gross_margin': gross_margin,
            'rev_growth': rev_growth, 'profit_growth': profit_growth,
            'stock_dict': stock_dict,
            'tech_data': tech_data,
            'sector_type': stock_dict.get('sector_type', 'default'),
        })

    scored.sort(key=lambda x: x['v5_score'], reverse=True)
    return scored[:top_n]


def calculate_buy_sell_v4(stock, v5_score, tech_data=None):
    """V4优化版本：行业感知fair_pe + 多因子upside + 技术动态折扣 + VWAP止损"""
    price = stock.get("price", 0)
    pe = stock.get("pe", 0)
    roe = stock.get("roe", 0)
    gross_margin = stock.get("gross_margin", 0)
    rev_growth = stock.get("rev_growth", 0)
    profit_growth = stock.get("profit_growth", 0)
    if price <= 0 or pe <= 0:
        return None

    td = tech_data or {}
    ma5 = td.get("ma5") or stock.get("ma5")
    ma20 = td.get("ma20") or stock.get("ma20")
    ma60 = td.get("ma60") or stock.get("ma60")
    recent_high = td.get("recent_high") or stock.get("recent_high")
    boll_upper = td.get("boll_upper") or stock.get("boll_upper")
    atr = td.get("atr") or stock.get("atr")
    rsi = td.get("rsi") or stock.get("rsi")
    boll_position = td.get("boll_position")
    boll_width_pct = td.get("boll_width_pct")
    macd_signal = td.get("macd_signal")
    obv_trend = td.get("obv_trend")
    vwap = td.get("vwap")
    momentum_20 = td.get("momentum_20", 0)
    momentum_60 = td.get("momentum_60", 0)
    turnover_rate = stock.get("turnover_rate", 0)

    # === 行业感知 fair_pe ===
    avg_growth = (rev_growth + profit_growth) / 2
    sector_type = stock.get("sector_type", "default")
    sector_info = SECTOR_PE_RANGES.get(sector_type)
    if sector_info:
        pe_low = sector_info["pe_fair_low"]
        pe_max = sector_info["pe_fair_max"]
    else:
        pe_low, pe_max = 12, 30
    roe_factor = min(max(roe / 20, 0), 1.0)
    growth_factor = min(max(avg_growth / 30, 0), 1.0)
    quality_blend = roe_factor * 0.6 + growth_factor * 0.4
    fair_pe = pe_low + (pe_max - pe_low) * quality_blend
    fair_pe = max(8, min(fair_pe, 120))

    if pe < fair_pe:
        pe_upside = (fair_pe - pe) / pe
    else:
        pe_upside = 0.0

    # === 基础买卖参数 ===
    star_rating = 1
    if pe < fair_pe:
        if v5_score >= 82:
            base_discount = 0.97
            rec = "强烈推荐"
            star_rating = 5 if v5_score >= 86 and roe >= 18 and gross_margin >= 28 else 4
            if star_rating == 4 and price * (1 - base_discount) <= price * 0.03:
                star_rating = 5
        elif v5_score >= 68:
            base_discount = 0.95
            rec = "推荐买入"
            star_rating = 4 if v5_score >= 75 else 3
        elif v5_score >= 55:
            base_discount = 0.92
            rec = "可逢低关注"
            star_rating = 3 if v5_score >= 62 else 2
        else:
            base_discount = 0.90
            rec = "轻度关注"
            star_rating = 1
    else:
        if v5_score >= 75 and pe < fair_pe * 1.3:
            base_discount = 0.90
            rec = "等待更好买点"
            star_rating = 3
        elif v5_score >= 58:
            base_discount = 0.88
            rec = "高估观望"
            star_rating = 2
        else:
            base_discount = 0.85
            rec = "暂不推荐"
            star_rating = 1

    # === 技术动态买入折扣调整 ===
    tech_adj = 0.0
    if rsi is not None:
        if rsi < 30:       tech_adj += 0.02
        elif rsi < 40:     tech_adj += 0.01
        elif rsi > 70:     tech_adj -= 0.03
        elif rsi > 60:     tech_adj -= 0.01
    if boll_position is not None:
        if boll_position < 0.2:   tech_adj += 0.02
        elif boll_position > 0.8: tech_adj -= 0.02
    if macd_signal:
        if macd_signal == "golden_cross":   tech_adj += 0.01
        elif macd_signal == "death_cross":  tech_adj -= 0.01
    if obv_trend:
        if obv_trend == "bullish":   tech_adj += 0.01
        elif obv_trend == "bearish": tech_adj -= 0.01
    if ma5 and ma20 and ma5 > 0 and ma20 > 0:
        if ma5 > ma20:    tech_adj += 0.01
        else:              tech_adj -= 0.02
    if turnover_rate and turnover_rate > 5:
        tech_adj += 0.01
    tech_adj = max(-0.05, min(0.03, tech_adj))
    buy_discount = max(0.82, min(0.98, base_discount + tech_adj))

    # === 多因子 upside ===
    upside = pe_upside
    if momentum_20 > 0 and momentum_60 > 0:
        upside += min(momentum_20 * 0.002, 0.10)
    elif momentum_20 > 5:
        upside += 0.03
    if obv_trend == "bullish":
        upside += 0.05
    if boll_width_pct is not None and boll_width_pct < 8:
        upside += 0.05
    if rsi is not None and rsi < 30:
        upside += 0.03
    upside = max(upside, 0.08)
    if v5_score >= 75:    upside = min(upside, 0.45)
    elif v5_score >= 60:  upside = min(upside, 0.35)
    else:                 upside = min(upside, 0.25)

    # === 买入价 ===
    buy_point = round(price * buy_discount, 2)
    if ma20 and ma20 > 0 and ma20 < price:
        buy_point = max(buy_point, round(ma20 * 0.995, 2))
    if ma60 and ma60 > 0 and ma60 < price:
        buy_point = max(buy_point, round(ma60 * 0.99, 2))
    if v5_score >= 82:   floor_pct = 0.90
    elif v5_score >= 68: floor_pct = 0.88
    elif v5_score >= 55: floor_pct = 0.86
    else:                floor_pct = 0.84
    buy_point = max(buy_point, round(price * floor_pct, 2))

    # === 卖出价 ===
    sell_point = round(price * (1 + upside), 2)
    if recent_high and recent_high > price:
        resistance = round(recent_high * 1.08, 2)
        if sell_point > resistance and upside > 0.15:
            weight = 0.7 if v5_score >= 75 else 0.5
            sell_point = round(sell_point * weight + resistance * (1 - weight), 2)
        elif sell_point > resistance:
            sell_point = round((sell_point + resistance) / 2, 2)
    if boll_upper and boll_upper > price and sell_point > boll_upper * 1.15:
        sell_point = round(min(sell_point, boll_upper * 1.15), 2)
    if sell_point <= buy_point * 1.10:
        sell_point = round(buy_point * 1.15, 2)

    # === 止损价 ===
    if atr and atr > 0 and price > 0:
        atr_stop_pct = 2 * atr / price * 100
        atr_stop_pct = max(5.0, min(12.0, atr_stop_pct))
        stop_loss = round(price * (1 - atr_stop_pct / 100), 2)
    else:
        stop_loss = round(price * 0.92, 2)
    if vwap and vwap > 0 and vwap > buy_point:
        vwap_stop = round(vwap * 0.98, 2)
        # VWAP止损不应超过买入价（否则不合理）
        if vwap_stop > stop_loss and vwap_stop < buy_point: stop_loss = vwap_stop
    if ma20 and ma20 > 0 and stop_loss < ma20 < price:
        support_stop = round(ma20 * 0.98, 2)
        # MA20止损不应超过买入价
        if support_stop > stop_loss and support_stop < buy_point: stop_loss = support_stop
    stop_floor = round(buy_point * 0.92, 2)
    if stop_loss < stop_floor:
        stop_loss = stop_floor

    # === 仓位 ===
    atr_val = atr or 0
    if atr_val and price > 0:
        volatility = atr_val / price * 100
    else:
        volatility = abs(stock.get("change_pct", 0)) or 2.0
    if volatility < 2:   position_pct = 20
    elif volatility < 4: position_pct = 15
    else:                position_pct = 10
    if rsi is not None:
        if rsi < 35:   position_pct += 3
        elif rsi > 65: position_pct -= 2
    if v5_score >= 82:    position_pct += 2
    elif v5_score < 55:   position_pct -= 2
    position_pct = max(5, min(25, position_pct))

    risk_reward_ratio = 0.0
    if price > stop_loss:
        risk_reward_ratio = round((sell_point - price) / (price - stop_loss), 2)

    return {
        "current": price,
        "buy": buy_point,
        "sell": sell_point,
        "stop_loss": stop_loss,
        "position_pct": position_pct,
        "risk_reward_ratio": risk_reward_ratio,
        "upside": round((sell_point - price) / price * 100, 1),
        "downside": round((price - buy_point) / price * 100, 1),
        "recommendation": rec,
        "star_rating": star_rating,
    }


def backtest_buy_sell_comparison(stocks, kline_cache=None):
    """对比回测：基线 vs V4优化版"""
    baseline_stats = {
        'total': 0, 'buy_reached': 0, 'sell_reached': 0, 'stop_reached': 0,
        'buy_then_sell': 0, 'buy_then_stop': 0,
        'max_gain_pct': [], 'max_loss_pct': [],
        'upside_pred': [], 'upside_actual': [],
        'rr_pred': [], 'rr_actual': [],
    }
    v4_stats = copy.deepcopy(baseline_stats)

    baseline_results = []
    v4_results = []

    for i, stock in enumerate(stocks):
        code = stock['code']
        name = stock['name']
        price = stock['price']
        v5_score = stock['v5_score']

        # 基线结果（来自evaluate_stock）
        bs_base = stock['buy_sell_baseline']
        # V4优化结果
        stock_with_sector = copy.deepcopy(stock['stock_dict'])
        stock_with_sector['sector_type'] = stock.get('sector_type', 'default')
        # 合入技术指标到stock dict（V4从tech_data取，但需要兼容无tech_data的情况）
        td = stock.get('tech_data') or {}
        if td:
            for key in ('atr', 'ma20', 'ma60', 'recent_high', 'boll_upper', 'rsi', 'ma5'):
                if td.get(key):
                    stock_with_sector[key] = td[key]
        bs_v4 = calculate_buy_sell_v4(stock_with_sector, v5_score, tech_data=td)

        if not bs_v4:
            print(f'  [{i+1}] {code} {name} V4=None, skip')
            continue

        print(f'\n[{i+1}/{len(stocks)}] {code} {name} price={price:.2f}')
        print(f'  BASE: Buy={bs_base["buy"]:.2f} Sell={bs_base["sell"]:.2f} Upside={bs_base.get("upside","N/A")}%')
        print(f'  V4:   Buy={bs_v4["buy"]:.2f} Sell={bs_v4["sell"]:.2f} Upside={bs_v4["upside"]}% Stop={bs_v4["stop_loss"]:.2f}')

        # 获取K线数据
        klines = get_history_kline_sina(code, datalen=60)
        if not klines or len(klines) < 5:
            print('  [Skip] No history data')
            continue

        recent = klines[-30:]
        highs = [float(k['high']) for k in recent]
        lows = [float(k['low']) for k in recent]

        # --- 基线回测 ---
        baseline_stats['total'] += 1
        br = any(low <= bs_base["buy"] for low in lows)
        sr = any(high >= bs_base["sell"] for high in highs)
        # 基线没有stop_loss字段，用估算
        base_stop = round(price * 0.92, 2)  # 默认8%止损
        strr = any(low <= base_stop for low in lows)
        if br: baseline_stats['buy_reached'] += 1
        if sr: baseline_stats['sell_reached'] += 1
        if strr: baseline_stats['stop_reached'] += 1
        if br:
            buy_day = next(j for j, low in enumerate(lows) if low <= bs_base["buy"])
            if any(highs[k] >= bs_base["sell"] for k in range(buy_day, len(highs))):
                baseline_stats['buy_then_sell'] += 1
            if any(lows[k] <= base_stop for k in range(buy_day, len(lows))):
                baseline_stats['buy_then_stop'] += 1
        max_high = max(highs)
        min_low = min(lows)
        max_gain = (max_high - price) / price * 100
        max_loss = (min_low - price) / price * 100
        baseline_stats['max_gain_pct'].append(max_gain)
        baseline_stats['max_loss_pct'].append(max_loss)
        baseline_stats['upside_pred'].append(bs_base.get("upside", 0))
        baseline_stats['upside_actual'].append(max_gain)
        rr_pred_base = bs_base.get("risk_reward_ratio", 0) or 0
        baseline_stats['rr_pred'].append(rr_pred_base)
        rr_actual = max_gain / abs(max_loss) if abs(max_loss) > 0.01 else 0
        baseline_stats['rr_actual'].append(rr_actual)

        # --- V4回测 ---
        v4_stats['total'] += 1
        v4_br = any(low <= bs_v4["buy"] for low in lows)
        v4_sr = any(high >= bs_v4["sell"] for high in highs)
        v4_strr = any(low <= bs_v4["stop_loss"] for low in lows)
        if v4_br: v4_stats['buy_reached'] += 1
        if v4_sr: v4_stats['sell_reached'] += 1
        if v4_strr: v4_stats['stop_reached'] += 1
        if v4_br:
            buy_day = next(j for j, low in enumerate(lows) if low <= bs_v4["buy"])
            if any(highs[k] >= bs_v4["sell"] for k in range(buy_day, len(highs))):
                v4_stats['buy_then_sell'] += 1
            if any(lows[k] <= bs_v4["stop_loss"] for k in range(buy_day, len(lows))):
                v4_stats['buy_then_stop'] += 1
        v4_stats['max_gain_pct'].append(max_gain)
        v4_stats['max_loss_pct'].append(max_loss)
        v4_stats['upside_pred'].append(bs_v4["upside"])
        v4_stats['upside_actual'].append(max_gain)
        v4_stats['rr_pred'].append(bs_v4["risk_reward_ratio"])
        v4_stats['rr_actual'].append(rr_actual)

        baseline_results.append({
            'code': code, 'name': name, 'price': price,
            'buy': bs_base["buy"], 'sell': bs_base["sell"], 'stop': base_stop,
            'buy_reached': br, 'sell_reached': sr, 'stop_reached': strr,
            'max_gain': round(max_gain, 2), 'max_loss': round(max_loss, 2),
            'upside_pred': bs_base.get("upside", 0), 'v5_score': v5_score,
        })
        v4_results.append({
            'code': code, 'name': name, 'price': price,
            'buy': bs_v4["buy"], 'sell': bs_v4["sell"], 'stop': bs_v4["stop_loss"],
            'buy_reached': v4_br, 'sell_reached': v4_sr, 'stop_reached': v4_strr,
            'max_gain': round(max_gain, 2), 'max_loss': round(max_loss, 2),
            'upside_pred': bs_v4["upside"], 'v5_score': v5_score,
        })
        time.sleep(0.1)

    return baseline_stats, v4_stats, baseline_results, v4_results


def print_stats(label, stats):
    total = stats['total']
    if total == 0:
        print(f'\n=== {label} === No valid data')
        return
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
    avg_upside_pred = np.mean(stats['upside_pred'])
    avg_upside_actual = np.mean(stats['upside_actual'])
    avg_max_gain = np.mean(stats['max_gain_pct'])
    avg_max_loss = np.mean(stats['max_loss_pct'])
    avg_rr_pred = np.mean(stats['rr_pred'])
    rr_valid = [x for x in stats['rr_actual'] if x > 0]
    avg_rr_actual = np.mean(rr_valid) if rr_valid else 0
    gap = abs(avg_upside_pred - avg_upside_actual)

    print(f'\n=== {label} ===')
    print(f'Stocks: {total}')
    print(f'Buy hit rate:   {buy_rate:.1f}% ({br}/{total})')
    print(f'Sell hit rate:  {sell_rate:.1f}% ({sr}/{total})')
    print(f'Stop hit rate:  {stop_rate:.1f}% ({strr}/{total})')
    print(f'Buy->Sell:      {buy_sell_rate:.1f}% ({bts}/{br})')
    print(f'Buy->Stop:      {buy_stop_rate:.1f}% ({btst}/{br})')
    print(f'Avg pred upside: {avg_upside_pred:.1f}%')
    print(f'Avg actual gain: {avg_upside_actual:.1f}%')
    print(f'Gap:             {gap:.1f}%')
    print(f'Avg RR pred:     {avg_rr_pred:.2f}')
    print(f'Avg RR actual:   {avg_rr_actual:.2f}')


if __name__ == '__main__':
    print('Getting top stocks...')
    stocks = get_top_stocks(top_n=30)
    print(f'\nSelected {len(stocks)} stocks')

    print('\nRunning comparison backtest...')
    base_stats, v4_stats, base_results, v4_results = backtest_buy_sell_comparison(stocks)

    print_stats('BASELINE (current)', base_stats)
    print_stats('V4 OPTIMIZED', v4_stats)

    # 保存对比结果
    output = {
        'baseline': {
            'results': base_results,
            'summary': {
                'total': base_stats['total'],
                'buy_rate': round(base_stats['buy_reached'] / max(base_stats['total'],1) * 100, 2),
                'sell_rate': round(base_stats['sell_reached'] / max(base_stats['total'],1) * 100, 2),
                'avg_upside_pred': round(np.mean(base_stats['upside_pred']), 2),
                'avg_upside_actual': round(np.mean(base_stats['upside_actual']), 2),
                'avg_rr_pred': round(np.mean(base_stats['rr_pred']), 2),
            }
        },
        'v4': {
            'results': v4_results,
            'summary': {
                'total': v4_stats['total'],
                'buy_rate': round(v4_stats['buy_reached'] / max(v4_stats['total'],1) * 100, 2),
                'sell_rate': round(v4_stats['sell_reached'] / max(v4_stats['total'],1) * 100, 2),
                'avg_upside_pred': round(np.mean(v4_stats['upside_pred']), 2),
                'avg_upside_actual': round(np.mean(v4_stats['upside_actual']), 2),
                'avg_rr_pred': round(np.mean(v4_stats['rr_pred']), 2),
            }
        }
    }

    output_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'data', 'backtest_comparison_result.json')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f'\nResults saved to: {output_path}')