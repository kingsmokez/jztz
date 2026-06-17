
"""
V4 Comprehensive Backtest - Real Data Validation

Test methodology:
1. Use real stock data from top-scoring picks
2. Fetch 120 days of K-line history
3. Simulate buying on days when price drops to buy_point
4. Track: did we hit sell target? stop loss? what was the actual return?
5. Multiple holding periods: 10d, 20d, 30d
6. Separate analysis for different star ratings
"""
import sys, os, json, time
from datetime import datetime, timedelta
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import numpy as np

from modules.scoring import evaluate_stock
from modules.data_fetcher import get_realtime_quotes, get_financial_data, get_preset_financials
from modules.technical import calculate_technical_indicators
from modules.logger import log


def get_history_kline_sina(code, datalen=120):
    """Get K-line data from Sina Finance API"""
    try:
        if code.startswith(("0", "3")):
            symbol = "sz" + code
        else:
            symbol = "sh" + code
        url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(datalen)}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        data = json.loads(r.text)
        return data
    except Exception as e:
        return []


def simulate_trade(klines, buy_price, sell_price, stop_loss, hold_days=30):
    """Simulate a trade: buy when price drops to buy_price, track outcome

    Returns dict with trade result
    """
    if not klines or len(klines) < 5:
        return None

    # Find all days where low <= buy_price (buy trigger)
    buy_days = []
    for i, k in enumerate(klines):
        if float(k["low"]) <= buy_price:
            buy_days.append(i)

    if not buy_days:
        return {"buy_triggered": False, "reason": "buy_price_not_reached"}

    # Use the first buy trigger
    buy_day_idx = buy_days[0]
    buy_date = klines[buy_day_idx]["day"]

    # Simulate holding for hold_days
    end_idx = min(buy_day_idx + hold_days, len(klines))
    future_klines = klines[buy_day_idx:end_idx]

    if len(future_klines) < 2:
        return {"buy_triggered": True, "reason": "insufficient_data_after_buy"}

    # Track daily outcomes
    hit_sell = False
    hit_stop = False
    sell_day = None
    stop_day = None
    max_gain = 0
    max_loss = 0
    daily_returns = []

    for j, k in enumerate(future_klines[1:], 1):  # skip buy day
        high = float(k["high"])
        low = float(k["low"])
        close = float(k["close"])

        gain_from_buy = (high - buy_price) / buy_price * 100
        loss_from_buy = (low - buy_price) / buy_price * 100
        max_gain = max(max_gain, gain_from_buy)
        max_loss = min(max_loss, loss_from_buy)
        daily_returns.append((close - buy_price) / buy_price * 100)

        if not hit_sell and not hit_stop:
            if high >= sell_price:
                hit_sell = True
                sell_day = j
            elif low <= stop_loss:
                hit_stop = True
                stop_day = j

    # Determine final outcome
    if hit_sell and (not hit_stop or sell_day <= stop_day):
        outcome = "sell_hit"
        exit_day = sell_day
        exit_price = sell_price
        return_pct = (sell_price - buy_price) / buy_price * 100
    elif hit_stop:
        outcome = "stop_hit"
        exit_day = stop_day
        exit_price = stop_loss
        return_pct = (stop_loss - buy_price) / buy_price * 100
    else:
        # Holding period ended without hitting sell or stop
        final_close = float(future_klines[-1]["close"])
        outcome = "hold_end"
        exit_day = len(future_klines) - 1
        exit_price = final_close
        return_pct = (final_close - buy_price) / buy_price * 100

    return {
        "buy_triggered": True,
        "buy_date": buy_date,
        "outcome": outcome,
        "exit_day": exit_day,
        "return_pct": round(return_pct, 2),
        "max_gain": round(max_gain, 2),
        "max_loss": round(max_loss, 2),
        "hold_days_available": len(future_klines) - 1,
    }


