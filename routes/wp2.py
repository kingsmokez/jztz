"""Flask Blueprint - WP2选股路由

恢复旧版完整WP2选股逻辑，前端模板期望字段:
code, name, price, ch(涨幅%), amt(成交额), cap(市值), vr(量比), ma(MA排列), rsi, macd, score
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

from flask import Blueprint, render_template, request, jsonify

from modules.logger import log
from modules.data_fetcher import get_stock_industry

wp2_bp = Blueprint("wp2", __name__)

WP2_PICK_LOCK = threading.Lock()
WP2_PICK_DATA: dict = {}
WP2_PICK_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'wp2_pick_cache.json')


def _format_wp2_stocks(data) -> list[dict]:
    if not data:
        return []
    stocks = []
    for item in data:
        if not isinstance(item, dict):
            continue
        stock = {
            "code": item.get("code", ""),
            "name": item.get("name", ""),
            "price": item.get("price", 0),
            "ch": item.get("ch", item.get("change_pct", 0)),
            "amt": item.get("amt", item.get("amount", 0)),
            "cap": item.get("cap", item.get("market_cap", 0)),
            "vr": item.get("vr", item.get("volume_ratio", 0)),
            "ma": item.get("ma", ""),
            "rsi": item.get("rsi", 0),
            "macd": item.get("macd", 0),
            "score": item.get("score", 0),
            "pe": item.get("pe", 0),
            "pb": item.get("pb", 0),
            "industry": item.get("industry", ""),
            "sector": item.get("sector", ""),
            "buy_sell": item.get("buy_sell", None),
            "v5_score": item.get("v5_score", None),
            "v5_factors": item.get("v5_factors", None),
            "v5_reasons": item.get("v5_reasons", None),
            "v5_recommendation": item.get("v5_recommendation", None),
            "reasons": item.get("reasons", None),
        }
        stocks.append(stock)
    return stocks


def _load_wp2_cache():
    global WP2_PICK_DATA
    try:
        if os.path.exists(WP2_PICK_FILE):
            with open(WP2_PICK_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 兼容两种缓存格式：
                # 1. 字典格式: {"date": ..., "stocks": ...} (wp2.py保存)
                # 2. 列表格式: [{stock1}, ...] (web_app.py保存)
                if isinstance(data, list):
                    today = datetime.now().strftime('%Y-%m-%d')
                    # V5.5: 过滤ROE<0的亏损股
                    data = [r for r in data
                            if not (isinstance(r.get('roe'), (int, float)) and r.get('roe') < 0)]
                    WP2_PICK_DATA = {
                        "date": today,
                        "stocks": data,
                        "pick_time": datetime.now().strftime('%H:%M:%S'),
                        "last_update": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        "filter_stats": [],
                        "market_info": {},
                        "running": False,
                    }
                    return
                elif isinstance(data, dict) and data.get('date') == datetime.now().strftime('%Y-%m-%d'):
                    WP2_PICK_DATA = data
                    return
    except Exception as e:
        log.warning(f"加载WP2缓存失败: {e}")
    WP2_PICK_DATA = {
        "date": datetime.now().strftime('%Y-%m-%d'),
        "stocks": [],
        "pick_time": None,
        "last_update": None,
        "filter_stats": [],
        "market_info": {},
        "running": False,
    }


def _save_wp2_cache():
    try:
        with open(WP2_PICK_FILE, 'w', encoding='utf-8') as f:
            json.dump(WP2_PICK_DATA, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"保存WP2缓存失败: {e}")


_load_wp2_cache()


@wp2_bp.route("/wp2")
@wp2_bp.route("/wp2_pick")
def wp2_pick():
    return render_template("wp2_pick.html")


@wp2_bp.route("/api/wp2/data")
def api_wp2_data():
    try:
        from web_app import get_wp2_data
        data = get_wp2_data()
        if not data:
            return jsonify({"success": False, "error": "暂无WP2选股数据"}), 404
        return jsonify({"success": True, "data": data})
    except Exception as e:
        log.error(f"获取WP2数据失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": f"获取数据失败: {e}"})


@wp2_bp.route("/api/wp2_pick")
def api_wp2_pick():
    try:
        from web_app import get_wp2_data
        web_data = get_wp2_data()
        log.info(f"[WP2_DEBUG] get_wp2_data() = {type(web_data)} len={len(web_data) if web_data else 'None'}")
        if web_data:
            stocks = _format_wp2_stocks(web_data)
            now = datetime.now()
            return jsonify({
                "success": True,
                "stocks": stocks,
                "pick_time": now.strftime('%H:%M:%S'),
                "last_update": now.strftime('%Y-%m-%d %H:%M:%S'),
                "filter_stats": [],
                "market_info": {},
                "running": False,
                "progress": "",
                "date": now.strftime('%Y-%m-%d'),
            })
    except Exception as e:
        log.warning(f"[WP2_DEBUG] 获取web_app WP2数据失败: {e}", exc_info=True)

    with WP2_PICK_LOCK:
        data = dict(WP2_PICK_DATA) if WP2_PICK_DATA else {}

    return jsonify({
        "success": True,
        "stocks": data.get('stocks', []),
        "pick_time": data.get('pick_time'),
        "last_update": data.get('last_update'),
        "filter_stats": data.get('filter_stats', []),
        "market_info": data.get('market_info', {}),
        "running": data.get('running', False),
        "progress": data.get('progress', ''),
        "date": data.get('date'),
    })


@wp2_bp.route("/api/wp2_pick_run", methods=["POST"])
def api_wp2_pick_run():
    params = request.get_json(silent=True) or {}
    min_cap = params.get('min_cap', 30)
    max_cap = params.get('max_cap', 300)
    min_amt = params.get('min_amt', 3)
    vol_mul = params.get('vol_mul', 1.5)
    break_n = params.get('break_n', 20)
    body_r = params.get('body_r', 0.6)
    rsi_lo = params.get('rsi_lo', 40)
    rsi_hi = params.get('rsi_hi', 75)
    min_score = params.get('min_score', 25)

    def run_async():
        _execute_wp2_pick(min_cap, max_cap, min_amt, vol_mul, break_n, body_r, rsi_lo, rsi_hi, min_score)

    t = threading.Thread(target=run_async, daemon=True)
    t.start()

    return jsonify({"success": True, "message": "尾盘选股已启动"})


@wp2_bp.route("/api/wp2_pick_execute")
def api_wp2_pick_execute():
    try:
        _execute_wp2_pick()
        with WP2_PICK_LOCK:
            stocks = WP2_PICK_DATA.get('stocks', [])
        return jsonify({"success": True, "stocks": stocks})
    except Exception as e:
        log.error(f"wp2选股执行失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e), "stocks": []})


# === WP2选股核心逻辑 — 代理到 modules.technical.calc_* ===
from modules.technical import calc_ma, calc_ema, calc_rsi, calc_macd


def _wp2_calc_ma(prices, period):
    """简单移动平均 (代理到 modules.technical.calc_ma)"""
    series = calc_ma(prices, period)
    return series[-1] if series else None


def _wp2_calc_ema(prices, period):
    """指数移动平均 (代理到 modules.technical.calc_ema)"""
    series = calc_ema(prices, period)
    return series[-1] if series else None


def _wp2_calc_rsi(prices, period=14):
    """RSI (代理到 modules.technical.calc_rsi)"""
    series = calc_rsi(prices, period)
    return series[-1] if series else None


def _wp2_calc_macd(prices):
    """MACD (代理到 modules.technical.calc_macd)"""
    result = calc_macd(prices)
    if not result or not result.get("dif"):
        return None
    return {
        "dif": result["dif"][-1] if result.get("dif") else None,
        "dea": result["dea"][-1] if result.get("dea") else None,
        "macd": result["macd"][-1] if result.get("macd") else None,
    }


def _wp2_calc_score(stock):
    sc = 0
    vr = stock.get('vr', 0)
    ch = stock.get('ch', 0)
    rsi = stock.get('rsi', 0)
    cap = stock.get('cap', 0) / 1e8
    if vr >= 2.5:
        sc += 30
    elif vr >= 2:
        sc += 25
    elif vr >= 1.5:
        sc += 18
    else:
        sc += 10
    if 3 <= ch <= 6:
        sc += 25
    elif ch >= 2:
        sc += 20
    elif ch >= 1:
        sc += 15
    else:
        sc += 5
    if 55 <= rsi <= 65:
        sc += 25
    elif 50 <= rsi <= 70:
        sc += 20
    else:
        sc += 10
    if 50 <= cap <= 200:
        sc += 20
    elif 30 <= cap <= 300:
        sc += 15
    else:
        sc += 8
    return min(sc, 100)


def _ma_alignment_filter(m5, m10, m20, m60, price):
    """MA 多级对齐过滤（布尔版，用于 ma 显示字段）"""
    if m5 < m20:
        return False
    if price <= m5:
        return False
    if m5 > m10 > m20 > m60:
        return True
    if m5 > m10 > m20:
        return True
    return False


def _ma_alignment_score(m5, m10, m20, m60, price):
    """MA 对齐评分 (最多 20 分)"""
    if m5 < m20:
        return 0
    if price <= m5:
        return 0
    if m5 > m10 > m20 > m60:
        return 20
    if m5 > m10 > m20:
        return 12
    return 5


def _volume_breakout_score(vl, t, vol_mul):
    """成交量突破评分 (最多 15 分)"""
    if t < 5:
        return 0
    current_vol = vl[t]
    prev_vol = vl[t - 1] if t > 0 else 1
    avg_5d = sum(vl[t - 5:t]) / 5 if t >= 5 else 1
    vol_ratio = current_vol / max(prev_vol, 1)
    if vol_ratio > 2.0 or current_vol > 2.0 * avg_5d:
        return 15
    if vol_ratio > 1.5 or current_vol > vol_mul * avg_5d:
        return 8
    return 0


def _rsi_score(rsi, rsi_lo=40, rsi_hi=75):
    """RSI 安全区评分 (最多 15 分)"""
    if 50 <= rsi <= 65:
        return 15
    if rsi_lo <= rsi < 50 or 65 < rsi <= rsi_hi:
        return 8
    return 0


def _macd_score(mc):
    """MACD 正向评分 (最多 10 分)"""
    pts = 0
    if mc['dif'] > mc['dea']:
        pts += 5
    if mc['macd'] > 0:
        pts += 5
    return pts


def _wp2_get_tencent_quote(codes):
    from modules.http_client import session
    all_stocks = {}
    batch_size = 50
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        url = f"https://qt.gtimg.cn/q={','.join(batch)}"
        try:
            r = session.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'}, timeout=15)
            for line in r.text.strip().split(';'):
                if not line.strip() or '~' not in line:
                    continue
                parts = line.split('~')
                if len(parts) < 48:
                    continue
                try:
                    pure_code = parts[2]
                    market = parts[0].split('=')[0].replace('v_', '').replace('s_', '').replace('v', '').replace('s', '')
                    market_num = '1' if pure_code.startswith('6') else '0'
                    price = float(parts[3]) if parts[3] else 0
                    pre_close = float(parts[4]) if parts[4] else 0
                    open_price = float(parts[5]) if parts[5] else 0
                    volume = float(parts[6]) if parts[6] else 0
                    amount_val = float(parts[37]) if len(parts) > 37 and parts[37] else 0
                    high = float(parts[33]) if len(parts) > 33 and parts[33] else 0
                    low = float(parts[34]) if len(parts) > 34 and parts[34] else 0
                    pct_change = float(parts[32]) if len(parts) > 32 and parts[32] else 0
                    turnover_rate = float(parts[38]) if len(parts) > 38 and parts[38] else 0
                    pb_val = float(parts[46]) if len(parts) > 46 and parts[46] and parts[46] != '-' else 0
                    pe_val = float(parts[39]) if len(parts) > 39 and parts[39] else 0
                    circ_mv = float(parts[45]) if len(parts) > 45 and parts[45] else 0
                    total_mv = float(parts[44]) if len(parts) > 44 and parts[44] else 0
                    vol_ratio = float(parts[49]) if len(parts) > 49 and parts[49] else 0
                    name = parts[1]

                    if price <= 0:
                        continue

                    all_stocks[pure_code] = {
                        'f2': price, 'f3': pct_change, 'f4': round(price - pre_close, 2),
                        'f5': volume, 'f6': amount_val * 1e4, 'f7': round((high - low) / pre_close * 100, 2) if pre_close > 0 else 0,
                        'f8': turnover_rate, 'f9': pe_val, 'f10': vol_ratio,
                        'f12': pure_code, 'f13': market_num, 'f14': name,
                        'f15': high, 'f16': low, 'f17': open_price, 'f18': pre_close,
                        'f20': total_mv * 1e8, 'f21': circ_mv * 1e8, 'f23': pb_val,
                    }
                except (ValueError, IndexError, NameError) as e:
                    log.debug(f"腾讯行情解析失败({pure_code if 'pure_code' in dir() else '?'}): {e}")
                    continue
        except Exception as e:
            log.warning(f"腾讯行情获取失败: {e}")
            continue
    return all_stocks


def _wp2_get_tencent_market_cap(codes):
    from modules.http_client import session
    url = f"https://qt.gtimg.cn/q={','.join(codes)}"
    try:
        r = session.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'}, timeout=15)
        caps = {}
        for line in r.text.strip().split(';'):
            if not line.strip() or '~' not in line:
                continue
            parts = line.split('~')
            if len(parts) < 46:
                continue
            pure_code = parts[2] if len(parts) > 2 else ""
            try:
                circ_mv = float(parts[45]) if parts[45] else 0
                caps[pure_code] = circ_mv
            except (ValueError, IndexError):
                if pure_code:
                    caps[pure_code] = 0
        return caps
    except Exception as e:
        log.warning(f"腾讯市值获取失败: {e}")
        return {}


def _execute_wp2_pick(min_cap=30, max_cap=500, min_amt=3, vol_mul=1.5, break_n=20, body_r=0.6, rsi_lo=40, rsi_hi=75, min_score=30):
    global WP2_PICK_DATA
    filter_log = []

    with WP2_PICK_LOCK:
        WP2_PICK_DATA['running'] = True

    try:
        now = datetime.now()
        log.info(f"执行尾盘强势股选股... {now.strftime('%H:%M:%S')}")

        from modules.http_client import session, EM_HEADERS

        market_info = {}
        try:
            ir = session.get('https://push2.eastmoney.com/api/qt/stock/get',
                             params={'secid': '1.000300', 'fields': 'f43,f60,f170'},
                             headers=EM_HEADERS, timeout=10)
            if ir.status_code == 200:
                idata = ir.json().get('data', {})
                if idata:
                    market_info = {
                        'idx_price': idata.get('f43', 0),
                        'idx_open': idata.get('f60', 0),
                        'idx_change': idata.get('f170', 0),
                    }
        except Exception:
            pass

        log.info("获取股票代码列表...")
        try:
            import akshare as ak
            code_df = ak.stock_info_a_code_name()
        except Exception as e:
            log.error(f"akshare获取失败: {e}")
            with WP2_PICK_LOCK:
                WP2_PICK_DATA['stocks'] = []
                WP2_PICK_DATA['pick_time'] = now.strftime('%H:%M:%S')
                WP2_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
                WP2_PICK_DATA['filter_stats'] = [{'n': 'akshare', 'b': 0, 'a': 0}]
                WP2_PICK_DATA['market_info'] = market_info
                WP2_PICK_DATA['running'] = False
                _save_wp2_cache()
            return

        log.info(f"共 {len(code_df)} 只股票")

        tencent_codes = []
        for _, row in code_df.iterrows():
            code = row['code']
            if code.startswith('688') or code.startswith('8') or code.startswith('4'):
                continue
            tencent_codes.append(f'sh{code}' if code.startswith('6') else f'sz{code}')

        all_data = {}
        batch_size = 50
        for i in range(0, len(tencent_codes), batch_size):
            batch = tencent_codes[i:i + batch_size]
            batch_data = _wp2_get_tencent_quote(batch)
            all_data.update(batch_data)
            time.sleep(0.3)

        all_stocks = list(all_data.values())
        log.info(f"获取行情数据 {len(all_stocks)} 只")

        s1 = []
        for s in all_stocks:
            c = str(s.get('f12', ''))
            n = str(s.get('f14', ''))
            if c.startswith('688') or c.startswith('8') or c.startswith('4'):
                continue
            if 'ST' in n or '退' in n or '*' in n:
                continue
            cap = float(s.get('f21', 0))
            if not cap or cap < min_cap * 1e8 or cap > max_cap * 1e8:
                continue
            amt = float(s.get('f6', 0))
            if not amt or amt < min_amt * 1e8:
                continue
            if not s.get('f2') or s.get('f2') == '-':
                continue
            s1.append(s)
        filter_log.append({'n': '基础过滤', 'b': len(all_stocks), 'a': len(s1)})
        log.info(f"第1层 基础过滤: {len(all_stocks)}→{len(s1)}")

        if not s1:
            with WP2_PICK_LOCK:
                WP2_PICK_DATA['stocks'] = []
                WP2_PICK_DATA['pick_time'] = now.strftime('%H:%M:%S')
                WP2_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
                WP2_PICK_DATA['filter_stats'] = filter_log
                WP2_PICK_DATA['market_info'] = market_info
                WP2_PICK_DATA['running'] = False
                _save_wp2_cache()
            return

        s1.sort(key=lambda x: float(x.get('f6', 0)), reverse=True)

        log.info(f"获取 {len(s1)} 只K线...")
        kline_data = {}
        from modules.kline_fetcher import kline_fetcher
        for i in range(0, len(s1), 50):
            batch = s1[i:i + 50]
            for s in batch:
                code = str(s.get('f12', ''))
                market = str(s.get('f13', '1'))
                symbol = f'sh{code}' if market == '1' else f'sz{code}'
                kd = kline_fetcher.get_kline_raw(symbol, 120)
                if kd:
                    kline_data[code] = kd
                time.sleep(0.1)

        log.info(f"K线获取完成: {len(kline_data)}/{len(s1)} 只成功, 源状态={kline_fetcher.health_status()}")

        stat_close, stat_vol, stat_ma, stat_atr, stat_vp = 0, 0, 0, 0, 0
        results = []

        for s in s1:
            code = str(s.get('f12', ''))
            kd = kline_data.get(code)
            if not kd or not kd.get('data') or not kd.get('data', {}).get('klines') or len(kd['data']['klines']) < 15:
                continue

            kl = []
            for line in kd['data']['klines']:
                parts = line.split(',')
                if len(parts) >= 6:
                    kl.append({'o': float(parts[1]), 'c': float(parts[2]), 'h': float(parts[3]), 'lo': float(parts[4]), 'v': float(parts[5])})
            kl = [k for k in kl if k['c'] > 0]
            if len(kl) < 15:
                continue

            cl = [k['c'] for k in kl]
            hi = [k['h'] for k in kl]
            lo = [k['lo'] for k in kl]
            vl = [k['v'] for k in kl]
            op = [k['o'] for k in kl]
            t = len(kl) - 1
            pct = float(s.get('f3', 0))

            m5 = _wp2_calc_ma(cl, 5)
            m10 = _wp2_calc_ma(cl, 10)
            m20 = _wp2_calc_ma(cl, 20)
            m60 = _wp2_calc_ma(cl, 60)
            rsi = _wp2_calc_rsi(cl)
            mc = _wp2_calc_macd(cl)

            score = 0.0

            # --- 因子1: 收盘位置 (0~40分) ---
            day_range = hi[t] - lo[t]
            close_pos_score = 0
            if day_range > 0:
                close_position = (cl[t] - lo[t]) / day_range
                upper_shadow = hi[t] - cl[t]
                shadow_ratio = upper_shadow / day_range
                is_limit_up = pct >= 9.5

                if close_position > 0.95 and shadow_ratio < 0.03:
                    close_pos_score = 25 if is_limit_up else 40
                elif close_position > 0.90 and shadow_ratio < 0.06:
                    close_pos_score = 20 if is_limit_up else 35
                elif close_position > 0.85 and shadow_ratio < 0.10:
                    close_pos_score = 18 if is_limit_up else 30
                elif close_position > 0.70 and shadow_ratio < 0.20:
                    close_pos_score = 15 if is_limit_up else 22
                elif close_position > 0.50:
                    close_pos_score = 10 if is_limit_up else 12
                elif close_position > 0.30:
                    close_pos_score = 5
                else:
                    close_pos_score = -5

                if shadow_ratio > 0.50:
                    close_pos_score -= 15
                elif shadow_ratio > 0.35:
                    close_pos_score -= 8
                elif shadow_ratio > 0.20:
                    close_pos_score -= 3

            score += close_pos_score
            if close_pos_score >= 30:
                stat_close += 1

            # --- 因子2: 连续放量 (-5~+20分) ---
            vol_score = 0
            if t >= 3:
                vols_recent = vl[t - 2:t + 1]
                if vols_recent[0] > 0 and vols_recent[1] > 0:
                    vol_increasing = vols_recent[0] < vols_recent[1] < vols_recent[2]
                    if vol_increasing:
                        vol_ratio_3d = vols_recent[2] / max(vols_recent[0], 1)
                        if vol_ratio_3d > 2.5:
                            vol_score = 20
                        elif vol_ratio_3d > 1.8:
                            vol_score = 15
                        else:
                            vol_score = 10
                avg_5d = sum(vl[t - 5:t]) / 5 if t >= 5 else 1
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

            # --- 因子3: 均线斜率加速 (-10~+15分) ---
            ma_score = 0
            if m5 and m10 and m20 and t >= 5:
                ma5_vals = []
                if len(cl) >= 5:
                    for i in range(max(0, t - 4), t + 1):
                        seg = cl[max(0, i - 4):i + 1]
                        if len(seg) == 5:
                            ma5_vals.append(sum(seg) / 5)
                if len(ma5_vals) >= 3:
                    slope_now = ma5_vals[-1] - ma5_vals[-2]
                    slope_prev = ma5_vals[-2] - ma5_vals[-3]
                    if slope_now > 0 and slope_prev > 0:
                        if slope_now > slope_prev * 1.5:
                            ma_score = 15
                        elif slope_now > slope_prev:
                            ma_score = 10
                        else:
                            ma_score = 5
                    elif slope_now > 0 and slope_prev <= 0:
                        ma_score = 12
                    elif slope_now <= 0 and slope_prev > 0:
                        ma_score = -10
                    elif slope_now <= 0:
                        ma_score = -5
                if m5 > m10 > m20:
                    ma_score += 3

            score += ma_score
            if ma_score >= 10:
                stat_ma += 1

            # --- 因子4: 波动率收缩突破 (-5~+15分) ---
            atr_score = 0
            if t >= 10:
                tr_list = []
                for i in range(max(1, t - 9), t + 1):
                    tr = max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
                    tr_list.append(tr)
                if len(tr_list) >= 5:
                    atr_5 = sum(tr_list[-5:]) / 5
                    atr_10 = sum(tr_list) / len(tr_list) if tr_list else 1
                    if atr_10 > 0:
                        atr_ratio = atr_5 / atr_10
                        if atr_ratio < 0.8 and cl[t] > hi[t - 1]:
                            atr_score = 15
                        elif atr_ratio < 0.9 and pct > 2:
                            atr_score = 10
                        elif atr_ratio > 1.5:
                            atr_score = -5

            score += atr_score
            if atr_score >= 10:
                stat_atr += 1

            # --- 因子5: 量价配合 (-10~+15分) ---
            vp_score = 0
            if t >= 5:
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
                        vp_score = 15
                    elif vol_up_on_up >= 1:
                        vp_score = 10
                if down_days >= 2 and vol_up_on_down >= 1:
                    vp_score = max(vp_score, 5)
                if pct > 3 and vl[t] < vl[t - 1] * 0.8:
                    vp_score = -10
                elif pct > 2 and vl[t] < vl[t - 1] * 0.9:
                    vp_score = min(vp_score, -5)

            score += vp_score
            if vp_score >= 10:
                stat_vp += 1

            # --- 因子6: 风险排除 (-30~-5分) ---
            risk_score = 0
            if t >= 2:
                limit_up_count = 0
                for i in range(t - 1, max(t - 4, -1), -1):
                    day_pct = (cl[i] - op[i]) / op[i] * 100 if op[i] > 0 else 0
                    if day_pct >= 9.5:
                        limit_up_count += 1
                    else:
                        break
                if limit_up_count >= 2:
                    risk_score -= 15
                elif limit_up_count >= 1:
                    risk_score -= 5
            if t >= 1 and lo[t] > hi[t - 1]:
                gap = (lo[t] - hi[t - 1]) / hi[t - 1]
                if gap > 0.05:
                    risk_score -= 10
                elif gap > 0.03:
                    risk_score -= 5
            if pct > 9.5:
                risk_score -= 30
            elif pct > 8:
                risk_score -= 15
            elif pct > 7:
                risk_score -= 8

            score += risk_score

            # 最低分数门槛
            if score < min_score:
                continue

            # 保留MA对齐显示字段
            ma_aligned = False
            if m5 and m10 and m20 and m60:
                ma_aligned = _ma_alignment_filter(m5, m10, m20, m60, cl[t])

            results.append({
                'code': code,
                'name': s.get('f14', ''),
                'price': round(cl[t], 2),
                'ch': round(pct, 2),
                'amt': float(s.get('f6', 0)),
                'cap': float(s.get('f21', 0)),
                'vr': float(s.get('f10', 0)),
                'to': float(s.get('f8', 0)),
                'ma': f"{m5:.1f}>{m10:.1f}" if ma_aligned and m5 and m10 else f"{m5:.1f}/{m10:.1f}" if m5 and m10 else "-",
                'rsi': round(rsi, 1) if rsi is not None else 0,
                'macd': round(mc['macd'], 4) if mc and mc['macd'] is not None else 0,
                'score': round(score, 1),
                'tech_score': round(score, 1),
                'pe': round(float(s.get('f9', 0)), 1) if s.get('f9') and float(s.get('f9', 0)) > 0 else 0,
                'pb': round(float(s.get('f23', 0)), 2) if s.get('f23') and float(s.get('f23', 0)) > 0 else 0,
            })

        filter_log.append({'n': '收盘位置(>=30)', 'b': len(s1), 'a': stat_close})
        filter_log.append({'n': '连续放量(>=10)', 'b': len(s1), 'a': stat_vol})
        filter_log.append({'n': '均线加速(>=10)', 'b': len(s1), 'a': stat_ma})
        filter_log.append({'n': '波动突破(>=10)', 'b': len(s1), 'a': stat_atr})
        filter_log.append({'n': '量价配合(>=10)', 'b': len(s1), 'a': stat_vp})
        filter_log.append({'n': f'达标(>={min_score})', 'b': len(s1), 'a': len(results)})

        results.sort(key=lambda x: x['score'], reverse=True)
        final = results[:20]

        # 为最终选股结果补全 industry 字段（前端模板有期望，但 picker 内部未填值）
        def _attach_industry(item):
            try:
                info = get_stock_industry(item.get('code', ''))
                item['industry'] = info.get('industry', '')
                item['sector'] = info.get('sector_type', 'default')
            except Exception:
                item.setdefault('industry', '')
                item.setdefault('sector', 'default')
            return item
        with ThreadPoolExecutor(max_workers=8) as _exec:
            list(_exec.map(_attach_industry, final))


        # Add buy/sell points and V5 evaluation
        try:
            from modules.scoring import calculate_buy_sell, multi_factor_evaluate
            from modules.technical import calculate_technical_indicators
            for stock in final:
                try:
                    code = stock.get('code', '')
                    tech = calculate_technical_indicators(code, days=30) if code else None
                    stock_for_bs = {
                        'code': code, 'name': stock.get('name', ''),
                        'price': stock.get('price', 0),
                    'pe': stock.get('pe', 0), 'pb': stock.get('pb', 0),
                        'market_cap': stock.get('cap', 0),
                        'turnover_rate': stock.get('to', stock.get('turnover_rate', 0)),
                        'change_pct': stock.get('ch', 0),
                    }
                    # Fetch financial data for proper V5 evaluation
                    try:
                        from modules.data_fetcher import get_financial_data
                        fin_map = get_financial_data([code])
                        fin = fin_map.get(code)
                        if fin:
                            stock_for_bs['roe'] = fin.roe
                            stock['roe'] = fin.roe  # V5.5: 保存ROE供后续过滤
                            stock_for_bs['gross_margin'] = fin.gross_margin
                            stock_for_bs['net_margin'] = fin.net_margin
                            stock_for_bs['rev_growth'] = fin.revenue_growth
                            stock_for_bs['profit_growth'] = fin.profit_growth
                            stock_for_bs['debt_ratio'] = fin.debt_ratio
                    except Exception:
                        pass
                    bs = calculate_buy_sell(stock_for_bs, stock.get('score', 50), tech_data=tech)
                    if bs:
                        stock['buy_sell'] = bs
                    # V5 multi-factor evaluation
                    try:
                        v5_result = multi_factor_evaluate(stock_for_bs)
                        if v5_result:
                            stock['v5_score'] = v5_result.get('v5_total') or v5_result.get('total_score')
                            stock['v5_factors'] = v5_result.get('v5_factors') or v5_result.get('factors')
                            stock['v5_reasons'] = v5_result.get('v5_reasons') or v5_result.get('reasons')
                            stock['v5_recommendation'] = v5_result.get('v5_recommendation') or v5_result.get('recommendation')
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception as e:
            log.warning(f'WP2 buy_sell计算失败: {e}')

        # V5.5: 过滤ROE<0的亏损股
        pre_filter_count = len(final)
        final = [s for s in final
                 if not (isinstance(s.get('roe'), (int, float)) and s.get('roe') < 0)]
        if len(final) < pre_filter_count:
            log.info(f'WP2 ROE过滤: {pre_filter_count} -> {len(final)} (移除{pre_filter_count - len(final)}只亏损股)')

        log.info(f"尾盘选股完成: {len(final)} 只")

        with WP2_PICK_LOCK:
            WP2_PICK_DATA['date'] = now.strftime('%Y-%m-%d')
            WP2_PICK_DATA['stocks'] = final
            WP2_PICK_DATA['pick_time'] = now.strftime('%H:%M:%S')
            WP2_PICK_DATA['last_update'] = now.strftime('%Y-%m-%d %H:%M:%S')
            WP2_PICK_DATA['filter_stats'] = filter_log
            WP2_PICK_DATA['market_info'] = market_info
            WP2_PICK_DATA['running'] = False
            _save_wp2_cache()

    except Exception as e:
        log.error(f"尾盘选股失败: {e}", exc_info=True)
        with WP2_PICK_LOCK:
            WP2_PICK_DATA['running'] = False
            _save_wp2_cache()
