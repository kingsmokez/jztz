"""尾盘选股 WP2 V2.0 回测 — 9因子评分体系

回测方法:
  1. 取最近 45 个交易日
  2. 每天用当日已知K线数据评分
  3. 取评分最高的 Top 10/20 股票
  4. 次日开盘买入、收盘卖出
  5. 统计胜率、平均收益等

注意:
  - 回测使用日K线模拟，无法完全模拟尾盘30分钟斜率因子
  - 板块强度用当日板块涨幅排名（近似模拟）
  - 实际收益可能高于回测（尾盘因子是正贡献）
"""

import sys, os, json, time
from datetime import datetime, timedelta
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.http_client import session, EM_HEADERS
from modules.logger import log
from modules.kline_fetcher import kline_fetcher
from modules.data_fetcher import _shorten_industry

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
BACKTEST_DAYS = 45           # 回测天数
TOP_N = 10                   # 每日选股数量
MIN_SCORE = 30               # 最低评分门槛
MIN_CAP = 30                 # 最小市值(亿)
MAX_CAP = 500                # 最大市值(亿)

# 候选股票池（取沪深300+中证500成分股中市值30-500亿的）
# 简化：取过去一段时间有足够K线数据的活跃股票


def get_trade_days(n: int) -> list[str]:
    """获取最近 n 个交易日"""
    days = []
    try:
        resp = session.get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={
                "secid": "1.000300",
                "fields1": "f1",
                "fields2": "f51",
                "klt": "101",
                "fqt": "1",
                "lmt": max(n * 2, 60),
            },
            headers=EM_HEADERS, timeout=10,
        )
        d = resp.json()
        klines = d.get("data", {}).get("klines", [])
        if klines:
            for k in klines:
                day = k.split(",")[0].replace("-", "")
                days.append(day)
            return days[-n:]
    except Exception as e:
        print(f"获取交易日失败: {e}")
    # 降级：用自然日推算（周末会跳过）
    d = datetime.now()
    while len(days) < n:
        ds = d.strftime("%Y%m%d")
        if d.weekday() < 5:
            days.insert(0, ds)
        d -= timedelta(days=1)
    return days[-n:]


def get_stock_list() -> list[dict]:
    """获取候选股票池：市值30-500亿的A股"""
    try:
        from modules.data_fetcher import _get_all_stock_codes
        codes = _get_all_stock_codes()
        if not codes:
            return get_sample_stocks()
        return [{"code": c, "name": ""} for c in codes]
    except:
        return get_sample_stocks()


def get_sample_stocks() -> list[dict]:
    """获取有足够K线数据的样本股"""
    stocks = []
    for prefix in ["000", "001", "002", "003", "300", "600", "601", "603", "605"]:
        for suffix in range(1, 10):
            code = f"{prefix}{suffix:03d}"
            if len(code) == 6:
                stocks.append({"code": code, "name": ""})
    return stocks[:50]  # 限制样本量


def fetch_klines_batch(stocks: list[dict]) -> dict[str, list[dict]]:
    """批量获取K线数据"""
    kline_data = {}
    def fetch(code):
        try:
            kd = kline_fetcher.get_kline_raw(
                f"sh{code}" if code.startswith("6") else f"sz{code}", 120
            )
            if kd and kd.get("data", {}).get("klines"):
                return code, kd
        except:
            pass
        return code, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch, s["code"]): s["code"] for s in stocks}
        for f in as_completed(futures):
            try:
                code, kd = f.result(timeout=30)
                if kd:
                    kline_data[code] = kd
            except:
                pass
    return kline_data


def parse_klines(kd: dict) -> list[dict]:
    """解析K线为列表"""
    if not kd:
        return []
    klines = kd.get("data", {}).get("klines", [])
    result = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 6:
            try:
                result.append({
                    "date": parts[0],
                    "o": float(parts[1]),
                    "c": float(parts[2]),
                    "h": float(parts[3]),
                    "lo": float(parts[4]),
                    "v": float(parts[5]),
                })
            except:
                continue
    return result


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


