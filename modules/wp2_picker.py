"""尾盘选股模块 - 输出格式与wp2_pick.html模板完全兼容

基于旧版 web_app.py 的 WP2 选股逻辑恢复：
- MA 多级对齐过滤
- 成交量突破过滤
- RSI/MACD 增强条件
- 自定义评分 _wp2_calc_score
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

from modules.http_client import EM_HEADERS, session
from modules.logger import log
from modules.data_fetcher import get_stock_industry, preload_industry_cache, get_financial_data, _get_session
from modules.market_env import get_market_env
from modules.scoring import calculate_buy_sell, multi_factor_evaluate, industry_concentration_limit


def _get_stock_list_api(timeout: int = 30) -> Optional[list[str]]:
    """获取A股代码列表 — 使用东方财富datacenter-web API"""
    try:
        from modules.data_fetcher import _get_all_stock_codes
        codes = _get_all_stock_codes()
        if codes:
            log.info(f"股票列表API获取完成: {len(codes)} 只")
            return codes
    except Exception as e:
        log.warning(f"股票列表API获取失败: {e}")
    try:
        from modules.data_fetcher import get_preset_financials
        preset = get_preset_financials()
        if preset:
            codes = [c for c in preset.keys() if c[0] not in ('2','4','8','9') and len(c) == 6]
            log.info(f"离线库降级获取 {len(codes)} 只")
            return codes
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 模块级缓存
# ---------------------------------------------------------------------------
_WP2_PICK_DATA: dict = {
    "stocks": [],
    "pick_time": None,
    "last_update": None,
    "filter_stats": [],
    "market_info": {},
    "running": False,
}
_WP2_PICK_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def run_wp2_picker(
    top_n: int = 20,
    min_cap: int = 30,
    max_cap: int = 500,
    min_amt: int = 3,
    vol_mul: float = 1.5,
    break_n: int = 20,
    body_r: float = 0.6,
    rsi_lo: float = 40,
    rsi_hi: float = 75,
    min_score: int = 30,
) -> list[dict]:
    """执行尾盘选股，返回与模板兼容的股票列表

    模板期望字段:
    - code, name, price, ch(涨跌幅), cap(市值), amt(成交额)
    - vr(量比), ma(MA信号), rsi, macd(MACD值), score
    """
    env = None
    try:
        env = get_market_env()
        if not env.can_pick():
            log.warning(f"WP2选股: 市场环境不佳(status={env.status}, trend={env.trend})，暂停选股")
            return []
        top_n = env.adjusted_top_n(top_n)
        log.info(f"WP2选股: 市场环境 status={env.status}, trend={env.trend}, top_n={top_n}")

        # V2.1: 强下跌市跳过尾盘选股（历史回测显示熊市尾盘策略亏损严重）
        if env.trend in ("bear", "strong_down") and env.status in ("strong_down", "bear"):
            log.warning(f"WP2选股 V2.1: 市场{env.trend}/{env.status}，暂停尾盘选股")
            _save_empty_result(datetime.now(), {}, filter_log)
            return []
    except Exception as e:
        log.warning(f"WP2选股: 市场环境检测失败: {e}，使用默认参数")
        env = None

    # V2.3: 大盘过热暂停 — 沪深300近5日涨幅≥4%时暂停
    try:
        idx_kd = kline_fetcher.get_kline_raw("sh000300", 10)
        if idx_kd and idx_kd.get("data",{}).get("klines"):
            klines = idx_kd["data"]["klines"]
            if len(klines) >= 5:
                closes = [float(k.split(",")[2]) for k in klines[-5:] if len(k.split(",")) >= 3]
                if len(closes) == 5:
                    idx_5d_pct = (closes[-1] - closes[0]) / closes[0] * 100
                    if idx_5d_pct >= 4:
                        log.warning(f"WP2选股 V2.3: 沪深300近5日涨幅{idx_5d_pct:.1f}%≥4%，过热暂停")
                        with _WP2_PICK_LOCK:
                            _WP2_PICK_DATA["running"] = False
                        return []
    except Exception:
        pass

    with _WP2_PICK_LOCK:
        _WP2_PICK_DATA["running"] = True

    filter_log: list[dict] = []

    try:
        now = datetime.now()
        log.info(f"执行尾盘强势股选股... {now.strftime('%H:%M:%S')}")

        # 1. 获取大盘信息
        market_info = _get_market_info()

        # 1b. 获取板块强度排名
        top_sectors = _get_top_sectors(top_n=15)

        # 2. 获取股票代码列表（使用datacenter-web API替代akshare）
        code_list = _get_stock_list_api(timeout=30)
        if not code_list:
            log.warning("获取股票列表失败")
            _save_empty_result(now, market_info, filter_log)
            return []

        log.info(f"共 {len(code_list)} 只股票")
        sina_codes = [f"sh{c}" if c.startswith("6") else f"sz{c}" for c in code_list]

        # 4. 批量获取行情
        all_data: dict[str, dict] = {}
        batch_size = 500
        for i in range(0, len(sina_codes), batch_size):
            batch = sina_codes[i : i + batch_size]
            batch_data = _wp2_get_sina_quote(batch)
            all_data.update(batch_data)
            time.sleep(0.3)

        # 5. 批量获取市值
        cap_data: dict[str, float] = {}
        for i in range(0, len(sina_codes), batch_size):
            batch = sina_codes[i : i + batch_size]
            batch_caps = _wp2_get_tencent_market_cap(batch)
            cap_data.update(batch_caps)
            time.sleep(0.3)

        for code, info in all_data.items():
            cap_info = cap_data.get(code, {})
            if isinstance(cap_info, dict):
                cap_yi = cap_info.get("cap", 0)
                info["f21"] = cap_yi * 1e8
                info["f9"] = cap_info.get("pe", info.get("f9", 0))
                info["f23"] = cap_info.get("pb", info.get("f23", 0))
                # 腾讯API提供真实换手率(field[38])和量比(field[49])
                tencent_tr = cap_info.get("turnover_rate", 0)
                tencent_vr = cap_info.get("volume_ratio", 0)
                if tencent_tr > 0:
                    info["f8"] = tencent_tr  # 替换新浪错误的换手率
                if tencent_vr > 0:
                    info["f10"] = tencent_vr  # 替换新浪计算的量比
            else:
                info["f21"] = float(cap_info) * 1e8 if cap_info else 0

        all_stocks = list(all_data.values())
        log.info(f"获取行情数据 {len(all_stocks)} 只")

        # 6. 第一层：基础过滤
        s1 = _base_filter(all_stocks, min_cap, max_cap, min_amt, filter_log)
        if not s1:
            _save_empty_result(now, market_info, filter_log)
            return []

        s1.sort(key=lambda x: float(x.get("f6", 0)), reverse=True)

        # ROE基本面预过滤（排除亏损股）
        s1_codes = [str(s.get("f12", "")) for s in s1 if s.get("f12")]
        fin_data = get_financial_data(s1_codes) if s1_codes else {}
        s1_filtered = []
        for s in s1:
            code = str(s.get("f12", ""))
            fin = fin_data.get(code)
            roe = fin.roe if fin else 0
            if roe >= 0:
                s1_filtered.append(s)
        if len(s1_filtered) < len(s1):
            log.info(f"ROE过滤: {len(s1)} -> {len(s1_filtered)} 只（排除{len(s1)-len(s1_filtered)}只亏损股）")
        s1 = s1_filtered
        if not s1:
            _save_empty_result(now, market_info, filter_log)
            return []

        # 7. 获取 K 线数据
        log.info(f"获取 {len(s1)} 只K线...")
        kline_data = _fetch_klines(s1)

        # 8. 多层技术过滤 + 评分
        results = _technical_filter_and_score(
            s1, kline_data, vol_mul, break_n, body_r, rsi_lo, rsi_hi, min_score, filter_log, top_sectors
        )

        results.sort(key=lambda x: x["score"], reverse=True)
        final = results[:top_n]

        # 预加载行业缓存 + 行业分散
        preload_industry_cache([s["code"] for s in results if s.get("code")])
        from modules.data_fetcher import _industry_cache as _ic
        for s in results:
            code = s.get("code", "")
            cached = _ic.get(code)
            if isinstance(cached, dict):
                s["industry"] = cached.get("industry", "未知")
                s["sector"] = cached.get("sector_type", "default")
        # 行业分散：同行业最多3只
        final = industry_concentration_limit(results, max_per_industry=3, min_count=top_n)[:top_n]
        for s in final:
            if not s.get("industry"):
                try:
                    info = get_stock_industry(s["code"])
                    s["industry"] = info.get("industry", "未知")
                except Exception:
                    pass

        # Add buy/sell points and V5 evaluation
        for stock in final:
            try:
                from modules.technical import calculate_technical_indicators
                code = stock.get("code", "")
                tech = calculate_technical_indicators(code, days=30) if code else None
                stock_for_bs = {
                    "code": code,
                    "name": stock.get("name", ""),
                    "price": stock.get("price", 0),
                    "pe": stock.get("pe", 0), "pb": stock.get("pb", 0),
                    "market_cap": stock.get("cap", 0),
                    "turnover_rate": stock.get("to", 0),
                    "change_pct": stock.get("ch", 0),
                }
                # Fetch financial data for proper V5 evaluation
                try:
                    fin_map = get_financial_data([code])
                    fin = fin_map.get(code)
                    if fin:
                        stock_for_bs["roe"] = fin.roe
                        stock_for_bs["gross_margin"] = fin.gross_margin
                        stock_for_bs["net_margin"] = fin.net_margin
                        stock_for_bs["rev_growth"] = fin.revenue_growth
                        stock_for_bs["profit_growth"] = fin.profit_growth
                        stock_for_bs["debt_ratio"] = fin.debt_ratio
                except Exception as fe:
                    log.debug(f"WP2: 财务数据获取失败 {code}: {fe}")
                bs = calculate_buy_sell(stock_for_bs, stock.get("score", 50), tech_data=tech)
                if bs:
                    stock["buy_sell"] = bs
                # V5 multi-factor evaluation
                try:
                    v5_result = multi_factor_evaluate(stock_for_bs)
                    if v5_result:
                        stock["v5_score"] = v5_result.get("v5_total") or v5_result.get("total_score")
                        stock["v5_factors"] = v5_result.get("v5_factors") or v5_result.get("factors")
                        stock["v5_reasons"] = v5_result.get("v5_reasons") or v5_result.get("reasons")
                        stock["v5_recommendation"] = v5_result.get("v5_recommendation") or v5_result.get("recommendation")
                except Exception:
                    pass
            except Exception:
                pass

        log.info(f"尾盘选股完成: {len(final)} 只")

        with _WP2_PICK_LOCK:
            _WP2_PICK_DATA["date"] = now.strftime("%Y-%m-%d")
            _WP2_PICK_DATA["stocks"] = final
            _WP2_PICK_DATA["pick_time"] = now.strftime("%H:%M:%S")
            _WP2_PICK_DATA["last_update"] = now.strftime("%Y-%m-%d %H:%M:%S")
            _WP2_PICK_DATA["filter_stats"] = filter_log
            _WP2_PICK_DATA["market_info"] = market_info
            _WP2_PICK_DATA["running"] = False

        return final

    except Exception as exc:
        log.error(f"尾盘选股失败: {exc}")
        with _WP2_PICK_LOCK:
            _WP2_PICK_DATA["running"] = False
        return []


def get_wp2_pick_data() -> dict:
    """获取当前 WP2 选股缓存数据（供路由使用）"""
    with _WP2_PICK_LOCK:
        return {
            "stocks": _WP2_PICK_DATA.get("stocks", []),
            "pick_time": _WP2_PICK_DATA.get("pick_time"),
            "last_update": _WP2_PICK_DATA.get("last_update"),
            "filter_stats": _WP2_PICK_DATA.get("filter_stats", []),
            "market_info": _WP2_PICK_DATA.get("market_info", {}),
            "running": _WP2_PICK_DATA.get("running", False),
        }


def set_wp2_running(running: bool = True) -> None:
    """设置运行状态"""
    with _WP2_PICK_LOCK:
        _WP2_PICK_DATA["running"] = running


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _get_market_info() -> dict:
    """获取大盘信息"""
    try:
        import requests
        ir = session.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": "1.000300", "fields": "f43,f60,f170"},
            headers=EM_HEADERS,
            timeout=10,
        )
        if ir.status_code == 200:
            idata = ir.json().get("data", {})
            if idata:
                return {
                    "idx_price": idata.get("f43", 0),
                    "idx_open": idata.get("f60", 0),
                    "idx_change": idata.get("f170", 0),
                }
    except Exception:
        pass
    return {}


def _save_empty_result(
    now: datetime, market_info: dict, filter_log: list[dict]
) -> None:
    """保存空结果"""
    with _WP2_PICK_LOCK:
        _WP2_PICK_DATA["stocks"] = []
        _WP2_PICK_DATA["pick_time"] = now.strftime("%H:%M:%S")
        _WP2_PICK_DATA["last_update"] = now.strftime("%Y-%m-%d %H:%M:%S")
        _WP2_PICK_DATA["filter_stats"] = filter_log or [{"n": "akshare", "b": 0, "a": 0}]
        _WP2_PICK_DATA["market_info"] = market_info
        _WP2_PICK_DATA["running"] = False


def _base_filter(
    all_stocks: list[dict],
    min_cap: int,
    max_cap: int,
    min_amt: int,
    filter_log: list[dict],
) -> list[dict]:
    """第一层：基础过滤"""
    s1: list[dict] = []
    for s in all_stocks:
        c = str(s.get("f12", ""))
        n = str(s.get("f14", ""))
        if c.startswith("688") or c.startswith("8") or c.startswith("4"):
            continue
        if "ST" in n or "退" in n or "*" in n:
            continue
        cap = float(s.get("f21", 0))
        if not cap or cap < min_cap * 1e8 or cap > max_cap * 1e8:
            continue
        amt = float(s.get("f6", 0))
        # 非交易时间成交额可能为0，用换手率替代判断活跃度
        turnover = float(s.get("f8", 0))
        if amt and amt < min_amt * 1e8 and turnover < 0.3:
            continue
        if not s.get("f2") or s.get("f2") == "-":
            continue
        s1.append(s)

    filter_log.append({"n": "基础过滤", "b": len(all_stocks), "a": len(s1)})
    log.info(f"第1层 基础过滤: {len(all_stocks)}->{len(s1)}")
    return s1


def _fetch_klines(s1: list[dict]) -> dict[str, dict]:
    """获取K线数据（多源自动降级，限制并发）"""
    from modules.kline_fetcher import kline_fetcher
    kline_data: dict[str, dict] = {}

    def _fetch_one(s: dict) -> tuple[str, dict | None]:
        code = str(s.get("f12", ""))
        market = str(s.get("f13", "1"))
        symbol = f"sh{code}" if market == "1" else f"sz{code}"
        kd = kline_fetcher.get_kline_raw(symbol, 120)
        time.sleep(0.1)  # 限速，避免触发WAF
        return (code, kd)

    # 降低并发数到3，避免WAF限流
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(_fetch_one, s) for s in s1]
        for future in as_completed(futures):
            try:
                code, kd = future.result(timeout=30)
                if kd:
                    kline_data[code] = kd
            except Exception:
                pass

    log.info(f"K线获取完成: {len(kline_data)}/{len(s1)} 只成功, 源状态={kline_fetcher.health_status()}")
    return kline_data


def _get_top_sectors(top_n: int = 15) -> dict[str, float]:
    """获取涨幅前N的行业板块，用于板块强度加分"""
    try:
        from modules.data_fetcher import fetch_sina_sectors
        sectors = fetch_sina_sectors("industry")
        if sectors:
            top = sorted(sectors, key=lambda x: x.get("change_pct", 0), reverse=True)[:top_n]
            return {s["name"]: s.get("change_pct", 0) for s in top}
    except Exception as e:
        log.debug(f"获取板块排名失败: {e}")
    return {}


def _check_limit_break(kl: list[dict], pct: float) -> bool:
    """检查是否炸板：今日曾涨停(zf>=9.5%)但收盘未封住"""
    if not kl or len(kl) < 1:
        return False
    last = kl[-1]
    o, c, h = last["o"], last["c"], last["h"]
    if o <= 0:
        return False
    limit_price = o * 1.095
    return h >= limit_price and c < limit_price * 0.995


def _technical_filter_and_score(
    s1: list[dict],
    kline_data: dict[str, dict],
    vol_mul: float,
    break_n: int,
    body_r: float,
    rsi_lo: float,
    rsi_hi: float,
    min_score: int,
    filter_log: list[dict],
    top_sectors: dict | None = None,
) -> list[dict]:
    """新策略评分体系 V2.3 — 基于回测验证的11大因子

    核心逻辑: 尾盘收盘位置好+温和上涨(2-5%)的股票次日表现最好

    11大因子:
    1. 收盘位置 (0~25分): 光头阳线=尾盘抢筹信号（V2.1下调权重防评分倒挂）
    2. 连续放量 (-5~+20分): 3日量递增+放量=资金持续进场
    3. 均线斜率加速 (-10~+20分): MA5加速上扬=趋势加强
    4. 波动率收缩突破 (-5~+15分): ATR收窄后突破=蓄势待发
    5. 量价配合 (-10~+15分): 放量涨+缩量跌=健康走势
    6. 风险排除 (-40~-5分): 涨幅过大/连板/跳空=次日回调风险
    7. 板块强度 (0~15分): 个股所在板块涨幅排名靠前=溢价效应
    8. 炸板惩罚 (-20分): 今日曾涨停但未封住=次日低开概率高
    9. 尾盘30分钟综合 (0~30分): 拉升斜率+尾盘量占比+竞价动量
    10. 尾盘相对强度 (-10~15分): 个股尾盘跑赢沪深300=相对强势
    11. 动量持续性 (-10~20分): 近5日涨幅3-12%且持续上涨=动量可持续
    """
    results: list[dict] = []
    stat_close = stat_vol = stat_ma = stat_atr = stat_vp = stat_sector = 0

    for s in s1:
        code = str(s.get("f12", ""))
        kd = kline_data.get(code)
        if not kd or not kd.get("data") or not kd.get("data", {}).get("klines"):
            continue
        klines = kd["data"]["klines"]
        if len(klines) < 15:
            continue

        # 解析K线
        kl: list[dict] = []
        for line in klines:
            parts = line.split(",")
            if len(parts) >= 6:
                kl.append(
                    {
                        "o": float(parts[1]),
                        "c": float(parts[2]),
                        "h": float(parts[3]),
                        "lo": float(parts[4]),
                        "v": float(parts[5]),
                    }
                )
        kl = [k for k in kl if k["c"] > 0]
        if len(kl) < 15:
            continue

        cl = [k["c"] for k in kl]
        hi = [k["h"] for k in kl]
        lo = [k["lo"] for k in kl]
        vl = [k["v"] for k in kl]
        op = [k["o"] for k in kl]
        t = len(kl) - 1

        # 当日涨幅
        pct = float(s.get("f3", 0))

        # MA 计算
        m5 = _wp2_calc_ma(cl, 5)
        m10 = _wp2_calc_ma(cl, 10)
        m20 = _wp2_calc_ma(cl, 20)
        m60 = _wp2_calc_ma(cl, 60)

        # RSI
        rsi = _wp2_calc_rsi(cl)
        # MACD
        mc = _wp2_calc_macd(cl)

        score = 0.0

        # =========================================================================
        # 因子1: 收盘位置 (0~25分) — V2.1下调权重(原40)，高分终点位高被过度追涨
        # 注意: 涨停板的光头阳线是被动形成的，不是主动尾盘抢筹，需降分
        # =========================================================================
        day_range = hi[t] - lo[t]
        close_pos_score = 0
        if day_range > 0:
            close_position = (cl[t] - lo[t]) / day_range
            upper_shadow = hi[t] - cl[t]
            shadow_ratio = upper_shadow / day_range

            # 涨停板降分: 涨停=被动光头，不是主动抢筹
            is_limit_up = pct >= 9.5

            if close_position > 0.95 and shadow_ratio < 0.03:
                close_pos_score = 15 if is_limit_up else 25  # V2.1: 40→25
            elif close_position > 0.90 and shadow_ratio < 0.06:
                close_pos_score = 12 if is_limit_up else 22  # V2.1: 35→22
            elif close_position > 0.85 and shadow_ratio < 0.10:
                close_pos_score = 10 if is_limit_up else 18  # V2.1: 30→18
            elif close_position > 0.70 and shadow_ratio < 0.20:
                close_pos_score = 8 if is_limit_up else 14   # V2.1: 22→14
            elif close_position > 0.50:
                close_pos_score = 6 if is_limit_up else 8    # V2.1: 12→8
            elif close_position > 0.30:
                close_pos_score = 3                          # V2.1: 5→3
            else:
                close_pos_score = -8                         # V2.1: -5→-8

            # 上影线惩罚
            if shadow_ratio > 0.50:
                close_pos_score -= 15  # 冲高回落严重
            elif shadow_ratio > 0.35:
                close_pos_score -= 8
            elif shadow_ratio > 0.20:
                close_pos_score -= 3

        score += close_pos_score
        if close_pos_score >= 30:
            stat_close += 1

        # =========================================================================
        # 因子2: 连续放量 (-5~+20分)
        # =========================================================================
        vol_score = 0
        if t >= 3:
            vols_recent = vl[t - 2 : t + 1]
            if vols_recent[0] > 0 and vols_recent[1] > 0:
                vol_increasing = vols_recent[0] < vols_recent[1] < vols_recent[2]
                if vol_increasing:
                    vol_ratio_3d = vols_recent[2] / max(vols_recent[0], 1)
                    if vol_ratio_3d > 2.5:
                        vol_score = 20  # 强力放量
                    elif vol_ratio_3d > 1.8:
                        vol_score = 15
                    else:
                        vol_score = 10  # 温和放量
                else:
                    vol_score = 0
            # 缩量上涨 = 量价背离
            avg_5d = sum(vl[t - 5 : t]) / 5 if t >= 5 else 1
            if vl[t] < avg_5d * 0.7 and pct > 0:
                vol_score = -5
        elif t >= 1:
            prev_vol = vl[t - 1] if t > 0 else 1
            vol_ratio = vl[t] / max(prev_vol, 1)
            if vol_ratio > 2.0:
                vol_score = 15
            elif vol_ratio > 1.5:
                vol_score = 8

        score += vol_score
        if vol_score >= 10:
            stat_vol += 1

        # =========================================================================
        # 因子3: 均线斜率加速 (-10~+20分) — V2.1上调权重(原15)，趋势因子回测表现稳定
        # =========================================================================
        ma_score = 0
        if m5 and m10 and m20 and t >= 5:
            # MA5 斜率
            ma5_vals = []
            if len(cl) >= 5:
                for i in range(max(0, t - 4), t + 1):
                    seg = cl[max(0, i - 4) : i + 1]
                    if len(seg) == 5:
                        ma5_vals.append(sum(seg) / 5)

            if len(ma5_vals) >= 3:
                slope_now = ma5_vals[-1] - ma5_vals[-2]
                slope_prev = ma5_vals[-2] - ma5_vals[-3]
                if slope_now > 0 and slope_prev > 0:
                    if slope_now > slope_prev * 1.5:
                        ma_score = 20  # V2.1: 15→20 强加速
                    elif slope_now > slope_prev:
                        ma_score = 14  # V2.1: 10→14 温和加速
                    else:
                        ma_score = 8   # V2.1: 5→8 斜率放缓
                elif slope_now > 0 and slope_prev <= 0:
                    ma_score = 15  # V2.1: 12→15 拐头向上
                elif slope_now <= 0 and slope_prev > 0:
                    ma_score = -15  # V2.1: -10→-15 拐头向下
                elif slope_now <= 0:
                    ma_score = -8   # V2.1: -5→-8 持续下行

            # MA对齐加分
            if m5 > m10 > m20:
                ma_score += 3  # 多头排列小加分

        score += ma_score
        if ma_score >= 10:
            stat_ma += 1

        # =========================================================================
        # 因子4: 波动率收缩突破 (-5~+15分)
        # =========================================================================
        atr_score = 0
        if t >= 10:
            # 计算5日和10日ATR
            tr_list = []
            for i in range(max(1, t - 9), t + 1):
                tr = max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
                tr_list.append(tr)

            if len(tr_list) >= 5:
                atr_5 = sum(tr_list[-5:]) / 5
                atr_10 = sum(tr_list) / len(tr_list) if tr_list else 1

                if atr_10 > 0:
                    atr_ratio = atr_5 / atr_10
                    # ATR收缩后突破
                    if atr_ratio < 0.8 and cl[t] > hi[t - 1]:
                        atr_score = 15  # 波动率收缩+突破
                    elif atr_ratio < 0.9 and pct > 2:
                        atr_score = 10  # 轻度收缩+上涨
                    elif atr_ratio > 1.5:
                        atr_score = -5  # 波动过大
                    else:
                        atr_score = 0

        score += atr_score
        if atr_score >= 10:
            stat_atr += 1

        # =========================================================================
        # 因子5: 量价配合 (-10~+15分)
        # =========================================================================
        vp_score = 0
        if t >= 5:
            # 近5日量价关系
            up_days = 0
            down_days = 0
            vol_up_on_up = 0
            vol_up_on_down = 0
            for i in range(t - 4, t + 1):
                chg = cl[i] - op[i]
                if chg > 0:
                    up_days += 1
                    if i > 0 and vl[i] > vl[i - 1]:
                        vol_up_on_up += 1
                elif chg < 0:
                    down_days += 1
                    if i > 0 and vl[i] < vl[i - 1]:
                        vol_up_on_down += 1

            if up_days >= 3:
                if vol_up_on_up >= 2:
                    vp_score = 15  # 放量上涨
                elif vol_up_on_up >= 1:
                    vp_score = 10
            if down_days >= 2 and vol_up_on_down >= 1:
                vp_score = max(vp_score, 5)  # 缩量下跌=健康

            # 量价背离惩罚
            if pct > 3 and vl[t] < vl[t - 1] * 0.8:
                vp_score = -10  # 涨幅大但缩量=背离
            elif pct > 2 and vl[t] < vl[t - 1] * 0.9:
                vp_score = min(vp_score, -5)

        score += vp_score
        if vp_score >= 10:
            stat_vp += 1

        # =========================================================================
        # 因子6: 风险排除 (-15~-5分)
        # =========================================================================
        risk_score = 0

        # 连续涨停排除
        if t >= 2:
            limit_up_count = 0
            for i in range(t - 1, max(t - 4, -1), -1):
                day_pct = (cl[i] - op[i]) / op[i] * 100 if op[i] > 0 else 0
                if day_pct >= 9.5:
                    limit_up_count += 1
                else:
                    break
            if limit_up_count >= 2:
                risk_score -= 15  # 连板风险大
            elif limit_up_count >= 1:
                risk_score -= 5

        # 跳空缺口
        if t >= 1 and lo[t] > hi[t - 1]:
            gap = (lo[t] - hi[t - 1]) / hi[t - 1]
            if gap > 0.05:
                risk_score -= 10
            elif gap > 0.03:
                risk_score -= 5

        # 涨幅过大惩罚（回测验证: 涨幅>7%次日回调概率高）
        # V2.1: 全面加重惩罚力度
        if pct > 9.5:
            risk_score -= 40  # V2.1: 30→40 接近/达到涨停，次日大概率低开
        elif pct > 8:
            risk_score -= 20  # V2.1: 15→20 涨幅过大
        elif pct > 7:
            risk_score -= 12  # V2.1: 8→12 涨幅偏大
        elif pct > 5:
            risk_score -= 5   # V2.1新增: 5-7%也扣分 涨幅较大

        score += risk_score

        # =========================================================================
        # 因子7: 板块强度加分 (0~15分)
        # =========================================================================
        sector_bonus = 0
        if top_sectors:
            try:
                from modules.data_fetcher import _industry_cache as _ic
                cached = _ic.get(code)
                if isinstance(cached, dict):
                    stock_industry = cached.get("industry", "")
                    for sec_name, sec_chg in top_sectors.items():
                        if stock_industry and (stock_industry in sec_name or sec_name in stock_industry):
                            if sec_chg >= 3:
                                sector_bonus = 15
                            elif sec_chg >= 2:
                                sector_bonus = 10
                            elif sec_chg >= 1:
                                sector_bonus = 5
                            break
            except Exception:
                pass
        score += sector_bonus

        # =========================================================================
        # 因子11: 动量持续性 (-10~15分) — V2.3新增
        # 近5日累计涨幅适中(3-12%)且持续放量=动量可持续
        # 近5日涨幅过大(>15%)=透支，涨幅为负=弱势
        # =========================================================================
        mom_score = 0
        if t >= 5:
            chg_5d = (cl[t] - cl[t-5]) / cl[t-5] * 100 if cl[t-5] > 0 else 0
            if 3 <= chg_5d <= 12:
                mom_score = 15  # 温和上涨，动量可持续
                # 如果5日每一天都在涨，额外加分
                up_days_5d = sum(1 for i in range(t-4, t+1) if cl[i] > cl[i-1])
                if up_days_5d >= 4:
                    mom_score += 5  # 连续上涨=强动量
            elif 12 < chg_5d <= 20:
                mom_score = 5   # 涨幅偏大，风险增加
            elif chg_5d > 20:
                mom_score = -10 # 涨幅过大，透支
            elif chg_5d < -5:
                mom_score = -8  # 近5日下跌=弱势
        score += mom_score

        # =========================================================================
        # 因子8: 炸板惩罚 (-20分)
        # =========================================================================
        if _check_limit_break(kl, pct):
            score -= 20

        # =========================================================================
        # 因子9: 尾盘30分钟综合评分 (0~30分) — V2.2 增强版
        # 包含3个子信号(来自同一份5分钟K线数据):
        #   9a. 尾盘拉升斜率 (0~12分): 最后30分钟价格斜率
        #   9b. 尾盘成交量占比 (0~10分): 尾盘量占全天比例
        #   9c. 尾盘竞价动量 (0~8分): 最后5分钟的价量特征
        # =========================================================================
        tail_bonus = 0
        try:
            from modules.kline_fetcher import kline_fetcher
            mk = kline_fetcher.get_minute_kline(code, minute=5, count=18)
            if mk and len(mk) >= 6:
                tail_closes = [k["close"] for k in mk[-6:]]
                tail_vols = [k["volume"] for k in mk[-6:]]
                
                # 9a: 尾盘拉升斜率 (0~12分)
                if tail_closes[-1] > tail_closes[0]:
                    slope = (tail_closes[-1] - tail_closes[0]) / tail_closes[0]
                    if slope > 0.015: tail_bonus += 12
                    elif slope > 0.008: tail_bonus += 8
                    elif slope > 0.003: tail_bonus += 4
                
                # 9b: 尾盘成交量占比 (0~10分) — 尾盘量能占全天比例
                # 也需要获取日K线来算全天成交量
                try:
                    day_k = kline_fetcher.get_kline_raw(
                        f"sh{code}" if code.startswith("6") else f"sz{code}", 2
                    )
                    if day_k and day_k.get("data",{}).get("klines"):
                        last_day = day_k["data"]["klines"][-1]
                        parts = last_day.split(",")
                        if len(parts) >= 6:
                            day_vol = float(parts[5])  # 全天成交量(手)
                            tail_vol_sum = sum(tail_vols)  # 尾盘30分钟成交量
                            if day_vol > 0:
                                vol_ratio = tail_vol_sum / day_vol
                                if vol_ratio > 0.30: tail_bonus += 10  # 尾盘占比>30% = 尾盘异动
                                elif vol_ratio > 0.22: tail_bonus += 7
                                elif vol_ratio > 0.15: tail_bonus += 4
                                elif vol_ratio < 0.05: tail_bonus -= 5  # 尾盘极度缩量=异常
                except Exception:
                    pass
                
                # 9c: 尾盘竞价动量 (0~8分) — 最后5分钟放量+收高
                last_5min = mk[-1] if mk else None
                prev_5min = mk[-2] if len(mk) >= 2 else None
                if last_5min and prev_5min:
                    # 最后5分钟涨幅
                    if prev_5min["close"] > 0:
                        last_rise = (last_5min["close"] - prev_5min["close"]) / prev_5min["close"]
                        if last_rise > 0.003:
                            tail_bonus += 4  # 最后5分钟还在涨
                    # 最后5分钟放量
                    if prev_5min["volume"] > 0 and last_5min["volume"] > prev_5min["volume"] * 1.3:
                        tail_bonus += 4  # 最后5分钟明显放量
        except Exception:
            pass
        score += tail_bonus

        # =========================================================================
        # 因子10: 尾盘相对强度 (0~15分) — V2.2新增
        # 股票尾盘走势 vs 沪深300尾盘走势，跑赢指数=相对强势
        # =========================================================================
        rel_strength = 0
        try:
            # 获取沪深300的5分钟K线
            idx_mk = kline_fetcher.get_minute_kline("000300", minute=5, count=18)
            if idx_mk and len(idx_mk) >= 6 and mk and len(mk) >= 6:
                idx_tail_close = idx_mk[-1]["close"]
                idx_tail_open = idx_mk[-6]["close"]
                if idx_tail_open > 0:
                    idx_ret = (idx_tail_close - idx_tail_open) / idx_tail_open
                    stock_ret = (mk[-1]["close"] - mk[-6]["close"]) / mk[-6]["close"]
                    excess_ret = stock_ret - idx_ret  # 超额收益
                    if excess_ret > 0.01:
                        rel_strength = 15  # 尾盘跑赢指数1%以上
                    elif excess_ret > 0.005:
                        rel_strength = 10
                    elif excess_ret > 0.002:
                        rel_strength = 5
                    elif excess_ret < -0.01:
                        rel_strength = -10  # 尾盘跑输指数1%以上
        except Exception:
            pass
        score += rel_strength

        # 最低分数门槛
        if score < min_score:
            continue

        # 保留MA对齐显示字段
        ma_aligned = False
        if m5 and m10 and m20 and m60:
            ma_aligned = _ma_alignment_filter(m5, m10, m20, m60, cl[t])

        # 构建结果
        results.append(
            {
                "code": code,
                "name": s.get("f14", ""),
                "price": round(cl[t], 2),
                "ch": round(pct, 2),
                "amt": round(float(s.get("f6", 0)), 2),
                "cap": round(float(s.get("f21", 0)), 2),
                "vr": round(float(s.get("f10", 0)), 2),
                "to": round(float(s.get("f8", 0)), 2),
                "ma": f"{m5:.1f}>{m10:.1f}" if ma_aligned and m5 and m10 else f"{m5:.1f}/{m10:.1f}" if m5 and m10 else "-",
                "rsi": round(rsi, 1) if rsi is not None else 0,
                "macd": round(mc["macd"], 4) if mc and mc["macd"] is not None else 0,
                "score": round(score, 1),
                "tech_score": round(score, 1),
                "pe": round(float(s.get("f9", 0)), 1) if s.get("f9") and float(s.get("f9", 0)) > 0 else 0,
                "pb": round(float(s.get("f23", 0)), 2) if s.get("f23") and float(s.get("f23", 0)) > 0 else 0,
            }
        )

    filter_log.append({"n": "收盘位置(>=30)", "b": len(s1), "a": stat_close})
    filter_log.append({"n": "连续放量(>=10)", "b": len(s1), "a": stat_vol})
    filter_log.append({"n": "均线加速(>=10)", "b": len(s1), "a": stat_ma})
    filter_log.append({"n": "波动突破(>=10)", "b": len(s1), "a": stat_atr})
    filter_log.append({"n": "量价配合(>=10)", "b": len(s1), "a": stat_vp})
    filter_log.append({"n": f"达标(>={min_score})", "b": len(s1), "a": len(results)})

    return results


# ---------------------------------------------------------------------------
# 过滤条件
# ---------------------------------------------------------------------------

def _ma_alignment_filter(m5: float, m10: float, m20: float, m60: float, price: float) -> bool:
    """MA 多级对齐过滤

    - MA5 > MA10 > MA20 > MA60 = 强多头对齐（优先）
    - MA5 > MA10 > MA20 = 中等对齐
    - 价格应在 MA5 之上
    - MA5 < MA20 则跳过（空头）
    """
    # 空头排除
    if m5 < m20:
        return False
    # 价格应在 MA5 之上
    if price <= m5:
        return False
    # 强多头：MA5 > MA10 > MA20 > MA60
    if m5 > m10 > m20 > m60:
        return True
    # 中等对齐：MA5 > MA10 > MA20
    if m5 > m10 > m20:
        return True
    return False


def _ma_alignment_score(m5: float, m10: float, m20: float, m60: float, price: float) -> float:
    """MA 对齐评分 (最多 20 分)

    - 强多头 MA5>MA10>MA20>MA60 + 价格>MA5: 20 分
    - 中等对齐 MA5>MA10>MA20 + 价格>MA5: 12 分
    - 弱多头 MA5>MA20 但非完整对齐 + 价格>MA5: 5 分
    - 空头 (MA5<MA20) 或价格<MA5: 0 分
    """
    # 空头排除：不得分
    if m5 < m20:
        return 0
    # 价格应在 MA5 之上
    if price <= m5:
        return 0
    # 强多头：MA5 > MA10 > MA20 > MA60
    if m5 > m10 > m20 > m60:
        return 20
    # 中等对齐：MA5 > MA10 > MA20
    if m5 > m10 > m20:
        return 12
    # 弱多头：MA5 > MA20 但排列不完整
    return 5


def _volume_breakout_filter(vl: list[float], t: int, vol_mul: float) -> bool:
    """成交量突破过滤

    - 量比 > 1.5（显著放量）
    - 或成交量 > 1.5 * 5日平均成交量
    - 或量比 > 2.0（非常显著，即使价格走平）
    """
    if t < 5:
        return False

    current_vol = vl[t]
    prev_vol = vl[t - 1] if t > 0 else 1
    avg_5d = sum(vl[t - 5 : t]) / 5 if t >= 5 else 1

    # 量比近似（当日 / 前一日）
    vol_ratio = current_vol / max(prev_vol, 1)

    # 条件1：量比 > 1.5
    if vol_ratio > 1.5:
        return True

    # 条件2：成交量 > 1.5 * 5日平均
    if current_vol > vol_mul * avg_5d:
        return True

    # 条件3：量比 > 2.0（非常显著）
    if vol_ratio > 2.0:
        return True

    return False


def _volume_breakout_score(vl: list[float], t: int, vol_mul: float) -> float:
    """成交量突破评分 (最多 15 分)

    - 强放量：量比 > 2.0 或 成交量 > 2x 5日均量: 15 分
    - 中等放量：量比 > 1.5 或 成交量 > 1.5x 5日均量: 8 分
    - 无明显放量: 0 分
    """
    if t < 5:
        return 0

    current_vol = vl[t]
    prev_vol = vl[t - 1] if t > 0 else 1
    avg_5d = sum(vl[t - 5 : t]) / 5 if t >= 5 else 1

    vol_ratio = current_vol / max(prev_vol, 1)

    # 强放量
    if vol_ratio > 2.0 or current_vol > 2.0 * avg_5d:
        return 15
    # 中等放量
    if vol_ratio > 1.5 or current_vol > vol_mul * avg_5d:
        return 8

    return 0


def _rsi_score(rsi: float, rsi_lo: float = 40, rsi_hi: float = 75) -> float:
    """RSI 安全区评分 (最多 15 分)

    - 最优区 (50-65): 15 分
    - 扩展区 (40-50 或 65-75): 8 分
    - 安全区外: 0 分
    """
    if 50 <= rsi <= 65:
        return 15
    if rsi_lo <= rsi < 50 or 65 < rsi <= rsi_hi:
        return 8
    return 0


def _macd_score(mc: dict) -> float:
    """MACD 正向评分 (最多 10 分)

    - DIF > DEA: 5 分
    - MACD > 0: 5 分
    """
    pts = 0.0
    if mc["dif"] > mc["dea"]:
        pts += 5
    if mc["macd"] > 0:
        pts += 5
    return pts


# ---------------------------------------------------------------------------
# 评分函数
# ---------------------------------------------------------------------------

def _wp2_calc_score(stock: dict) -> float:
    """WP2 基础评分 (0-100) — 与 tech_score 不重叠的维度

    tech_score 已包含: 量能(量比)、RSI、MACD、MA对齐、突破、实体比
    base_score 应只包含 tech_score 没有的维度:
    - ch (涨跌幅): 25% — 日涨幅，tech_score 用 momentum_20/60
    - cap (市值): 25% — 市值偏好，tech_score 不涉及
    - amt (成交额): 25% — 绝对成交额，tech_score 只有量比
    - to (换手率): 25% — 活跃度，tech_score 不涉及
    """
    sc = 0
    ch = stock.get("ch", 0)
    cap = stock.get("cap", 0) / 1e8
    amt = stock.get("amt", 0) / 1e8  # 成交额(亿)
    to = stock.get("to", 0)          # 换手率

    # 涨跌幅评分 (0-25): 日涨幅 2-6% 最佳区间
    if 2 <= ch <= 6:
        sc += 25
    elif 1 <= ch < 2:
        sc += 20
    elif ch >= 1 or -2 <= ch < 0:
        sc += 15  # 微涨或小回调
    elif ch > 6:
        sc += 10  # 涨幅过大，谨慎
    else:
        sc += 5

    # 市值评分 (0-25): 30-500亿最佳区间（中盘股弹性好）
    if 30 <= cap <= 200:
        sc += 25
    elif 200 < cap <= 500:
        sc += 20
    elif 20 <= cap < 30 or 500 < cap <= 1000:
        sc += 15
    else:
        sc += 8

    # 成交额评分 (0-25): 日成交额 3-20亿 最佳（有足够流动性）
    if 3 <= amt <= 20:
        sc += 25
    elif 1 <= amt < 3:
        sc += 18  # 流动性偏低
    elif 20 < amt <= 50:
        sc += 15  # 流动性充足
    elif amt > 50:
        sc += 10  # 超大额，可能过热
    else:
        sc += 5

    # 换手率评分 (0-25): 1-5% 最佳活跃区间
    if 1 <= to <= 5:
        sc += 25
    elif 5 < to <= 10:
        sc += 20  # 高度活跃
    elif 0.5 <= to < 1:
        sc += 15  # 低活跃
    elif to > 10:
        sc += 8   # 过热
    else:
        sc += 5

    return min(sc, 100)


# ---------------------------------------------------------------------------
# 技术指标计算（旧版兼容）— 代理到 modules.technical.calc_*
# ---------------------------------------------------------------------------
from modules.technical import calc_ma, calc_ema, calc_rsi, calc_macd


def _wp2_calc_ma(prices: list[float], period: int) -> Optional[float]:
    """计算简单移动平均 (代理到 modules.technical.calc_ma)"""
    series = calc_ma(prices, period)
    return series[-1] if series else None


def _wp2_calc_ema(prices: list[float], period: int) -> Optional[float]:
    """计算指数移动平均 (代理到 modules.technical.calc_ema)"""
    series = calc_ema(prices, period)
    return series[-1] if series else None


def _wp2_calc_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """计算 RSI (代理到 modules.technical.calc_rsi)"""
    series = calc_rsi(prices, period)
    return series[-1] if series else None


def _wp2_calc_macd(prices: list[float]) -> Optional[dict]:
    """计算 MACD (代理到 modules.technical.calc_macd)"""
    result = calc_macd(prices)
    if not result or not result.get("dif"):
        return None
    return {
        "dif": result["dif"][-1] if result.get("dif") else None,
        "dea": result["dea"][-1] if result.get("dea") else None,
        "macd": result["macd"][-1] if result.get("macd") else None,
    }


# ---------------------------------------------------------------------------
# 数据获取函数
# ---------------------------------------------------------------------------

def _wp2_get_sina_quote(codes: list[str]) -> dict[str, dict]:
    """从新浪获取行情数据"""
    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
    try:
        r = session.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/",
            },
            timeout=15,
        )
        stocks: dict[str, dict] = {}
        pattern = r'var hq_(\w+)="(.*?)";'
        for match in re.finditer(pattern, r.text):
            raw_code = match.group(1)
            content = match.group(2)
            if not content:
                continue
            fields = content.split(",")
            if len(fields) < 32:
                continue
            try:
                price = float(fields[3])
                pre_close = float(fields[2])
                open_price = float(fields[1])
                high = float(fields[4])
                low = float(fields[5])
                volume = int(fields[8])
                amount = float(fields[9])
                pct_change = (
                    round((price - pre_close) / pre_close * 100, 2)
                    if pre_close > 0
                    else 0
                )
                # 从新浪变量名提取纯代码（去掉所有前缀如hq_/r_/str_/sh/sz）
                # 可能格式: hq_sh600519, r_sz000001, str_sh600519 等
                clean = raw_code
                for prefix in ("hq_", "r_", "str_", "s_"):
                    if clean.startswith(prefix):
                        clean = clean[len(prefix):]
                        break
                if clean.startswith("sh") or clean.startswith("sz"):
                    pure_code = clean[2:]
                else:
                    pure_code = clean
                market = "1" if "sh" in raw_code else "0"
                # 注意: 新浪API fields[30]=日期, fields[31]=时间, 不含换手率和PE
                # 换手率、PE、量比将从腾讯API补充
                turnover_rate = 0  # 将由腾讯API提供
                pe_val = 0  # 将由腾讯API提供
                vol_ratio = 0  # 将由腾讯API提供真实量比
                stocks[pure_code] = {
                    "f2": price,
                    "f3": pct_change,
                    "f4": round(price - pre_close, 2) if pre_close > 0 else 0,
                    "f5": volume,
                    "f6": amount,
                    "f7": (
                        round((high - low) / pre_close * 100, 2)
                        if pre_close > 0
                        else 0
                    ),
                    "f8": turnover_rate,
                    "f9": pe_val,
                    "f10": vol_ratio,
                    "f12": pure_code,
                    "f13": market,
                    "f14": fields[0],
                    "f15": high,
                    "f16": low,
                    "f17": open_price,
                    "f18": pre_close,
                    "f20": 0,
                    "f21": 0,
                    "f23": pe_val,
                }
            except (ValueError, IndexError):
                continue
        return stocks
    except Exception as e:
        log.warning(f"新浪行情获取失败: {e}")
        return {}


def _wp2_get_tencent_market_cap(codes: list[str]) -> dict[str, dict]:
    """从腾讯获取市值+PE+PB+换手率+量比数据，返回 dict[code -> {cap, pe, pb, turnover_rate, volume_ratio}]"""
    codes_str = ",".join(codes)
    url = f"https://qt.gtimg.cn/q={codes_str}"

    try:
        r = session.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/",
            },
            timeout=15,
        )
        result: dict[str, dict] = {}
        pattern = r'v_(\w+)="(.*?)";'
        for match in re.finditer(pattern, r.text):
            full_code = match.group(1)
            content = match.group(2)
            if not content:
                continue
            fields = content.split("~")
            pure_code = full_code[2:]
            try:
                circ_mv = float(fields[45]) if len(fields) > 45 and fields[45] else 0
                pe_val = float(fields[39]) if len(fields) > 39 and fields[39] and fields[39] != "-" else 0
                pb_val = float(fields[46]) if len(fields) > 46 and fields[46] and fields[46] != "-" else 0
                turnover_rate = float(fields[38]) if len(fields) > 38 and fields[38] and fields[38] != "-" else 0
                volume_ratio = float(fields[49]) if len(fields) > 49 and fields[49] and fields[49] != "-" else 0
                result[pure_code] = {
                    "cap": circ_mv, "pe": pe_val, "pb": pb_val,
                    "turnover_rate": turnover_rate, "volume_ratio": volume_ratio,
                }
            except (ValueError, IndexError):
                result[pure_code] = {"cap": 0, "pe": 0, "pb": 0, "turnover_rate": 0, "volume_ratio": 0}
        return result
    except Exception as e:
        log.warning(f"腾讯市值获取失败: {e}")
        return {}

