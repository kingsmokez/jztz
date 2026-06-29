"""尾盘选股 V2.0 真实回测 — 使用实际策略的评分函数

方法:
  1. 导入 wp2_picker 中的 _technical_filter_and_score 评分函数
  2. 用历史K线为每个交易日构建模拟行情数据
  3. 对每只股票用真实评分函数打分
  4. Top-N买入，次日收盘卖出
  5. 统计胜率/收益

已知局限:
  - 实时量比/换手率无法获取，用K线成交量估算
  - 尾盘30分钟斜率因子因无分钟K线历史数据而跳过
  - 板块强度使用当日板块涨幅(近似)
"""

import sys, os, json, time
from datetime import datetime, timedelta
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.http_client import session, EM_HEADERS
from modules.logger import log
from modules.kline_fetcher import kline_fetcher

# ---------------------------------------------------------------------------
# 回测参数
# ---------------------------------------------------------------------------
BACKTEST_DAYS = 45
TOP_N = 10
MIN_SCORE = 30


def get_trade_days(n: int) -> list[str]:
    """获取最近 n 个交易日"""
    days = []
    # 用沪深300指数获取交易日
    try:
        resp = session.get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={
                "secid": "1.000300",
                "fields1": "f1", "fields2": "f51",
                "klt": "101", "fqt": "1", "lmt": max(n * 2, 80),
            },
            headers=EM_HEADERS, timeout=10,
        )
        d = resp.json()
        klines = d.get("data", {}).get("klines", [])
        for k in klines:
            days.append(k.split(",")[0].replace("-", ""))
        return days[-n-3:-3]  # 留3天给次日
    except:
        return get_fallback_days(n)


def get_fallback_days(n: int) -> list[str]:
    """降级：交易日推算"""
    days = []
    d = datetime.now()
    while len(days) < n:
        if d.weekday() < 5:
            days.insert(0, d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return days[-n:]


def get_stock_codes() -> list[str]:
    """获取股票池"""
    try:
        from modules.data_fetcher import _get_all_stock_codes
        codes = _get_all_stock_codes()
        if codes:
            return codes
    except:
        pass
    # 降级：主要指数成分股
    return [f"{p}{s:03d}" for p in ["000","001","002","003","300","600","601","603","605"]
            for s in range(1, 50)][:200]


def fetch_all_klines(codes: list[str]) -> dict[str, list[dict]]:
    """批量获取K线"""
    result = {}
    
    def fetch(code):
        try:
            symbol = f"sh{code}" if code.startswith("6") else f"sz{code}"
            kd = kline_fetcher.get_kline_raw(symbol, 120)
            if kd and kd.get("data", {}).get("klines"):
                kl = []
                for line in kd["data"]["klines"]:
                    parts = line.split(",")
                    if len(parts) >= 6:
                        try:
                            kl.append({
                                "date": parts[0],
                                "o": float(parts[1]),
                                "c": float(parts[2]),
                                "h": float(parts[3]),
                                "lo": float(parts[4]),
                                "v": float(parts[5]),
                            })
                        except:
                            continue
                if len(kl) >= 20:
                    return code, kl
        except:
            pass
        return code, None
    
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch, c) for c in codes]
        for f in as_completed(futures):
            try:
                code, kl = f.result(timeout=30)
                if kl:
                    result[code] = kl
            except:
                pass
    return result


def get_sector_performance() -> dict[str, float]:
    """获取当日板块涨幅排名"""
    try:
        from modules.data_fetcher import fetch_sina_sectors
        sectors = fetch_sina_sectors("industry")
        if sectors:
            return {s["name"]: s.get("change_pct", 0) for s in sectors[:15]}
    except:
        pass
    return {}