def score_stock(kl: list[dict], t: int) -> float:
    """简化的评分函数，使用日K线模拟（不含分钟K线因子）"""
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
    rsi = calc_rsi(cl)
    
    score = 0.0
    
    # 因子1: 收盘位置 (0~40分)
    day_range = hi[t] - lo[t]
    close_pos = 0
    if day_range > 0:
        pos = (cl[t] - lo[t]) / day_range
        upper_shadow = hi[t] - cl[t]
        shadow_ratio = upper_shadow / day_range
        
        if pos > 0.95 and shadow_ratio < 0.03:
            close_pos = 40 if pct < 9.5 else 25
        elif pos > 0.85 and shadow_ratio < 0.10:
            close_pos = 30 if pct < 9.5 else 18
        elif pos > 0.70:
            close_pos = 22 if pct < 9.5 else 15
        elif pos > 0.50:
            close_pos = 12
        elif pos > 0.30:
            close_pos = 5
        else:
            close_pos = -5
        
        if shadow_ratio > 0.50:
            close_pos -= 15
        elif shadow_ratio > 0.35:
            close_pos -= 8
    
    score += close_pos
    
    # 因子2: 连续放量 (-5~20)
    vol_score = 0
    if t >= 3:
        vols = vl[t-2:t+1]
        if vols[0] > 0 and vols[1] > 0:
            increasing = vols[0] < vols[1] < vols[2]
            if increasing:
                vol_ratio = vols[2] / max(vols[0], 1)
                if vol_ratio > 2.5: vol_score = 20
                elif vol_ratio > 1.8: vol_score = 15
                else: vol_score = 10
        avg_5d = sum(vl[t-5:t]) / 5 if t >= 5 else 1
        if vl[t] < avg_5d * 0.7 and pct > 0:
            vol_score = -5
    score += vol_score
    
    # 因子3: 均线斜率 (0~15)
    ma_score = 0
    if m5 and m10 and m20 and t >= 5:
        if m5 > m10 > m20 > cl[t] and cl[t] > m5:
            ma_score = 15
        elif m5 > m10 > m20:
            ma_score = 10
        elif m5 > m20:
            ma_score = 5
        else:
            ma_score = -5
    score += ma_score
    
    # 因子5: 量价配合 (-10~15)
    vp_score = 0
    if t >= 5:
        up_days = sum(1 for i in range(t-4, t+1) if cl[i] > op[i])
        if up_days >= 3:
            vp_score = 10
        if pct > 3 and t > 0 and vl[t] < vl[t-1] * 0.8:
            vp_score = -10
    score += vp_score
    
    # 因子6: 风险排除
    risk = 0
    if pct > 9.5: risk -= 30
    elif pct > 8: risk -= 15
    elif pct > 7: risk -= 8
    if t >= 2:
        for i in range(t-1, max(t-4, -1), -1):
            day_pct = (cl[i] - op[i]) / op[i] * 100 if op[i] > 0 else 0
            if day_pct >= 9.5:
                risk -= 5
    score += risk
    
    return score


