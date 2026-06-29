"""竞价选股回填+回测一体化脚本

由于历史竞价快照无法获取，使用日线数据模拟：
- 用历史日线的 open 作为当天竞价后开盘价
- preclose 计算 gap_pct
- 当日 volume / 前5日均量 计算 volume_ratio
- 当日成交额 / 流通市值 计算 turnover_ratio
- 重跑 V2 选股逻辑
- 对比选出股票后续 1/3/5 日涨跌，计算胜率

用法:
    python scripts/auction_backtest.py --days 30 --top-n 5
    python scripts/auction_backtest.py --days 60 --top-n 5 --hold 3

输出:
    1. 回填结果追加到 auction_history.jsonl (source=backtest)
    2. 控制台打印胜率/平均涨幅/最大回撤等指标
    3. 每只选股的详细 1/3/5 日表现
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

# 让脚本能从项目根目录导入
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from modules.kline_fetcher import KlineFetcher
from modules.data_fetcher import get_stock_industry
from modules.logger import log
from routes.auction import (
    _calculate_score_v2,
    _build_buy_advice,
    _should_exclude_v2,
    _append_auction_history,
    AUCTION_HISTORY_FILE,
)


# 简化的沪深300成分/代用代码 — 用于大盘状态
CSI300_CODE = "000300"
# 全市场股票代码获取：用新浪接口拉一遍（脚本里为了简单，用本地缓存的代码列表）
def get_all_stock_codes() -> list[str]:
    """从东财接口拉全市场 A 股代码列表"""
    import requests
    try:
        # 东财沪深A股列表
        url = "http://80.push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": 6000, "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f3",
            "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
            "fields": "f12,f13",  # code, market
        }
        resp = requests.get(url, params=params, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0"})
        data = resp.json().get("data", {}).get("diff", [])
        codes = []
        for item in data:
            code = item.get("f12", "")
            market = item.get("f13", 0)
            if not code:
                continue
            # 转换为6位纯数字代码
            codes.append(code)
        log.info(f"获取全市场股票代码: {len(codes)}只")
        return codes
    except Exception as e:
        log.error(f"获取股票代码列表失败: {e}")
        return []


def get_index_daily(code: str, days: int = 80) -> list[dict]:
    """获取指数日线数据（用东财接口）

    Args:
        code: 指数代码，如 "000300" (沪深300)
        days: 需要的交易日数
    """
    import requests
    try:
        # 东财 secid: 1.000xxx=沪, 0.399xxx=深
        if code.startswith("000"):
            secid = f"1.{code}"
        elif code.startswith("399"):
            secid = f"0.{code}"
        else:
            secid = f"1.{code}"

        url = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "klt": 101,  # 日K
            "fqt": 1,    # 前复权
            "end": "20500101",
            "lmt": days + 30,
        }
        resp = requests.get(url, params=params, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0"})
        klines_data = resp.json().get("data", {}).get("klines", [])
        result = []
        for row in klines_data:
            parts = row.split(",")
            if len(parts) >= 7:
                result.append({
                    "date": parts[0],
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                    "amount": float(parts[6]) / 1e8,  # 转亿元
                })
        return result
    except Exception as e:
        log.error(f"获取指数 {code} 数据失败: {e}")
        return []


def get_stock_daily(code: str, days: int = 80) -> list[dict]:
    """获取股票日线数据"""
    fetcher = KlineFetcher()
    return fetcher.get_kline(code, count=days + 30) or []


def compute_auction_features(klines: list[dict], idx: int) -> Optional[dict]:
    """从日线数据模拟计算当天的竞价特征

    Args:
        klines: 日K线列表（按时间正序）
        idx: 当天在 klines 中的索引

    Returns:
        {
            "open": float, "preclose": float, "gap_pct": float,
            "volume_ratio": float, "turnover_ratio": float, "amount": float,
            "kline_summary": {...},
        }
    """
    if idx < 5 or idx >= len(klines):
        return None

    today = klines[idx]
    prev = klines[idx - 1]

    open_price = today.get("open", 0)
    preclose = prev.get("close", 0)
    if open_price <= 0 or preclose <= 0:
        return None

    gap_pct = open_price / preclose - 1

    # 量比 = 当日成交量 / 前5日平均成交量
    today_vol = today.get("volume", 0)
    avg_5d_vol = sum(klines[i].get("volume", 0) for i in range(idx - 5, idx)) / 5
    volume_ratio = today_vol / avg_5d_vol if avg_5d_vol > 0 else 0

    # 换手率估算（无流通市值数据，用成交额/总市值近似不可行）
    # 这里用 amount（成交额，亿元）作为代理
    amount = today.get("amount", 0) or (today.get("volume", 0) * today.get("close", 0) / 1e8)
    # turnover_ratio 无法精确计算，置为0（评分公式会容错）
    turnover_ratio = 0

    # K线摘要（用前5日数据）
    recent_5 = klines[max(0, idx - 5):idx]
    recent_closes = [k.get("close", 0) for k in recent_5]
    recent_highs = [k.get("high", 0) for k in recent_5]
    recent_lows = [k.get("low", 0) for k in recent_5]
    if not recent_closes or recent_closes[-1] <= 0:
        return None

    cum_gain_5d = (recent_closes[-1] / recent_closes[0] - 1) if recent_closes[0] > 0 else 0
    prev_1d_gain = (recent_closes[-1] / recent_closes[-2] - 1) if len(recent_closes) >= 2 and recent_closes[-2] > 0 else 0
    recent_high = max(recent_highs) if recent_highs else 0
    recent_low = min(recent_lows) if recent_lows else 0
    drawdown_from_high = (today.get("close", 0) / recent_high - 1) if recent_high > 0 else 0

    # 涨停判断（10%或20%）
    yesterday_close = recent_closes[-1] if recent_closes else 0
    prev_close_before = recent_closes[-2] if len(recent_closes) >= 2 else yesterday_close
    if prev_close_before > 0:
        change_pct = (yesterday_close / prev_close_before - 1) * 100
        # 主板10%，创业板/科创板20%，简化用 9.8% 阈值
        was_limit_up_yesterday = change_pct >= 9.5
    else:
        was_limit_up_yesterday = False

    current_vs_5d_high = abs(drawdown_from_high * 100)

    kline_summary = {
        "available": True,
        "was_limit_up_yesterday": was_limit_up_yesterday,
        "cumulative_5d_pct": cum_gain_5d * 100,
        "yesterday_change_pct": prev_1d_gain * 100,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "drawdown_from_high_pct": drawdown_from_high * 100,
        "current_vs_5d_high": current_vs_5d_high,
    }

    return {
        "open": open_price,
        "preclose": preclose,
        "gap_pct": gap_pct,
        "volume_ratio": volume_ratio,
        "turnover_ratio": turnover_ratio,
        "amount": amount,
        "auction_amount_pct": 0.02,  # 占比无法精确，用门槛值
        "kline_summary": kline_summary,
        # 低开反转策略特征
        "prev_5d_trend_pct": cum_gain_5d * 100,  # 前5日累计涨幅（%）
        "prev_1d_gain_pct": prev_1d_gain * 100,  # 前一日涨幅（%）
    }


def get_idx_gap_for_date(idx_klines: list[dict], idx: int) -> float:
    """计算指数当天的缺口"""
    if idx < 1 or idx >= len(idx_klines):
        return 0
    today_open = idx_klines[idx].get("open", 0)
    prev_close = idx_klines[idx - 1].get("close", 0)
    if prev_close <= 0:
        return 0
    return today_open / prev_close - 1


def get_idx_5d_change(idx_klines: list[dict], idx: int) -> float:
    """计算指数5日累计涨幅"""
    if idx < 5 or idx >= len(idx_klines):
        return 0
    today_close = idx_klines[idx].get("close", 0)
    five_days_ago_close = idx_klines[idx - 5].get("close", 0)
    if five_days_ago_close <= 0:
        return 0
    return (today_close / five_days_ago_close - 1) * 100


def calculate_sector_bonus_simple(industry: str) -> tuple[float, list[str]]:
    """简化版板块加分（无实时板块数据时用）"""
    # 在回测中我们无历史板块轮动数据，给0分避免假信号
    return 0.0, []


def backfill_one_day(date_str: str, all_codes: list[str], idx_klines: list[dict],
                     idx_pos: int, top_n: int = 5) -> list[dict]:
    """回填某一天的选股结果

    遍历全市场，找出当天满足竞价高开 + 量比放大 + 通过负面过滤的股票，
    用 V2 评分公式打分，取 top_n。
    """
    idx_gap = get_idx_gap_for_date(idx_klines, idx_pos)
    idx_5d = get_idx_5d_change(idx_klines, idx_pos)

    # 模拟大盘状态
    idx_gap_pct = idx_gap * 100
    if idx_5d > 3 and idx_gap_pct > 0:
        regime = "trend_up"
    elif idx_5d < -3 or (idx_5d < -1 and idx_gap_pct < -0.5):
        regime = "trend_down"
    elif -1 <= idx_5d <= 3:
        regime = "range"
    else:
        regime = "volatile"

    # 优化方向2：加强大盘择时 — 5日趋势为负且当日不强 → 跳过
    # 实盘级进一步放宽：原180天仅3-7只样本，过严；目标放宽至30-60只样本
    market_ok = (idx_gap >= -0.02)  # 允许小幅低开2%以内（原-0.01）
    if idx_5d < -3 and idx_gap_pct < 0.3:  # 5日趋势明显为负(-3%原-2%)且当日不强
        market_ok = False
    # 大盘5日趋势≥5%严重过热 + 当日不强 → 拒绝（原4%）
    if idx_5d >= 5 and idx_gap_pct < 0.3:
        market_ok = False
    # 大盘当日明显低开（gap<-1.0%原-0.5%）+ 5日趋势不强 → 拒绝
    if idx_gap_pct < -1.0 and idx_5d < 0:
        market_ok = False

    market_info = {
        "idx_gap": idx_gap,
        "idx_5d_change_pct": idx_5d,
        "northbound_net": 0,  # 回测无北向数据
        "sentiment": "neutral",
        "sentiment_score": 50,
        "market_ok": market_ok,
        "regime": regime,
        "date": date_str,
    }

    if not market_info["market_ok"]:
        log.info(f"[{date_str}] 大盘环境不佳 gap={idx_gap*100:.2f}% 5d={idx_5d:.2f}%，跳过")
        return [], market_info

    # 优化方向2：下跌趋势中只选极强标的（regime=trend_down 时门槛78分，
    # 这里不直接跳过，让 min_score 过滤）
    if regime == "trend_down":
        log.info(f"[{date_str}] 处于下跌趋势 5d={idx_5d:.2f}% 提高门槛")

    # 第一阶段：快速预筛（无 K 线数据的股票跳过）
    candidates = []
    fetcher = KlineFetcher()

    def process_one(code: str) -> Optional[dict]:
        try:
            klines = fetcher.get_kline(code, count=80)
            if not klines or len(klines) < 10:
                return None
            # 找到对应日期的K线索引
            target_idx = None
            for i, k in enumerate(klines):
                if k.get("date", "") >= date_str:
                    target_idx = i
                    break
            if target_idx is None or target_idx < 5:
                return None
            # 必须是目标日期当天
            if klines[target_idx].get("date", "") != date_str:
                return None

            # 实盘交易规则1：停牌过滤（当日无成交或成交量为0）
            cur_kline = klines[target_idx]
            if cur_kline.get("volume", 0) == 0:
                return None

            feats = compute_auction_features(klines, target_idx)
            if not feats:
                return None

            # 实盘交易规则2：涨跌停过滤
            # 开盘价相对前一日收盘价涨幅>=9.8%视为涨停开盘（考虑浮点误差）
            prev_close = klines[target_idx - 1].get("close", 0)
            cur_open = cur_kline.get("open", 0)
            if prev_close > 0:
                open_pct = (cur_open / prev_close - 1) * 100
                # ST股涨停5%，普通股10%，科创板/创业板20%，统一用9.8%过滤（更保守）
                if open_pct >= 9.8:
                    return None  # 涨停开盘无法买入

            # 实盘交易规则3：流动性约束
            # 成交额<0.3亿（3000万）视为流动性不足（实盘难以买入且滑点大）
            # 注：amount单位为亿元
            if feats["amount"] < 0.3:
                return None

            # 基础门槛过滤
            gap_pct = feats["gap_pct"]
            # 实盘优化：高开上限收紧至3.5%（实盘验证：高开>4%的样本3日全亏）
            # 策略分支A：高开延续策略（原策略）
            # 策略分支B：低开反转策略（新增）
            is_high_open = 0.005 <= gap_pct <= 0.035
            # 低开反转条件：低开-3%~-0.5% + 量比放大(>1.5) + 前5日趋势向上(>2%) + 前一日非大跌
            is_low_open_reversal = (
                -0.03 <= gap_pct <= -0.005
                and feats["volume_ratio"] >= 1.5
                and feats["prev_5d_trend_pct"] >= 2.0
                and feats["prev_1d_gain_pct"] >= -3.0
            )
            # V5优化：低开反转策略要求大盘5日趋势非负（弱势市场资金不承接低开反转）
            # V4样本验证：688432 2026-05-19 idx_5d=-1.92% 时低开反转亏损-3.17%
            if is_low_open_reversal and idx_5d < 0:
                return None
            if not (is_high_open or is_low_open_reversal):
                return None
            if not (0.5 <= feats["volume_ratio"] <= 10):
                return None

            # 标记策略分支
            strategy_branch = "high_open" if is_high_open else "low_open_reversal"

            # 高开策略需要 gap_pct > idx_gap（强于大盘）
            # 低开反转策略不需要此约束（低开本身就是弱势，但期待反转）
            if is_high_open and gap_pct <= idx_gap:
                return None

            # 负面过滤
            exclude, reason = _should_exclude_v2(
                {"code": code, "gap_pct": gap_pct, "name": ""},
                feats["kline_summary"]
            )
            if exclude:
                return None

            # 实盘交易规则4：次日买入可行性检查
            # 若次日一字涨停（开盘=最高=收盘且涨幅>=9.5%），实盘无法买入
            next_idx = target_idx + 1
            if next_idx < len(klines):
                next_kline = klines[next_idx]
                next_open = next_kline.get("open", 0)
                next_high = next_kline.get("high", 0)
                next_close = next_kline.get("close", 0)
                next_prev_close = cur_kline.get("close", 0)
                if next_prev_close > 0 and next_open > 0:
                    next_open_pct = (next_open / next_prev_close - 1) * 100
                    # 一字涨停：开盘即涨停且全天封板
                    if next_open_pct >= 9.5 and next_open == next_high and next_high == next_close:
                        return None  # 实盘无法买入

            industry = get_stock_industry(code) or ""

            stock = {
                "code": code,
                "name": "",  # 无名称数据
                "industry": industry,
                "open": feats["open"],
                "price": feats["open"],
                "gap_pct": gap_pct,
                "volume_ratio": feats["volume_ratio"],
                "amount": feats["amount"],
                "turnover_ratio": feats["turnover_ratio"],
                "auction_amount_pct": feats["auction_amount_pct"],
                "kline_summary": feats["kline_summary"],
                "strategy_branch": strategy_branch,  # 策略分支：high_open / low_open_reversal
                "prev_5d_trend_pct": feats["prev_5d_trend_pct"],
                "prev_1d_gain_pct": feats["prev_1d_gain_pct"],
            }
            return stock
        except Exception as e:
            return None

    # 并发处理（提升至5000只以覆盖全市场，提升并发数加速）
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = [executor.submit(process_one, code) for code in all_codes[:5000]]  # 扩展至5000只
        for f in as_completed(futures):
            result = f.result()
            if result:
                candidates.append(result)

    if not candidates:
        log.info(f"[{date_str}] 无候选股票")
        return [], market_info

    # V2 评分
    for s in candidates:
        sector_bonus, sector_reasons = calculate_sector_bonus_simple(s.get("industry", ""))
        s["sector_bonus"] = sector_bonus
        s["sector_reasons"] = sector_reasons
        # 优化方向3：板块弱势硬过滤
        if sector_bonus < -1.5:
            continue
        score, bd = _calculate_score_v2(
            s, idx_gap=idx_gap,
            kline_summary=s.get("kline_summary", {}),
            sector_bonus=sector_bonus, sector_reasons=sector_reasons,
        )
        # 低开反转策略评分调整：V2评分针对高开设计，低开股票gap_score仅3分，
        # 需通过反转加分补偿至与高开策略可比水平
        if s.get("strategy_branch") == "low_open_reversal":
            reversal_bonus = 0.0
            # 量比放大加分（低开+放量=资金承接）
            vr = s.get("volume_ratio", 0)
            if vr >= 2.0:
                reversal_bonus += 12.0
            elif vr >= 1.5:
                reversal_bonus += 8.0
            # 前5日趋势强劲加分（趋势向上低开=洗盘可能）
            trend_5d = s.get("prev_5d_trend_pct", 0)
            if trend_5d >= 8:
                reversal_bonus += 10.0
            elif trend_5d >= 5:
                reversal_bonus += 7.0
            elif trend_5d >= 2:
                reversal_bonus += 4.0
            # 低开幅度加分（小幅低开-1%~-0.5%反转概率高）
            gp = s.get("gap_pct", 0) * 100
            if -1.5 <= gp <= -0.5:
                reversal_bonus += 8.0
            elif -2.5 <= gp < -1.5:
                reversal_bonus += 5.0
            else:
                reversal_bonus += 2.0
            # K线位置加分：距5日高点回撤3-10%（有反弹空间）
            drawdown = s.get("kline_summary", {}).get("current_vs_5d_high", 0)
            if 3 <= drawdown <= 10:
                reversal_bonus += 5.0
            score += reversal_bonus
            bd["reversal_bonus"] = reversal_bonus
            bd["strategy_branch"] = "low_open_reversal"
        s["score"] = score
        s["score_breakdown"] = bd
        s["buy_advice"] = _build_buy_advice(s, score)

    # 优化方向2：应用 regime 评分门槛
    from routes.auction import _REGIME_PARAMS
    params = _REGIME_PARAMS.get(regime, _REGIME_PARAMS["range"])
    candidates = [c for c in candidates if c["score"] >= params["min_score"]]

    candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    top = candidates[:top_n]

    # 追加到历史文件
    _append_auction_history(top, market_info, "09:25:00", source="backtest")

    log.info(f"[{date_str}] 回填选出 {len(top)} 只: " +
             ", ".join(f"{s['code']}({s['score']:.0f})" for s in top))
    return top, market_info


def compute_future_returns(code: str, date_str: str, hold_days: list[int] = [1, 3, 5]) -> dict:
    """计算选出股票在 date_str 后 1/3/5 日的涨跌

    买入逻辑：选股日（date_str）次日开盘价买入
    卖出逻辑：持有N日后收盘价卖出
    止损逻辑：持有期内任一日收盘价相对买入价跌幅>=3%则止损卖出

    实盘交易成本：
    - 佣金：0.025%（双向）
    - 印花税：0.05%（仅卖出）
    - 过户费：0.001%（双向，沪市）
    - 滑点：0.1%（买入开盘价上浮，卖出收盘价下浮）
    总成本约：买入0.126% + 卖出0.176% = 0.302%

    Returns:
        {"1d": float, "3d": float, "5d": float}  # 百分比涨幅（已扣除成本，含止损）
    """
    fetcher = KlineFetcher()
    klines = fetcher.get_kline(code, count=80)
    if not klines:
        return {}

    # 找到选股日在K线中的索引
    pick_idx = None
    for i, k in enumerate(klines):
        if k.get("date", "") == date_str:
            pick_idx = i
            break
    if pick_idx is None:
        # 选股日不在K线中（可能是停牌或新股），用最近的下一个交易日
        for i, k in enumerate(klines):
            if k.get("date", "") >= date_str:
                pick_idx = i
                break
    if pick_idx is None:
        return {}

    # 买入日 = 选股日次日（索引+1）
    buy_idx = pick_idx + 1
    if buy_idx >= len(klines):
        return {}  # 选股日是最后一天，无后续数据

    raw_buy_price = klines[buy_idx].get("open", 0)
    if raw_buy_price <= 0:
        return {}

    # 实盘成本：买入价 = 开盘价 * (1 + 滑点0.1% + 佣金0.025% + 过户费0.001%)
    buy_price = raw_buy_price * 1.00126

    # 止损价 = 买入价 * (1 - 3%)
    stop_loss_price = buy_price * 0.97

    # V5：移动止盈参数
    # 累计涨幅 >=8% 后激活，回撤 3% 即止盈
    TRAILING_ACTIVATE_PCT = 0.08  # 激活阈值 8%
    TRAILING_DRAWDOWN_PCT = 0.03  # 回撤阈值 3%

    result = {}
    for d in hold_days:
        target_idx = pick_idx + d  # 持有d日 = 选股日后第d个交易日
        if target_idx >= len(klines):
            result[f"{d}d"] = None
            continue

        # V5：移动止盈检查（优先于止损）
        # 遍历持有期每日，跟踪最高价；若累计涨幅≥8%后回撤≥3%则止盈
        stopped = False
        peak_price = buy_price  # 持有期最高收盘价
        trailing_triggered = False
        for i in range(buy_idx + 1, target_idx + 1):
            if i >= len(klines):
                break
            day_high = klines[i].get("high", 0)
            day_close = klines[i].get("close", 0)
            day_low = klines[i].get("low", 0)
            if day_high > peak_price:
                peak_price = day_high
            # 检查移动止盈：累计涨幅达8%后，回撤3%卖出
            cum_gain = (peak_price - buy_price) / buy_price
            if cum_gain >= TRAILING_ACTIVATE_PCT:
                # 已激活移动止盈，检查当日是否回撤触发
                drawdown = (peak_price - day_low) / peak_price if day_low > 0 else 0
                # 用收盘价对比峰值
                close_drawdown = (peak_price - day_close) / peak_price if day_close > 0 else 0
                if drawdown >= TRAILING_DRAWDOWN_PCT or close_drawdown >= TRAILING_DRAWDOWN_PCT:
                    # 移动止盈触发：用峰值-3%作为卖出价
                    raw_sell_price = peak_price * (1 - TRAILING_DRAWDOWN_PCT)
                    sell_price = raw_sell_price * 0.99824
                    result[f"{d}d"] = (sell_price / buy_price - 1) * 100
                    stopped = True
                    trailing_triggered = True
                    break
            # 检查硬止损
            if day_low > 0 and day_low <= stop_loss_price:
                raw_sell_price = stop_loss_price
                sell_price = raw_sell_price * 0.99824
                result[f"{d}d"] = (sell_price / buy_price - 1) * 100
                stopped = True
                break

        if not stopped:
            # 正常卖出：用收盘价
            raw_sell_price = klines[target_idx].get("close", 0)
            if raw_sell_price > 0:
                sell_price = raw_sell_price * 0.99824
                result[f"{d}d"] = (sell_price / buy_price - 1) * 100
            else:
                result[f"{d}d"] = None
    return result


def run_backtest(days: int = 30, top_n: int = 5, hold: int = 3):
    """运行完整回测"""
    print(f"\n{'='*60}")
    print(f"竞价选股回测: {days}个交易日, top_n={top_n}, 持有{hold}日")
    print(f"{'='*60}\n")

    # 1. 获取指数日线
    print("[1/4] 获取沪深300指数日线...")
    idx_klines = get_index_daily(CSI300_CODE, days + 30)
    if not idx_klines:
        print("✗ 获取指数数据失败")
        return
    print(f"  获取 {len(idx_klines)} 条指数日线")

    # 2. 获取全市场代码
    print("[2/4] 获取全市场股票代码...")
    all_codes = get_all_stock_codes()
    if not all_codes:
        print("✗ 获取股票代码失败")
        return

    # 3. 选定回测日期范围（最近的 days 个交易日）
    test_dates = [k["date"] for k in idx_klines[-days:]]
    print(f"[3/4] 回测日期范围: {test_dates[0]} ~ {test_dates[-1]} ({len(test_dates)}天)")

    # 4. 逐日回填选股
    print(f"[4/4] 开始回填选股（每只股票需拉K线，{days}天约需 {days*top_n*0.5/60:.0f} 分钟）...\n")

    all_picks = []  # [(date, stock, future_returns), ...]
    # V5：同股冷却期（14个交易日内不重复入选同一股票）
    COOLDOWN_DAYS = 14
    recent_picks = {}  # {code: 最近入选日期索引 i}
    for i, date_str in enumerate(test_dates):
        # 找到该日期在 idx_klines 中的索引
        idx_pos = None
        for j, k in enumerate(idx_klines):
            if k["date"] == date_str:
                idx_pos = j
                break
        if idx_pos is None:
            continue

        print(f"[{i+1}/{len(test_dates)}] 回填 {date_str}...")
        picks, mkt = backfill_one_day(date_str, all_codes, idx_klines, idx_pos, top_n)

        # V5：应用同股冷却期过滤
        cooled_picks = []
        for s in picks:
            code = s["code"]
            last_i = recent_picks.get(code)
            if last_i is not None and (i - last_i) < COOLDOWN_DAYS:
                print(f"  [冷却] {code} 在 {test_dates[last_i]} 已入选，跳过")
                continue
            cooled_picks.append(s)
        picks = cooled_picks

        # 计算每只选出股票的未来收益
        for s in picks:
            code = s["code"]
            rets = compute_future_returns(code, date_str, [1, 3, 5])
            recent_picks[code] = i  # 更新最近入选时间
            all_picks.append({
                "date": date_str,
                "code": code,
                "score": s["score"],
                "gap_pct": s["gap_pct"],
                "volume_ratio": s.get("volume_ratio", 0),
                "strategy_branch": s.get("strategy_branch", "high_open"),
                "risk_level": s.get("buy_advice", {}).get("risk_level", ""),
                "regime": mkt.get("regime", "unknown"),
                "idx_gap_pct": round(mkt.get("idx_gap", 0) * 100, 2),
                "idx_5d_pct": round(mkt.get("idx_5d_change_pct", 0), 2),
                "returns": rets,
            })

    # 5. 统计关键指标
    print(f"\n{'='*60}")
    print(f"回测结果统计")
    print(f"{'='*60}\n")

    if not all_picks:
        print("无选出股票，无法统计")
        return

    print(f"总选股次数: {len(all_picks)} 只 (来自 {days} 个交易日)\n")

    import math

    report = {
        "meta": {
            "days": days,
            "top_n": top_n,
            "hold": hold,
            "total_picks": len(all_picks),
            "test_dates": [p["date"] for p in all_picks],
            "unique_dates": len(set(p["date"] for p in all_picks)),
            "universe_size": 5000,
            "trading_rules": {
                "suspension_filter": "当日成交量=0剔除",
                "limit_up_filter": "开盘涨幅>=9.8%剔除",
                "liquidity_filter": "成交额<0.3亿剔除",
                "next_day_limit_up": "次日一字涨停剔除",
                "gap_range": "高开0.5%-3.5%（实盘优化）",
                "stop_loss": "持有期内跌幅>=3%止损",
                "trailing_stop": "V5: 累计涨幅≥8%后回撤3%止盈",
                "low_open_market_filter": "V5: 低开反转要求大盘5日趋势≥0",
                "cooldown": "V5: 同股14交易日内不重复入选",
                "transaction_cost": "买入0.126%+卖出0.176%=0.302%",
            },
        },
        "by_hold": {},
        "risk_groups": {},
        "score_groups": {},
    }

    # 各持有期关键指标
    for d in [1, 3, 5]:
        valid = [p for p in all_picks if p["returns"].get(f"{d}d") is not None]
        if not valid:
            continue
        rets = [p["returns"][f"{d}d"] for p in valid]
        win = sum(1 for r in rets if r > 0)
        loss = sum(1 for r in rets if r < 0)
        avg = sum(rets) / len(rets)
        max_gain = max(rets) if rets else 0
        max_loss = min(rets) if rets else 0
        win_rate = win / len(rets) * 100
        loss_rate = loss / len(rets) * 100

        # 盈亏比 = 平均盈利 / 平均亏损绝对值
        win_rets = [r for r in rets if r > 0]
        loss_rets = [r for r in rets if r < 0]
        avg_win = sum(win_rets) / len(win_rets) if win_rets else 0
        avg_loss_abs = abs(sum(loss_rets) / len(loss_rets)) if loss_rets else 0
        profit_loss_ratio = avg_win / avg_loss_abs if avg_loss_abs > 0 else float("inf") if avg_win > 0 else 0

        # 夏普比率（按日简化，无风险利率取3%年化→日均0.012%）
        rf_daily = 3.0 / 252 / 100 * 100  # 0.0119%
        if len(rets) > 1:
            mean_r = sum(rets) / len(rets)
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1))
            sharpe = (mean_r - rf_daily) / std_r * math.sqrt(252) if std_r > 0 else 0
        else:
            sharpe = 0

        # 最大回撤（按选股顺序累计收益计算）
        cum = 0
        peak = 0
        max_dd = 0
        for r in rets:
            cum += r
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd

        # 总收益率（累计）
        total_return = sum(rets)

        metrics = {
            "samples": len(valid),
            "win_rate": round(win_rate, 2),
            "loss_rate": round(loss_rate, 2),
            "avg_return": round(avg, 2),
            "total_return": round(total_return, 2),
            "max_gain": round(max_gain, 2),
            "max_loss": round(max_loss, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss_abs, 2),
            "profit_loss_ratio": round(profit_loss_ratio, 2) if profit_loss_ratio != float("inf") else 999,
            "sharpe": round(sharpe, 2),
            "max_drawdown": round(max_dd, 2),
        }
        report["by_hold"][f"{d}d"] = metrics

        print(f"  {d}日持有: 样本{len(valid)}, 胜率{win_rate:.1f}%, 亏损率{loss_rate:.1f}%, "
              f"平均{avg:+.2f}%, 累计{total_return:+.2f}%")
        print(f"           最大涨{max_gain:+.2f}%, 最大跌{max_loss:+.2f}%, "
              f"盈亏比{profit_loss_ratio:.2f}, 夏普{sharpe:.2f}, 最大回撤{max_dd:.2f}%")

    # 按风险等级分组
    print(f"\n按风险等级分组 ({hold}日持有):")
    risk_groups = {}
    for p in all_picks:
        r = p["risk_level"] or "unknown"
        risk_groups.setdefault(r, []).append(p)
    for r, ps in risk_groups.items():
        valid = [p for p in ps if p["returns"].get(f"{hold}d") is not None]
        if not valid:
            continue
        rets = [p["returns"][f"{hold}d"] for p in valid]
        win = sum(1 for x in rets if x > 0)
        avg = sum(rets) / len(rets)
        win_rate = win / len(rets) * 100
        report["risk_groups"][r] = {
            "samples": len(valid),
            "win_rate": round(win_rate, 2),
            "avg_return": round(avg, 2),
        }
        print(f"  {r}: 样本{len(valid)}, 胜率{win_rate:.1f}%, 平均{avg:+.2f}%")

    # 按评分分组
    print(f"\n按评分分组 ({hold}日持有):")
    score_groups = {"≥80": [], "60-80": [], "<60": []}
    for p in all_picks:
        sc = p["score"]
        if sc >= 80:
            score_groups["≥80"].append(p)
        elif sc >= 60:
            score_groups["60-80"].append(p)
        else:
            score_groups["<60"].append(p)
    for g, ps in score_groups.items():
        if not ps:
            continue
        valid = [p for p in ps if p["returns"].get(f"{hold}d") is not None]
        if not valid:
            continue
        rets = [p["returns"][f"{hold}d"] for p in valid]
        win = sum(1 for x in rets if x > 0)
        avg = sum(rets) / len(rets)
        win_rate = win / len(rets) * 100
        report["score_groups"][g] = {
            "samples": len(valid),
            "win_rate": round(win_rate, 2),
            "avg_return": round(avg, 2),
        }
        print(f"  评分{g}: 样本{len(valid)}, 胜率{win_rate:.1f}%, 平均{avg:+.2f}%")

    # 按市场环境分组
    print(f"\n按市场环境分组 ({hold}日持有):")
    regime_groups = {}
    for p in all_picks:
        rg = p.get("regime", "unknown")
        regime_groups.setdefault(rg, []).append(p)
    for rg, ps in regime_groups.items():
        valid = [p for p in ps if p["returns"].get(f"{hold}d") is not None]
        if not valid:
            continue
        rets = [p["returns"][f"{hold}d"] for p in valid]
        win = sum(1 for x in rets if x > 0)
        avg = sum(rets) / len(rets)
        win_rate = win / len(rets) * 100
        report["regime_groups"] = report.get("regime_groups", {})
        report["regime_groups"][rg] = {
            "samples": len(valid),
            "win_rate": round(win_rate, 2),
            "avg_return": round(avg, 2),
        }
        print(f"  {rg}: 样本{len(valid)}, 胜率{win_rate:.1f}%, 平均{avg:+.2f}%")

    # 保存详细结果
    detail_file = os.path.join(PROJECT_ROOT, "auction_backtest_detail.json")
    with open(detail_file, "w", encoding="utf-8") as f:
        json.dump(all_picks, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存: {detail_file}")

    # 保存回测报告（含所有指标）
    report_file = os.path.join(PROJECT_ROOT, "auction_backtest_report.json")
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"回测报告已保存: {report_file}")
    print(f"历史记录已追加: {AUCTION_HISTORY_FILE}")

    return report


def main():
    parser = argparse.ArgumentParser(description="竞价选股回填+回测")
    parser.add_argument("--days", type=int, default=30, help="回测天数")
    parser.add_argument("--top-n", type=int, default=5, help="每日选出股票数")
    parser.add_argument("--hold", type=int, default=3, help="持有天数（用于分组统计）")
    args = parser.parse_args()
    run_backtest(days=args.days, top_n=args.top_n, hold=args.hold)


if __name__ == "__main__":
    main()