def run_backtest(top_n=30, hold_days=30):
    """Main backtest function"""
    print("=" * 70)
    print(f"V4 COMPREHENSIVE BUY/SELL BACKTEST (hold={hold_days}d, top_n={top_n})")
    print("=" * 70)

    # Step 1: Get current stock data and scores
    print("\n[1/4] Getting real-time quotes and scoring...")
    quotes = get_realtime_quotes()
    if not quotes:
        print("Cannot get quotes")
        return {}

    candidates = {}
    for code, q in quotes.items():
        name = q.name or ""
        if "ST" in name or "*" in name:
            continue
        if code.startswith("9") or code.startswith("8") or code.startswith("4"):
            continue
        if q.price <= 1:
            continue
        candidates[code] = q

    codes = list(candidates.keys())[:800]
    financials = get_financial_data(codes)
    preset_financials = get_preset_financials()

    # Step 2: Score all candidates
    print(f"[2/4] Scoring {len(codes)} stocks...")
    scored = []
    for i, code in enumerate(codes):
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
            if pf.get("pb", 0) > 0:
                pb = pf["pb"]
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
            tech_data = calculate_technical_indicators(code, days=30)
        except:
            tech_data = None
        eval_result = evaluate_stock(stock_dict, tech_data=tech_data)
        if not eval_result or eval_result.get("score", 0) < 50:
            continue
        buy_sell = eval_result.get("buy_sell")
        if not buy_sell:
            continue
        scored.append({
            "code": code, "name": q.name, "price": q.price,
            "score": eval_result["score"],
            "v5_score": eval_result.get("v5_score", 0),
            "buy_sell": buy_sell, "pe": q.pe, "roe": roe,
        })

    scored.sort(key=lambda x: x["v5_score"], reverse=True)
    stocks = scored[:top_n]
    print(f"  Selected {len(stocks)} stocks for backtest")

    # Step 3: Fetch K-line data and simulate trades
    print(f"[3/4] Fetching K-line data and simulating trades...")
    results = []
    for i, stock in enumerate(stocks):
        code = stock["code"]
        name = stock["name"]
        price = stock["price"]
        bs = stock["buy_sell"]
        buy = bs["buy"]
        sell = bs["sell"]
        sell_first = bs.get("sell_first", sell)  # V5.2 first target
        stop = bs["stop_loss"]
        upside_pred = bs["upside"]
        rr_pred = bs["risk_reward_ratio"]
        rec = bs["recommendation"]
        star = bs["star_rating"]
        v5 = stock["v5_score"]

        print(f"\n  [{i+1}/{len(stocks)}] {code} {name} V5={v5:.0f} Star={star}")
        print(f"    Price={price:.2f} Buy={buy:.2f} Sell={sell:.2f} Stop={stop:.2f} Upside={upside_pred}%")

        klines = get_history_kline_sina(code, datalen=120)
        if not klines or len(klines) < 10:
            print(f"    [Skip] No history data")
            continue

        # Use last 90 trading days for backtesting
        recent = klines[-90:]

        # Simulate trade with 30-day hold
        trade_30 = simulate_trade(recent, buy, sell, stop, hold_days=30)
        trade_60 = simulate_trade(recent, buy, sell, stop, hold_days=60)
        trade_90 = simulate_trade(recent, buy, sell, stop, hold_days=90)
        # Simulate trade with 20-day hold
        trade_20 = simulate_trade(recent, buy, sell, stop, hold_days=20)
        # Simulate trade with 10-day hold
        trade_10 = simulate_trade(recent, buy, sell, stop, hold_days=10)

        result = {
            "code": code, "name": name, "price": price,
            "buy": buy, "sell": sell, "stop": stop,
            "upside_pred": upside_pred, "rr_pred": rr_pred,
            "v5_score": v5, "star_rating": star, "recommendation": rec,
            "trade_30d": trade_30,
            "trade_20d": trade_20,
            "trade_10d": trade_10,
            "trade_60d": trade_60,
            "trade_90d": trade_90,
        }
        results.append(result)

        # Print trade outcomes
        for label, trade in [("30d", trade_30), ("20d", trade_20), ("10d", trade_10)]:
            if trade and trade.get("buy_triggered"):
                print(f"    {label}: {trade['outcome']} return={trade['return_pct']:.1f}% max_gain={trade['max_gain']:.1f}% max_loss={trade['max_loss']:.1f}%")
            elif trade:
                print(f"    {label}: buy not triggered")

        time.sleep(0.1)

    # Step 4: Analyze results
    print(f"\n[4/4] Analyzing results...")
    print_analysis(results, hold_days)

    return results