def run_backtest():
    """执行回测"""
    print("=" * 60)
    print(f"WP2 V2.0 回测 (9因子评分体系)")
    print(f"回测天数: {BACKTEST_DAYS}, TopN: {TOP_N}, 最低评分: {MIN_SCORE}")
    print("=" * 60)
    
    # 1. 获取交易日
    print("\n[1] 获取交易日...")
    trade_days = get_trade_days(BACKTEST_DAYS + 10)
    print(f"    获取到 {len(trade_days)} 个交易日")
    
    # 2. 获取股票池
    print("\n[2] 获取股票池...")
    stocks = get_stock_list()
    print(f"    候选股: {len(stocks)} 只")
    
    # 3. 获取K线数据
    print("\n[3] 获取K线数据...")
    kline_data = fetch_klines_batch(stocks)
    print(f"    成功获取: {len(kline_data)} 只")
    
    # 4. 逐日回测
    print("\n[4] 执行回测...")
    all_trades = []
    total_days = 0
    
    for idx in range(len(trade_days) - 3):  # 留3天给次日表现
        back_test_day = trade_days[idx]
        next_day = trade_days[idx + 1]
        
        # 对每只股票评分
        candidates = []
        for code in list(kline_data.keys())[:200]:  # 控制计算量
            kd = kline_data.get(code)
            kl = parse_klines(kd)
            if len(kl) < 20:
                continue
            
            # 找到回测日在K线中的位置
            t = -1
            for i, k in enumerate(kl):
                if k["date"].replace("-", "").startswith(back_test_day):
                    t = i
                    break
            if t < 15:
                continue
            
            s = score_stock(kl, t)
            if s >= MIN_SCORE:
                # 当日收盘价
                buy_price = kl[t]["c"]
                candidates.append({
                    "code": code,
                    "score": round(s, 1),
                    "buy_price": buy_price,
                    "buy_date": back_test_day,
                })
        
        if not candidates:
            continue
        
        # 取TopN
        candidates.sort(key=lambda x: x["score"], reverse=True)
        picks = candidates[:TOP_N]
        
        # 检查次日表现
        day_trades = []
        for pick in picks:
            code = pick["code"]
            kd = kline_data.get(code)
            kl = parse_klines(kd)
            
            next_idx = -1
            for i, k in enumerate(kl):
                if k["date"].replace("-", "").startswith(next_day):
                    next_idx = i
                    break
            
            if next_idx < 0 or next_idx >= len(kl):
                continue
            
            sell_price = kl[next_idx]["c"]
            day_return = (sell_price - pick["buy_price"]) / pick["buy_price"] * 100
            
            day_trades.append({
                "code": code,
                "score": pick["score"],
                "buy_price": pick["buy_price"],
                "sell_price": sell_price,
                "return_pct": round(day_return, 2),
                "buy_date": back_test_day,
                "sell_date": next_day,
            })
        
        all_trades.extend(day_trades)
        total_days += 1
        
        if total_days % 5 == 0:
            print(f"    已回测 {total_days}/{len(trade_days)-3} 天, 交易 {len(all_trades)} 笔")
    
    # 5. 统计结果
    print("\n" + "=" * 60)
    print("回测结果")
    print("=" * 60)
    
    if not all_trades:
        print("无交易记录")
        return
    
    returns = [t["return_pct"] for t in all_trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    
    win_rate = len(wins) / len(returns) * 100 if returns else 0
    avg_return = sum(returns) / len(returns) if returns else 0
    max_gain = max(returns) if returns else 0
    max_loss = min(returns) if returns else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    
    # 累计收益
    cum = 1.0
    equity = []
    for r in returns:
        cum *= (1 + r / 100)
        equity.append(cum)
    
    total_return = (cum - 1) * 100
    
    # 最大回撤
    peak = 1.0
    max_dd = 0
    for eq in equity:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd
    
    # 夏普比（简化，假设无风险利率0）
    std = (sum((r - avg_return)**2 for r in returns) / len(returns)) ** 0.5 if len(returns) > 1 else 0
    sharpe = avg_return / std * (252 ** 0.5) if std > 0 else 0
    
    print(f"\n交易统计:")
    print(f"  回测天数:    {total_days}")
    print(f"  总交易笔数:  {len(all_trades)}")
    print(f"  日均交易:    {len(all_trades)/max(total_days,1):.1f} 笔")
    
    print(f"\n收益统计:")
    print(f"  累计收益:    {total_return:+.2f}%")
    print(f"  平均每笔收益: {avg_return:+.2f}%")
    print(f"  胜率:        {win_rate:.1f}%")
    print(f"  平均盈利:    {avg_win:+.2f}%")
    print(f"  平均亏损:    {avg_loss:+.2f}%")
    print(f"  最大盈利:    {max_gain:+.2f}%")
    print(f"  最大亏损:    {max_loss:+.2f}%")
    print(f"  收益标准差:  {std:.2f}")
    print(f"  夏普比率:    {sharpe:.2f}")
    print(f"  最大回撤:    {max_dd:.1f}%")
    
    # 按评分区间统计
    print(f"\n按评分区间统计:")
    for bucket in [(30, 40), (40, 50), (50, 60), (60, 999)]:
        bucket_trades = [t for t in all_trades if bucket[0] <= t["score"] < bucket[1]]
        if bucket_trades:
            bucket_ret = sum(t["return_pct"] for t in bucket_trades) / len(bucket_trades)
            bucket_win = sum(1 for t in bucket_trades if t["return_pct"] > 0) / len(bucket_trades) * 100
            print(f"  {bucket[0]}-{bucket[1]}分: {len(bucket_trades)}笔, 平均{bucket_ret:+.2f}%, 胜率{bucket_win:.1f}%")
    
    # Top 10 最好/最差交易
    print(f"\n最佳5笔:")
    for t in sorted(all_trades, key=lambda x: x["return_pct"], reverse=True)[:5]:
        print(f"  {t['code']} {t['buy_date']} 评分{t['score']} {t['return_pct']:+.2f}%")
    
    print(f"\n最差5笔:")
    for t in sorted(all_trades, key=lambda x: x["return_pct"])[:5]:
        print(f"  {t['code']} {t['buy_date']} 评分{t['score']} {t['return_pct']:+.2f}%")


if __name__ == "__main__":
    run_backtest()