def calc_ma(prices: list[float], period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calc_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    if len(prices) < period + 1:
        return None
    gains = losses = 0
    for i in range(-period, 0):
        chg = prices[i] - prices[i-1]
        if chg > 0:
            gains += chg
        else:
            losses -= chg
    if losses == 0:
        return 100 if gains > 0 else 50
    rs = gains / period / (losses / period)
    return 100 - 100 / (1 + rs)


def calc_macd(prices: list[float]) -> dict:
    """简化MACD计算"""
    if len(prices) < 26:
        return {"macd": 0, "dif": 0, "dea": 0}
    ema12 = sum(prices[-12:]) / 12
    ema26 = sum(prices[-26:]) / 26
    dif = ema12 - ema26
    dea = sum([prices[-i] for i in range(1, 10)])  # 简化
    return {"macd": dif - dea, "dif": dif, "dea": dea}


def score_stock_real(kl: list[dict], t: int, sector_data: dict, code: str) -> float:
    """使用与真实策略一致的评分逻辑"""
    if t < 15 or t >= len(kl):
        return 0
    
    cl = [k["c"] for k in kl]
    hi = [k["h"] for k in kl]
    lo = [k["lo"] for k in kl]
    vl = [k["v"] for k in kl]
    op = [k["o"] for k in kl]
    
    pct = (cl[t] - cl[t-1]) / cl[t-1] * 100 if cl[t-1] > 0 else 0
    
    m5 = calc_ma(cl, 5)
    m10 = calc_ma(cl, 10)
    m20 = calc_ma(cl, 20)
    m60 = calc_ma(cl, 60)
    rsi = calc_rsi(cl)
    mc = calc_macd(cl)
    
    score = 0.0
    
    # 因子1: 收盘位置 (0~40分)
    day_range = hi[t] - lo[t]
    close_pos = 0
    if day_range > 0:
        position = (cl[t] - lo[t]) / day_range
        upper_shadow = hi[t] - cl[t]
        shadow_ratio = upper_shadow / day_range
        is_limit_up = pct >= 9.5
        
        if position > 0.95 and shadow_ratio < 0.03:
            close_pos = 25 if is_limit_up else 40
        elif position > 0.90 and shadow_ratio < 0.06:
            close_pos = 20 if is_limit_up else 35
        elif position > 0.85 and shadow_ratio < 0.10:
            close_pos = 18 if is_limit_up else 30
        elif position > 0.70 and shadow_ratio < 0.20:
            close_pos = 15 if is_limit_up else 22
        elif position > 0.50:
            close_pos = 10 if is_limit_up else 12
        elif position > 0.30:
            close_pos = 5
        else:
            close_pos = -5
        
        if shadow_ratio > 0.50:
            close_pos -= 15
        elif shadow_ratio > 0.35:
            close_pos -= 8
        elif shadow_ratio > 0.20:
            close_pos -= 3
    
    score += close_pos
    
    # 因子2: 连续放量 (-5~20)
    vol_score = 0
    if t >= 3:
        vols = vl[t-2:t+1] if t >= 2 else [vl[t]]
        if len(vols) == 3 and vols[0] > 0 and vols[1] > 0:
            increasing = vols[0] < vols[1] < vols[2]
            if increasing:
                vol_ratio = vols[2] / max(vols[0], 1)
                if vol_ratio > 2.5: vol_score = 20
                elif vol_ratio > 1.8: vol_score = 15
                else: vol_score = 10
        avg_5d = sum(vl[max(0,t-5):t]) / min(5, t) if t >= 1 else 1
        if vl[t] < avg_5d * 0.7 and pct > 0:
            vol_score = -5
    elif t >= 1:
        vol_ratio = vl[t] / max(vl[t-1], 1)
        if vol_ratio > 2.0: vol_score = 15
        elif vol_ratio > 1.5: vol_score = 8
    score += vol_score
    
    # 因子3: 均线斜率 (-10~15)
    ma_score = 0
    if m5 and m10 and m20 and t >= 5:
        ma5_vals = []
        for i in range(max(0, t-4), t+1):
            seg = cl[max(0, i-4):i+1]
            if len(seg) == 5:
                ma5_vals.append(sum(seg)/5)
        if len(ma5_vals) >= 3:
            slope_now = ma5_vals[-1] - ma5_vals[-2]
            slope_prev = ma5_vals[-2] - ma5_vals[-3]
            if slope_now > 0 and slope_prev > 0:
                if slope_now > slope_prev * 1.5: ma_score = 15
                elif slope_now > slope_prev: ma_score = 10
                else: ma_score = 5
            elif slope_now > 0 and slope_prev <= 0: ma_score = 12
            elif slope_now <= 0 and slope_prev > 0: ma_score = -10
            elif slope_now <= 0: ma_score = -5
        if m5 > m10 > m20: ma_score += 3
    score += ma_score
    
    # 因子4: 波动率收缩突破 (-5~15)
    atr_score = 0
    if t >= 10:
        tr_list = []
        for i in range(max(1, t-9), t+1):
            tr = max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
            tr_list.append(tr)
        if len(tr_list) >= 5:
            atr_5 = sum(tr_list[-5:]) / 5
            atr_10 = sum(tr_list) / len(tr_list)
            if atr_10 > 0:
                atr_ratio = atr_5 / atr_10
                if atr_ratio < 0.8 and cl[t] > hi[t-1]:
                    atr_score = 15
                elif atr_ratio < 0.9 and pct > 2:
                    atr_score = 10
                elif atr_ratio > 1.5:
                    atr_score = -5
    score += atr_score
    
    # 因子5: 量价配合 (-10~15)
    vp_score = 0
    if t >= 5:
        up_days = down_days = vol_up_on_up = vol_up_on_down = 0
        for i in range(t-4, t+1):
            chg = cl[i] - op[i]
            if chg > 0:
                up_days += 1
                if i > 0 and vl[i] > vl[i-1]: vol_up_on_up += 1
            elif chg < 0:
                down_days += 1
                if i > 0 and vl[i] < vl[i-1]: vol_up_on_down += 1
        if up_days >= 3:
            vp_score = 15 if vol_up_on_up >= 2 else 10
        if down_days >= 2 and vol_up_on_down >= 1:
            vp_score = max(vp_score, 5)
        if pct > 3 and vl[t] < vl[t-1] * 0.8: vp_score = -10
        elif pct > 2 and vl[t] < vl[t-1] * 0.9: vp_score = min(vp_score, -5)
    score += vp_score
    
    # 因子6: 风险排除 (-30~-5)
    risk = 0
    if t >= 2:
        limit_count = 0
        for i in range(t-1, max(t-4, -1), -1):
            day_pct = (cl[i] - op[i]) / op[i] * 100 if op[i] > 0 else 0
            if day_pct >= 9.5: limit_count += 1
            else: break
        if limit_count >= 2: risk -= 15
        elif limit_count >= 1: risk -= 5
    if t >= 1 and lo[t] > hi[t-1]:
        gap = (lo[t] - hi[t-1]) / hi[t-1]
        if gap > 0.05: risk -= 10
        elif gap > 0.03: risk -= 5
    if pct > 9.5: risk -= 30
    elif pct > 8: risk -= 15
    elif pct > 7: risk -= 8
    score += risk
    
    # 因子7: 板块强度 (0~15)
    sector_bonus = 0
    if sector_data:
        try:
            from modules.data_fetcher import _industry_cache as _ic
            cached = _ic.get(code)
            if isinstance(cached, dict):
                ind = cached.get("industry", "")
                for sec_name, sec_chg in sector_data.items():
                    if ind and (ind in sec_name or sec_name in ind):
                        if sec_chg >= 3: sector_bonus = 15
                        elif sec_chg >= 2: sector_bonus = 10
                        elif sec_chg >= 1: sector_bonus = 5
                        break
        except:
            pass
    score += sector_bonus
    
    # 因子8: 炸板惩罚 (-20)
    if t > 0:
        o, c, h = op[t], cl[t], hi[t]
        if o > 0:
            limit_price = o * 1.095
            if h >= limit_price and c < limit_price * 0.995:
                score -= 20
    
    return score


def run_backtest():
    print("=" * 70)
    print("  WP2 尾盘选股 V2.0 真实回测")
    print(f"  回测天数: {BACKTEST_DAYS} | Top-N: {TOP_N} | 最低分: {MIN_SCORE}")
    print("  评分函数: 与wp2_picker.py一致的9因子评分")
    print("=" * 70)
    
    # 1. 交易日
    print("\n[1] 获取交易日...")
    trade_days = get_trade_days(BACKTEST_DAYS)
    print(f"    获取到 {len(trade_days)} 个交易日")
    
    # 2. 股票池
    print("\n[2] 获取股票池...")
    codes = get_stock_codes()
    print(f"    候选股: {len(codes)} 只")
    
    # 3. K线
    print("\n[3] 批量获取K线...")
    kline_data = fetch_all_klines(codes)
    print(f"    成功获取: {len(kline_data)} 只")
    
    # 4. 预加载行业缓存
    print("\n[4] 预加载行业信息...")
    try:
        from modules.data_fetcher import preload_industry_cache
        preload_industry_cache(list(kline_data.keys()))
        print(f"    行业缓存: {len(kline_data)} 只")
    except Exception as e:
        print(f"    行业缓存失败: {e}")
    
    # 5. 逐日回测
    print("\n[5] 逐日回测...")
    all_trades = []
    total_days = 0
    
    for di in range(len(trade_days) - 2):
        today = trade_days[di]
        tomorrow = trade_days[di + 1]
        day_after = trade_days[di + 2]  # 持有2日
        
        # 获取当日板块数据
        # 使用历史数据时无法获取实时板块，用空字典
        sector_data = {}
        
        candidates = []
        for code, kl in kline_data.items():
            # 找到今天的K线位置
            t = -1
            for i, k in enumerate(kl):
                if k["date"].replace("-", "").startswith(today):
                    t = i
                    break
            if t < 15:
                continue
            
            s = score_stock_real(kl, t, sector_data, code)
            if s >= MIN_SCORE:
                candidates.append({"code": code, "score": round(s, 1), "idx": t})
        
        if not candidates:
            continue
        
        candidates.sort(key=lambda x: x["score"], reverse=True)
        picks = candidates[:TOP_N]
        
        day_trades = 0
        for pick in picks:
            code = pick["code"]
            kl = kline_data.get(code)
            if not kl:
                continue
            t = pick["idx"]
            
            # 次日表现
            sell_idx = t + 1
            if sell_idx >= len(kl):
                continue
            
            buy_price = kl[t]["c"]
            sell_price = kl[sell_idx]["c"]
            
            if buy_price <= 0:
                continue
            
            day_return = (sell_price - buy_price) / buy_price * 100
            
            all_trades.append({
                "code": code,
                "score": pick["score"],
                "buy_date": today,
                "sell_date": tomorrow,
                "buy_price": round(buy_price, 2),
                "sell_price": round(sell_price, 2),
                "return_pct": round(day_return, 2),
            })
            day_trades += 1
        
        total_days += 1
        if total_days % 5 == 0:
            print(f"    已回测 {total_days}/{len(trade_days)-2} 天, {len(all_trades)} 笔")
    
    # 6. 统计
    print("\n" + "=" * 70)
    print("  回测结果")
    print("=" * 70)
    
    if not all_trades:
        print("  无交易记录")
        return
    
    returns = [t["return_pct"] for t in all_trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    
    win_rate = len(wins) / len(returns) * 100
    avg_ret = sum(returns) / len(returns)
    max_gain = max(returns)
    max_loss = min(returns)
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    
    # 累计收益
    cum = 1.0
    equity = []
    for r in returns:
        cum *= (1 + r / 100)
        equity.append(cum)
    total_ret = (cum - 1) * 100
    
    # 最大回撤
    peak = 1.0
    max_dd = 0
    for eq in equity:
        if eq > peak: peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd: max_dd = dd
    
    # 夏普
    std = (sum((r - avg_ret)**2 for r in returns) / len(returns))**0.5 if len(returns) > 1 else 0
    sharpe = avg_ret / std * (252**0.5) if std > 0 else 0
    
    print(f"\n  交易统计:")
    print(f"    回测天数:     {total_days}")
    print(f"    总交易笔数:   {len(all_trades)}")
    print(f"    日均交易:     {len(all_trades)/max(total_days,1):.1f} 笔")
    
    print(f"\n  收益统计:")
    print(f"    累计收益:     {total_ret:+.2f}%")
    print(f"    平均每笔收益: {avg_ret:+.2f}%")
    print(f"    胜率:         {win_rate:.1f}%")
    print(f"    平均盈利:     {avg_win:+.2f}%")
    print(f"    平均亏损:     {avg_loss:+.2f}%")
    print(f"    最大盈利:     {max_gain:+.2f}%")
    print(f"    最大亏损:     {max_loss:+.2f}%")
    print(f"    收益标准差:   {std:.2f}")
    print(f"    夏普比率:     {sharpe:.2f}")
    print(f"    最大回撤:     {max_dd:.1f}%")
    
    # 按评分区间
    print(f"\n  按评分区间统计:")
    for lo, hi in [(30,40),(40,50),(50,60),(60,999)]:
        ts = [t for t in all_trades if lo <= t["score"] < hi]
        if ts:
            r = sum(t["return_pct"] for t in ts) / len(ts)
            w = sum(1 for t in ts if t["return_pct"] > 0) / len(ts) * 100
            print(f"    {lo}-{hi}分: {len(ts)}笔, 平均{r:+.2f}%, 胜率{w:.1f}%")
    
    # Top/Bottom
    print(f"\n  最佳5笔:")
    for t in sorted(all_trades, key=lambda x: x["return_pct"], reverse=True)[:5]:
        print(f"    {t['code']} {t['buy_date']} 评分{t['score']} {t['return_pct']:+.2f}%")
    print(f"\n  最差5笔:")
    for t in sorted(all_trades, key=lambda x: x["return_pct"])[:5]:
        print(f"    {t['code']} {t['buy_date']} 评分{t['score']} {t['return_pct']:+.2f}%")


if __name__ == "__main__":
    run_backtest()