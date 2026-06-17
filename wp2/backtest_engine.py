"""
量价共振·尾盘选股 回测引擎 v2
==============================
使用真实历史数据进行策略回测

使用方法:
    python backtest_engine.py

依赖: pip install akshare pandas numpy
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import json
import sqlite3
import os
import warnings

warnings.filterwarnings('ignore')

# ==================== 配置 ====================

@dataclass
class BacktestConfig:
    """回测配置"""
    # 选股参数（与前端一致）
    min_cap: float = 30.0
    max_cap: float = 300.0
    min_amount: float = 3.0
    vol_multiplier: float = 1.5
    break_days: int = 20
    body_ratio: float = 0.6
    rsi_low: float = 50.0
    rsi_high: float = 75.0
    max_output: int = 4

    # 交易参数
    initial_capital: float = 100000.0
    position_size: float = 0.25
    stop_loss: float = -0.08
    stop_profit: float = 0.15
    max_hold_days: int = 20
    risk_per_trade: float = 0.02

    # 风控
    market_filter: bool = True  # 大盘风控
    market_ma_period: int = 20

    # 日期
    start_date: str = '20230101'
    end_date: str = '20251231'

    # 数据
    db_path: str = 'stock_history.db'
    sample_size: int = 200  # 回测股票数量（全市场太大，用样本）


# ==================== 技术指标计算 ====================

def calc_ma(series: pd.Series, period: int) -> pd.Series:
    """计算简单移动平均"""
    return series.rolling(window=period, min_periods=period).mean()


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """计算指数移动平均"""
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """计算RSI"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta).where(delta < 0, 0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100 - 100 / (1 + rs)
    return rsi


def calc_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """计算MACD"""
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    dif = ema_fast - ema_slow
    dea = calc_ema(dif, signal)
    macd = 2 * (dif - dea)
    return dif, dea, macd


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """计算ATR"""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=period).mean()
    return atr


# ==================== 数据获取 ====================

def init_database(db_path: str):
    """初始化数据库"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS kline (
            code TEXT, date TEXT, open REAL, high REAL,
            low REAL, close REAL, volume REAL, amount REAL,
            turnover REAL, circ_mv REAL,
            PRIMARY KEY (code, date)
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_date ON kline(date)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_code ON kline(code)')
    conn.commit()
    conn.close()


def fetch_stock_list() -> List[str]:
    """获取股票列表"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        # 过滤：剔除688、8开头、4开头
        df = df[~df['code'].str.startswith(('688', '8', '4'))]
        return df['code'].tolist()
    except Exception as e:
        print(f"获取股票列表失败: {e}")
        # 返回示例股票
        return [
            '000001', '000002', '000333', '000568', '000651',
            '000725', '000768', '000776', '000858', '002001',
            '002007', '002027', '002049', '002142', '002230',
            '002352', '002415', '002594', '300014', '300033',
            '300059', '300122', '300274', '300496', '300750',
            '600000', '600009', '600028', '600030', '600036',
            '600048', '600050', '600104', '600276', '600309',
            '600406', '600436', '600438', '600519', '600585',
            '600690', '600703', '600741', '600745', '600809',
            '601012', '601066', '601088', '601100', '601138',
            '601166', '601186', '601211', '601318', '601398',
            '601601', '601668', '601688', '601728', '601888',
            '601899', '601919', '601988', '603019', '603288',
            '603501', '603659', '603893', '605117', '688981'
        ]