def print_analysis(results, hold_days):
    """Comprehensive analysis of backtest results"""
    print("\n" + "=" * 70)
    print("=== V4 COMPREHENSIVE BACKTEST ANALYSIS ===")
    print("=" * 70)

    total = len(results)
    if total == 0:
        print("No results to analyze")
        return

    # Overall stats
    buy_triggered = 0
    sell_hit = 0
    stop_hit = 0
    hold_end = 0
    not_triggered = 0
    returns = []
    max_gains = []
    max_losses = []

    # By star rating
    by_star = defaultdict(lambda: {"count": 0, "buy_triggered": 0, "sell_hit": 0, "stop_hit": 0, "hold_end": 0, "returns": []})

    for r in results:
        trade = r.get("trade_30d")
        star = r.get("star_rating", 0)

        by_star[star]["count"] += 1

        if not trade:
            continue

        if not trade.get("buy_triggered"):
            not_triggered += 1
            continue

        buy_triggered += 1
        outcome = trade.get("outcome", "")
        ret = trade.get("return_pct", 0)
        returns.append(ret)
        max_gains.append(trade.get("max_gain", 0))
        max_losses.append(trade.get("max_loss", 0))

        by_star[star]["buy_triggered"] += 1

        if outcome == "sell_hit":
            sell_hit += 1
            by_star[star]["sell_hit"] += 1
        elif outcome == "stop_hit":
            stop_hit += 1
            by_star[star]["stop_hit"] += 1
        elif outcome == "hold_end":
            hold_end += 1
            by_star[star]["hold_end"] += 1

        by_star[star]["returns"].append(ret)

    # Print overall stats
    print(f"\n--- Overall Stats (total={total}) ---")
    print(f"  Buy triggered: {buy_triggered}/{total} ({buy_triggered/total*100:.1f}%)")
    print(f"  Buy NOT triggered: {not_triggered}/{total} ({not_triggered/total*100:.1f}%)")

    if buy_triggered > 0:
        print(f"\n--- Trade Outcomes (of {buy_triggered} triggered) ---")
        print(f"  Hit sell target: {sell_hit} ({sell_hit/buy_triggered*100:.1f}%)")
        print(f"  Hit stop loss:   {stop_hit} ({stop_hit/buy_triggered*100:.1f}%)")
        print(f"  Hold to end:     {hold_end} ({hold_end/buy_triggered*100:.1f}%)")

        if returns:
            avg_ret = np.mean(returns)
            med_ret = np.median(returns)
            win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
            avg_max_gain = np.mean(max_gains)
            avg_max_loss = np.mean(max_losses)

            print(f"\n--- Return Analysis ---")
            print(f"  Avg return: {avg_ret:.2f}%")
            print(f"  Median return: {med_ret:.2f}%")
            print(f"  Win rate: {win_rate:.1f}%")
            print(f"  Avg max gain: {avg_max_gain:.2f}%")
            print(f"  Avg max loss: {avg_max_loss:.2f}%")

            # Profit factor
            gross_profit = sum(r for r in returns if r > 0)
            gross_loss = abs(sum(r for r in returns if r < 0))
            pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
            print(f"  Profit factor: {pf:.2f}")

            # Compare by 20d and 10d hold
            print(f"\n--- Hold Period Comparison ---")
            for label, key in [("10d", "trade_10d"), ("20d", "trade_20d"), ("30d", "trade_30d"), ("60d", "trade_60d"), ("90d", "trade_90d")]:
                period_returns = []
                period_wins = 0
                period_total = 0
                for r in results:
                    trade = r.get(key)
                    if trade and trade.get("buy_triggered"):
                        period_total += 1
                        ret = trade.get("return_pct", 0)
                        period_returns.append(ret)
                        if ret > 0:
                            period_wins += 1
                if period_returns:
                    avg = np.mean(period_returns)
                    wr = period_wins / len(period_returns) * 100
                    print(f"  {label}: avg_return={avg:.2f}%, win_rate={wr:.1f}%, n={len(period_returns)}")

    # By star rating
    print(f"\n--- By Star Rating ---")
    for star in sorted(by_star.keys(), reverse=True):
        d = by_star[star]
        if d["buy_triggered"] == 0:
            print(f"  {star}-star: n={d['count']}, no buy triggered")
            continue
        avg_r = np.mean(d["returns"]) if d["returns"] else 0
        wr = sum(1 for r in d["returns"] if r > 0) / len(d["returns"]) * 100 if d["returns"] else 0
        sell_rate = d["sell_hit"] / d["buy_triggered"] * 100
        stop_rate = d["stop_hit"] / d["buy_triggered"] * 100
        print(f"  {star}-star: n={d['count']}, triggered={d['buy_triggered']}, sell={sell_rate:.0f}%, stop={stop_rate:.0f}%, avg_ret={avg_r:.1f}%, win={wr:.0f}%")

    # Diagnosis
    print(f"\n--- Diagnosis ---")
    if buy_triggered > 0:
        if sell_hit / buy_triggered > 0.6:
            print("  [!] Sell target too easy to hit - raise sell price")
        elif sell_hit / buy_triggered < 0.2:
            print("  [!] Sell target too hard to hit - lower sell price")
        else:
            print("  [OK] Sell target reasonable")

        if stop_hit / buy_triggered > 0.5:
            print("  [!] Stop loss triggered too often - widen stop or add buy confirmation")
        else:
            print("  [OK] Stop loss frequency reasonable")

        if returns:
            if np.mean(returns) < -3:
                print("  [!] Average return is negative - strategy needs improvement")
            elif np.mean(returns) < 0:
                print("  [!] Average return slightly negative - marginal improvement needed")
            else:
                print(f"  [OK] Average return positive ({np.mean(returns):.1f}%)")

            if win_rate < 40:
                print("  [!] Win rate too low - need better entry timing")
            else:
                print(f"  [OK] Win rate acceptable ({win_rate:.0f}%)")


if __name__ == "__main__":
    result = run_backtest(top_n=30, hold_days=30)
    if result:
        output_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "backtest_v4_result.json")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        # Convert numpy types for JSON serialization
        def convert(obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            return obj
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=convert)
        print(f"\nResult saved to: {output_path}")