def fetch_history_data(code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """获取单只股票历史数据"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start, end_date=end,
            adjust="qfq"
        )
        if df is None or df.empty:
            return None

        # 重命名列
        df = df.rename(columns={
            '日期': 'date', '开盘': 'open', '收盘': 'close',
            '最高': 'high', '最低': 'low', '成交量': 'volume',
            '成交额': 'amount', '振幅': 'amplitude',
            '涨跌幅': 'change_pct', '涨跌额': 'change',
            '换手率': 'turnover'
        })
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        return df
    except Exception as e:
        return None


def build_database(config: BacktestConfig, sample_codes: List[str] = None):
    """构建历史数据库"""
    print(f"正在构建数据库: {config.db_path}")
    init_database(config.db_path)

    if sample_codes is None:
        sample_codes = fetch_stock_list()
        if len(sample_codes) > config.sample_size:
            import random
            random.seed(42)
            sample_codes = random.sample(sample_codes, config.sample_size)

    print(f"获取 {len(sample_codes)} 只股票历史数据...")
    print(f"日期范围: {config.start_date} ~ {config.end_date}")

    conn = sqlite3.connect(config.db_path)
    total_rows = 0

    for i, code in enumerate(sample_codes):
        if i % 20 == 0:
            print(f"  进度: {i}/{len(sample_codes)}...")

        df = fetch_history_data(code, config.start_date, config.end_date)
        if df is None or len(df) < 60:
            continue

        # 添加技术指标
        df['ma5'] = calc_ma(df['close'], 5)
        df['ma10'] = calc_ma(df['close'], 10)
        df['ma20'] = calc_ma(df['close'], 20)
        df['ma60'] = calc_ma(df['close'], 60)
        df['rsi'] = calc_rsi(df['close'], 14)
        dif, dea, macd = calc_macd(df['close'])
        df['dif'] = dif
        df['dea'] = dea
        df['macd'] = macd
        df['atr'] = calc_atr(df['high'], df['low'], df['close'], 14)

        # 计算量比（5日均量）
        df['vol_ma5'] = calc_ma(df['volume'], 5)
        df['volume_ratio'] = df['volume'] / df['vol_ma5']

        # 计算N日高点
        df['high_20'] = df['high'].rolling(window=20, min_periods=1).max().shift(1)

        # 计算流通市值（简化：用收盘价 * 流通股本估算）
        # 实际应该用真实流通股本数据
        df['circ_mv'] = df['close'] * df['volume'] / (df['turnover'] / 100 + 0.001) / 1e8

        # 写入数据库
        for _, row in df.iterrows():
            conn.execute('''
                INSERT OR REPLACE INTO kline
                (code, date, open, high, low, close, volume, amount, turnover, circ_mv)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                code, row['date'].strftime('%Y-%m-%d'),
                row['open'], row['high'], row['low'], row['close'],
                row['volume'], row['amount'], row['turnover'],
                row['circ_mv']
            ))

        total_rows += len(df)

    conn.commit()
    conn.close()
    print(f"数据库构建完成，共 {total_rows} 条记录")


# ==================== 选股策略 ====================

def screen_stock(df: pd.DataFrame, idx: int, config: BacktestConfig) -> Optional[Dict]:
    """
    对单只股票进行五层过滤筛选
    df: 包含技术指标的DataFrame
    idx: 当前日期索引
    """
    if idx < 60:  # 需要至少60日数据
        return None

    row = df.iloc[idx]

    # 第1层: 基础过滤
    circ_mv = row.get('circ_mv', 0)
    if not (config.min_cap <= circ_mv <= config.max_cap):
        return None

    amount = row.get('amount', 0) / 1e8
    if amount < config.min_amount:
        return None

    # 第2层: 均线多头排列
    ma5 = row.get('ma5')
    ma10 = row.get('ma10')
    ma20 = row.get('ma20')
    ma60 = row.get('ma60')
    close = row['close']

    if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20) or pd.isna(ma60):
        return None

    if not (ma5 > ma10 > ma20 > ma60 and close > ma20):
        return None

    # 第3层: 量价突破
    vol_ratio = row.get('volume_ratio', 0)
    if pd.isna(vol_ratio) or vol_ratio < config.vol_multiplier:
        return None

    # 突破N日新高
    high_n = row.get('high_20', 0)
    if pd.isna(high_n) or close <= high_n:
        return None

    # 实体占比
    o, h, l, c = row['open'], row['high'], row['low'], row['close']
    body = abs(c - o)
    range_total = h - l
    if range_total <= 0 or body / range_total < config.body_ratio:
        return None

    # 第4层: RSI安全区
    rsi = row.get('rsi')
    if pd.isna(rsi) or not (config.rsi_low < rsi < config.rsi_high):
        return None

    # 第5层: MACD确认
    dif = row.get('dif')
    dea = row.get('dea')
    macd = row.get('macd')
    if pd.isna(dif) or pd.isna(dea) or pd.isna(macd):
        return None
    if not (dif > dea and macd > 0):
        return None

    # 计算评分
    score = calc_score(vol_ratio, row.get('change_pct', 0), rsi, circ_mv)

    return {
        'code': row.get('code', ''),
        'date': row['date'],
        'price': c,
        'change_pct': row.get('change_pct', 0),
        'amount': amount,
        'cap': circ_mv,
        'vol_ratio': vol_ratio,
        'turnover': row.get('turnover', 0),
        'ma_status': f"{ma5:.1f}>{ma10:.1f}",
        'rsi': rsi,
        'macd': macd,
        'atr': row.get('atr', c * 0.02),
        'score': score
    }


def calc_score(vol_ratio, change_pct, rsi, cap):
    """计算股票评分"""
    score = 0

    # 量比得分 (30分)
    if vol_ratio >= 2.5:
        score += 30
    elif vol_ratio >= 2.0:
        score += 25
    elif vol_ratio >= 1.5:
        score += 18
    else:
        score += 10

    # 涨幅得分 (25分)
    ch = change_pct or 0
    if 3 <= ch <= 6:
        score += 25
    elif ch >= 2:
        score += 20
    elif ch >= 1:
        score += 15
    else:
        score += 5

    # RSI得分 (25分)
    if 55 <= rsi <= 65:
        score += 25
    elif 50 <= rsi <= 70:
        score += 20
    else:
        score += 10

    # 市值得分 (20分)
    if 50 <= cap <= 200:
        score += 20
    elif 30 <= cap <= 300:
        score += 15
    else:
        score += 8

    return min(score, 100)


# ==================== 回测引擎 ====================

@dataclass
class Trade:
    """交易记录"""
    code: str
    name: str = ''
    buy_date: str = ''
    buy_price: float = 0.0
    sell_date: str = ''
    sell_price: float = 0.0
    hold_days: int = 0
    pnl_pct: float = 0.0
    exit_reason: str = ''
    shares: int = 0


@dataclass
class BacktestResult:
    """回测结果"""
    trades: List[Trade] = field(default_factory=list)
    daily_nav: List[Dict] = field(default_factory=list)
    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_trade_return: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0


def run_backtest(config: BacktestConfig) -> BacktestResult:
    """运行回测"""
    result = BacktestResult()

    print(f"\n{'='*60}")
    print(f"量价共振·尾盘选股 策略回测")
    print(f"{'='*60}")
    print(f"回测区间: {config.start_date} ~ {config.end_date}")
    print(f"初始资金: {config.initial_capital:,.0f}")
    print(f"止损/止盈: {config.stop_loss*100:.0f}% / {config.stop_profit*100:.0f}%")
    print(f"最大持仓天数: {config.max_hold_days}")
    print(f"{'='*60}\n")

    # 检查数据库
    if not os.path.exists(config.db_path):
        print("数据库不存在，开始构建...")
        build_database(config)

    conn = sqlite3.connect(config.db_path)

    # 获取所有股票代码
    codes = pd.read_sql("SELECT DISTINCT code FROM kline", conn)['code'].tolist()
    print(f"回测股票数: {len(codes)}只")

    # 获取所有交易日
    dates = pd.read_sql(
        "SELECT DISTINCT date FROM kline ORDER BY date",
        conn
    )['date'].tolist()
    print(f"回测交易日: {len(dates)}天\n")

    # 逐日回测
    capital = config.initial_capital
    positions: Dict[str, Dict] = {}  # 当前持仓
    daily_nav = []
    trades = []

    for date_idx, date in enumerate(dates):
        if date_idx % 50 == 0:
            print(f"  回测进度: {date_idx}/{len(dates)} ({date})...")

        # 1. 检查持仓是否需要平仓
        to_sell = []
        for code, pos in positions.items():
            # 获取当日价格
            df_price = pd.read_sql(
                f"SELECT * FROM kline WHERE code='{code}' AND date='{date}'",
                conn
            )
            if df_price.empty:
                continue

            row = df_price.iloc[0]
            current_price = row['close']
            hold_days = (datetime.strptime(date, '%Y-%m-%d') -
                          datetime.strptime(pos['buy_date'], '%Y-%m-%d')).days

            pnl_pct = (current_price - pos['buy_price']) / pos['buy_price']

            # 检查止损
            should_sell = False
            reason = ''

            if pnl_pct <= config.stop_loss:
                should_sell = True
                reason = 'stop_loss'
            elif pnl_pct >= config.stop_profit:
                should_sell = True
                reason = 'take_profit'
            elif hold_days >= config.max_hold_days:
                should_sell = True
                reason = 'time_exit'

            if should_sell:
                to_sell.append((code, current_price, reason, hold_days))

        # 执行卖出
        for code, sell_price, reason, hold_days in to_sell:
            pos = positions[code]
            pnl_pct = (sell_price - pos['buy_price']) / pos['buy_price']
            capital += pos['shares'] * sell_price

            trades.append(Trade(
                code=code,
                buy_date=pos['buy_date'],
                buy_price=pos['buy_price'],
                sell_date=date,
                sell_price=sell_price,
                hold_days=hold_days,
                pnl_pct=pnl_pct,
                exit_reason=reason,
                shares=pos['shares']
            ))
            del positions[code]

        # 2. 选股（每隔5个交易日）
        if date_idx % 5 != 0:
            # 记录净值
            portfolio_value = capital
            for code, pos in positions.items():
                df_price = pd.read_sql(
                    f"SELECT close FROM kline WHERE code='{code}' AND date='{date}'",
                    conn
                )
                if not df_price.empty:
                    portfolio_value += pos['shares'] * df_price.iloc[0]['close']

            daily_nav.append({'date': date, 'nav': portfolio_value})
            continue

        # 大盘风控
        if config.market_filter:
            df_index = pd.read_sql(
                f"SELECT * FROM kline WHERE code='000300' AND date='{date}'",
                conn
            )
            if not df_index.empty:
                idx_row = df_index.iloc[0]
                # 简化为检查当日跌幅
                if idx_row.get('change_pct', 0) < -2:
                    daily_nav.append({'date': date, 'nav': capital + sum(
                        positions[c]['shares'] * pd.read_sql(
                            f"SELECT close FROM kline WHERE code='{c}' AND date='{date}'",
                            conn
                        ).iloc[0]['close'] if not pd.read_sql(
                            f"SELECT close FROM kline WHERE code='{c}' AND date='{date}'",
                            conn
                        ).empty else 0 for c in positions
                    )})
                    continue

        # 选股
        signals = []
        for code in codes:
            # 获取股票历史数据（当前日期及之前）
            df_stock = pd.read_sql(
                f"SELECT * FROM kline WHERE code='{code}' AND date <= '{date}' ORDER BY date",
                conn
            )
            if len(df_stock) < 60:
                continue

            # 重新计算技术指标（因为数据库中可能没有预计算）
            df_stock['ma5'] = calc_ma(df_stock['close'], 5)
            df_stock['ma10'] = calc_ma(df_stock['close'], 10)
            df_stock['ma20'] = calc_ma(df_stock['close'], 20)
            df_stock['ma60'] = calc_ma(df_stock['close'], 60)
            df_stock['rsi'] = calc_rsi(df_stock['close'], 14)
            dif, dea, macd = calc_macd(df_stock['close'])
            df_stock['dif'] = dif
            df_stock['dea'] = dea
            df_stock['macd'] = macd
            df_stock['vol_ma5'] = calc_ma(df_stock['volume'], 5)
            df_stock['volume_ratio'] = df_stock['volume'] / df_stock['vol_ma5']
            df_stock['high_20'] = df_stock['high'].rolling(window=20, min_periods=1).max().shift(1)
            df_stock['circ_mv'] = df_stock['close'] * df_stock['volume'] / (df_stock['turnover'] / 100 + 0.001) / 1e8

            signal = screen_stock(df_stock, len(df_stock) - 1, config)
            if signal:
                signal['code'] = code
                signals.append(signal)

        # 按评分排序，取前max_output只
        signals.sort(key=lambda x: x['score'], reverse=True)
        selected = signals[:config.max_output]

        # 买入
        for sig in selected:
            if capital <= 0:
                break

            # 仓位管理：单只不超过总资金的25%
            max_invest = capital * config.position_size
            shares = int(max_invest / sig['price'])
            if shares <= 0:
                continue

            cost = shares * sig['price']
            if cost > capital:
                shares = int(capital / sig['price'])
                cost = shares * sig['price']

            capital -= cost
            positions[sig['code']] = {
                'code': sig['code'],
                'buy_price': sig['price'],
                'buy_date': date,
                'shares': shares,
                'amount': cost
            }

        # 记录净值
        portfolio_value = capital
        for code, pos in positions.items():
            df_price = pd.read_sql(
                f"SELECT close FROM kline WHERE code='{code}' AND date='{date}'",
                conn
            )
            if not df_price.empty:
                portfolio_value += pos['shares'] * df_price.iloc[0]['close']

        daily_nav.append({'date': date, 'nav': portfolio_value})

    conn.close()

    # 计算回测指标
    result.trades = trades
    result.daily_nav = daily_nav
    result.total_trades = len(trades)

    if trades:
        returns = [t.pnl_pct for t in trades]
        result.winning_trades = sum(1 for r in returns if r > 0)
        result.losing_trades = sum(1 for r in returns if r <= 0)
        result.win_rate = result.winning_trades / len(trades) if trades else 0

        wins = [r for r in returns if r > 0]
        losses = [abs(r) for r in returns if r <= 0]
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 1
        result.profit_factor = avg_win / avg_loss if avg_loss > 0 else float('inf')
        result.avg_trade_return = np.mean(returns)

    if daily_nav:
        nav_values = [d['nav'] for d in daily_nav]
        result.total_return = (nav_values[-1] - config.initial_capital) / config.initial_capital

        # 年化收益
        days = len(daily_nav)
        if days > 1:
            result.annual_return = (nav_values[-1] / nav_values[0]) ** (252 / days) - 1

        # 最大回撤
        peak = nav_values[0]
        max_dd = 0
        for v in nav_values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown = max_dd

        # 夏普比率
        if len(nav_values) > 1:
            daily_returns = [(nav_values[i] - nav_values[i-1]) / nav_values[i-1]
                           for i in range(1, len(nav_values))]
            if daily_returns and np.std(daily_returns) > 0:
                result.sharpe_ratio = (np.mean(daily_returns) / np.std(daily_returns)) * np.sqrt(252)

    return result


def print_results(result: BacktestResult, config: BacktestConfig):
    """打印回测结果"""
    print(f"\n{'='*60}")
    print(f"回测结果汇总")
    print(f"{'='*60}")
    print(f"总交易次数:     {result.total_trades}")
    print(f"盈利次数:       {result.winning_trades}")
    print(f"亏损次数:       {result.losing_trades}")
    print(f"胜率:           {result.win_rate*100:.1f}%")
    print(f"盈亏比:         {result.profit_factor:.2f}")
    print(f"平均单笔收益:   {result.avg_trade_return*100:.2f}%")
    print(f"总收益率:       {result.total_return*100:.2f}%")
    print(f"年化收益率:     {result.annual_return*100:.2f}%")
    print(f"最大回撤:       {result.max_drawdown*100:.2f}%")
    print(f"夏普比率:       {result.sharpe_ratio:.2f}")
    print(f"{'='*60}\n")

    if result.trades:
        print("最近20笔交易:")
        for t in result.trades[-20:]:
            print(f"  {t.code} | 买入: {t.buy_price:.2f} | 卖出: {t.sell_price:.2f} | "
                  f"收益: {t.pnl_pct*100:+.2f}% | {t.exit_reason} | {t.hold_days}天")

    # 保存结果到文件
    output = {
        'config': {
            'start_date': config.start_date,
            'end_date': config.end_date,
            'initial_capital': config.initial_capital,
            'stop_loss': config.stop_loss,
            'stop_profit': config.stop_profit,
            'max_hold_days': config.max_hold_days,
        },
        'metrics': {
            'total_trades': result.total_trades,
            'winning_trades': result.winning_trades,
            'losing_trades': result.losing_trades,
            'win_rate': round(result.win_rate * 100, 1),
            'profit_factor': round(result.profit_factor, 2),
            'avg_trade_return': round(result.avg_trade_return * 100, 2),
            'total_return': round(result.total_return * 100, 2),
            'annual_return': round(result.annual_return * 100, 2),
            'max_drawdown': round(result.max_drawdown * 100, 2),
            'sharpe_ratio': round(result.sharpe_ratio, 2),
        },
        'trades': [
            {
                'code': t.code,
                'buy_date': t.buy_date,
                'buy_price': t.buy_price,
                'sell_date': t.sell_date,
                'sell_price': t.sell_price,
                'pnl_pct': round(t.pnl_pct * 100, 2),
                'hold_days': t.hold_days,
                'exit_reason': t.exit_reason,
            }
            for t in result.trades
        ],
        'daily_nav': result.daily_nav
    }

    with open('backtest_result.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存到: backtest_result.json")


# ==================== 主程序 ====================

if __name__ == '__main__':
    config = BacktestConfig()

    # 检查是否需要构建数据库
    if not os.path.exists(config.db_path):
        print("首次运行，需要构建历史数据库...")
        print("这将获取股票历史数据，可能需要几分钟...")
        build_database(config)

    # 运行回测
    result = run_backtest(config)
    print_results(result, config)
