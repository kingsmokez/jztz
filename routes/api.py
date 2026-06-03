"""Flask Blueprint - 通用API路由 (搜索/登录/市场/新闻/选股/详情等)"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Optional

from flask import Blueprint, request, render_template, jsonify

from modules.api_response import api_success, api_error
from modules.logger import log

api_bp = Blueprint("api", __name__)


@api_bp.route("/login")
def login_page():
    return render_template("login.html")


@api_bp.route("/cb_arbitrage")
def cb_arbitrage_page():
    return render_template("cb_arbitrage.html")


@api_bp.route("/auction_compare")
def auction_compare_page():
    return render_template("auction_compare.html")


@api_bp.route("/api/search", methods=["POST"])
def api_search():
    try:
        data = request.get_json(silent=True)
        if not data:
            return api_error("请求体不能为空")
        keyword = data.get("keyword", "").strip()
        if not keyword:
            return api_error("搜索关键词不能为空")
        from web_app import api_search_stock
        result = api_search_stock(keyword)
        return api_success(result)
    except Exception as e:
        log.error(f"搜索失败: {e}", exc_info=True)
        return api_error(f"搜索失败: {e}")


@api_bp.route("/api/search_stock")
def api_search_stock_get():
    """搜索全市场股票（支持名称或代码模糊匹配）- GET版本"""
    query = request.args.get("q", "").strip()
    if not query or len(query) < 1:
        return jsonify({"success": False, "error": "请输入搜索关键词"}), 400

    try:
        from modules.http_client import session, HEADERS, EM_HEADERS, DC_HEADERS
        from modules.data_fetcher import get_financial_data
        from modules.scoring import evaluate_stock
        from modules.config import LIQUOR_NAMES, BANK_CODES, BASE_DIR
        import os

        matched_stocks = []

        # 1. 名称搜索：东方财富smartbox
        if not query.isdigit() or len(query) >= 2:
            try:
                smartbox_url = "https://searchapi.eastmoney.com/api/suggest/get"
                smartbox_params = {
                    "input": query,
                    "type": "14",
                    "token": os.environ.get("SMARTBOX_TOKEN", ""),
                    "count": 10,
                }
                resp = session.get(smartbox_url, params=smartbox_params,
                                   headers={"User-Agent": "Mozilla/5.0", "Referer": "https://so.eastmoney.com/"},
                                   timeout=10)
                data = resp.json()
                if data.get("QuotationCodeTable") and data["QuotationCodeTable"].get("Data"):
                    for item in data["QuotationCodeTable"]["Data"][:10]:
                        try:
                            code = str(item.get("Code", ""))
                            name = item.get("Name", "")
                            classify = item.get("Classify", "")
                            if not code or not name or classify != "AStock":
                                continue
                            if "ST" in name or "*" in name:
                                continue
                            if code.startswith('8') or code.startswith('4') or code.startswith('920'):
                                continue
                            matched_stocks.append({"code": code, "name": name})
                        except Exception:
                            continue
            except Exception as e:
                log.warning(f"Smartbox搜索失败: {e}")

        # 2. 代码搜索
        if query.isdigit():
            code = query.zfill(6)
            if code not in [s["code"] for s in matched_stocks]:
                matched_stocks.append({"code": code, "name": ""})

        # 3. 批量获取行情
        if matched_stocks:
            tx_codes = []
            for s in matched_stocks:
                c = s["code"]
                tx_codes.append(f"sh{c}" if c.startswith('6') else f"sz{c}")

            for i in range(0, len(tx_codes), 80):
                batch = tx_codes[i:i + 80]
                try:
                    url = 'http://qt.gtimg.cn/q=' + ','.join(batch)
                    resp = session.get(url, timeout=15)
                    lines = resp.text.strip().split(';')
                    for line in lines:
                        if not line.strip():
                            continue
                        parts = line.split('~')
                        if len(parts) < 50:
                            continue
                        code = parts[2]
                        if not code or len(code) != 6:
                            continue
                        try:
                            price = float(parts[3]) if parts[3] else 0
                        except (ValueError, TypeError):
                            price = 0
                        if price <= 0:
                            continue
                        try:
                            change_pct = float(parts[32]) if parts[32] else 0
                        except (ValueError, TypeError):
                            change_pct = 0
                        try:
                            pe = float(parts[39]) if parts[39] and parts[39] != '-' else 0
                            if pe > 10000 or pe < 0:
                                pe = 0
                        except (ValueError, TypeError):
                            pe = 0
                        try:
                            total_cap_yi = float(parts[44]) if parts[44] else 0
                        except (ValueError, TypeError):
                            total_cap_yi = 0
                        try:
                            high = float(parts[33]) if parts[33] else 0
                        except (ValueError, TypeError):
                            high = 0
                        try:
                            low = float(parts[34]) if parts[34] else 0
                        except (ValueError, TypeError):
                            low = 0
                        try:
                            open_p = float(parts[5]) if parts[5] else 0
                        except (ValueError, TypeError):
                            open_p = 0
                        try:
                            prev_close = float(parts[4]) if parts[4] else 0
                        except (ValueError, TypeError):
                            prev_close = 0
                        try:
                            volume_gu = float(parts[37]) if parts[37] else 0
                        except (ValueError, TypeError):
                            volume_gu = 0
                        try:
                            amount_wan = float(parts[43]) if parts[43] else 0
                        except (ValueError, TypeError):
                            amount_wan = 0
                        try:
                            pb = float(parts[46]) if parts[46] and parts[46] != '-' else 0
                        except (ValueError, TypeError):
                            pb = 0

                        for ms in matched_stocks:
                            if ms["code"] == code:
                                ms.update({
                                    "name": parts[1] or ms["name"],
                                    "price": price, "change_pct": change_pct,
                                    "volume": volume_gu, "amount": amount_wan * 10000,
                                    "market_cap": total_cap_yi,
                                    "pe": pe, "pb": pb,
                                    "high": high, "low": low, "open": open_p, "prev_close": prev_close,
                                })
                                break
                except Exception as e:
                    log.warning(f"腾讯API搜索行情失败: {e}")

        matched_stocks = [s for s in matched_stocks if s.get("price", 0) > 0]

        # 4. 评分
        results = []
        for stock in matched_stocks:
            r = evaluate_stock(stock)
            if r:
                results.append(r)
            else:
                results.append({
                    "code": stock["code"], "name": stock["name"],
                    "price": stock.get("price", 0), "change_pct": stock.get("change_pct", 0),
                    "pe": stock.get("pe", 0), "pb": stock.get("pb", 0),
                    "roe": stock.get("roe", 0), "gross_margin": stock.get("gross_margin", 0),
                    "net_margin": stock.get("net_margin", 0),
                    "rev_growth": stock.get("rev_growth", 0), "profit_growth": stock.get("profit_growth", 0),
                    "market_cap": stock.get("market_cap", 0),
                    "score": 0,
                    "dimensions": {"profitability": 0, "growth": 0, "health": 0, "valuation": 0, "cashflow": 0},
                    "reasons": [], "buy_sell": None,
                })

        results.sort(key=lambda x: x["score"], reverse=True)

        # 5. 行业信息
        from modules.data_fetcher import get_stock_industry

        def fetch_industry(stock):
            try:
                info = get_stock_industry(stock['code'])
                stock['industry'] = info.get('industry', '未知')
            except Exception:
                stock['industry'] = '未知'

        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(fetch_industry, results))

        return jsonify({
            "success": True,
            "query": query,
            "results": results,
            "total_matched": len(matched_stocks),
        })
    except Exception as e:
        log.error(f"搜索股票失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/api/pick_v5")
def api_pick_v5():
    """执行选股并返回v5多因子评分结果（按v5评分排序）

    返回每个股票的 v5_total, v5_factors(value/quality/growth/momentum/sentiment)
    以及旧评分作为对比
    """
    try:
        from modules.stock_picker import run_picker
        from modules.data_fetcher import get_preset_financials

        log.info("收到v5选股请求，开始执行...")
        results = run_picker()
        total = results[0].get('_total_scanned', len(get_preset_financials())) if results else len(get_preset_financials())

        # 按v5评分排序
        results.sort(key=lambda x: x.get('v5_score', 0), reverse=True)

        # 精选Top 5 + 完整列表Top 50
        top5 = results[:5]
        full_list = results[:50]

        # 清理内部字段
        for r in top5 + full_list:
            r.pop('_total_scanned', None)
            r.pop('_final_score', None)
            r.pop('_v5_final', None)
            r.pop('_is_hot', None)
            r.pop('_hot_sector', None)
            r['score'] = round(r.get('score', 0), 1)
            r['v5_score'] = round(r.get('v5_score', 0), 2)

        return jsonify({
            "success": True,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_scanned": total,
            "sort_by": "v5_multi_factor",
            "top5": top5,
            "full_list": full_list,
            "message": "精选Top 5建议持仓，完整列表展示Top 50供参考",
        })
    except Exception as e:
        log.error(f"v5选股错误: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/api/pick_compare")
def api_pick_compare():
    """对比旧策略和v5策略的选股结果"""
    try:
        from modules.stock_picker import run_picker

        results = run_picker()
        total = results[0].get('_total_scanned', 0) if results else 0

        # 按旧策略排序
        old_sorted = sorted(results, key=lambda x: x.get('score', 0), reverse=True)[:10]
        # 按v5排序
        v5_sorted = sorted(results, key=lambda x: x.get('v5_score', 0), reverse=True)[:10]

        # 计算统计
        old_codes = set(r['code'] for r in old_sorted)
        v5_codes = set(r['code'] for r in v5_sorted)
        overlap = old_codes & v5_codes

        # 评分差异
        all_scores = []
        for r in results:
            diff = r.get('v5_score', 0) - r.get('score', 0)
            all_scores.append({
                'code': r['code'], 'name': r['name'],
                'old_score': round(r.get('score', 0), 1),
                'v5_score': round(r.get('v5_score', 0), 2),
                'diff': round(diff, 2),
            })
        biggest_diff = sorted(all_scores, key=lambda x: abs(x['diff']), reverse=True)[:5]

        return jsonify({
            "success": True,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_scanned": total,
            "overlap": len(overlap),
            "overlap_pct": len(overlap) * 10,
            "old_top10": [{
                'rank': i+1, 'code': r['code'], 'name': r['name'],
                'old_score': round(r.get('score', 0), 1),
                'v5_score': round(r.get('v5_score', 0), 2),
            } for i, r in enumerate(old_sorted)],
            "v5_top10": [{
                'rank': i+1, 'code': r['code'], 'name': r['name'],
                'v5_score': round(r.get('v5_score', 0), 2),
                'old_score': round(r.get('score', 0), 1),
                'v5_factors': r.get('v5_factors', {}),
            } for i, r in enumerate(v5_sorted)],
            "biggest_diff": biggest_diff,
        })
    except Exception as e:
        log.error(f"对比错误: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/api/market")
def api_market():
    """获取全市场概览 (60秒缓存 via cache_manager)"""
    from modules.data_fetcher import get_realtime_quotes
    from modules.cache_manager import cache as _cache
    from modules.cache_config import MARKET_TTL

    _cache_key = 'market_overview'
    cached = _cache.get(_cache_key)
    if cached is not None:
        return jsonify(cached)

    try:
        stocks = get_realtime_quotes()
        if not stocks:
            return jsonify({"success": False, "error": "无法获取市场数据"})

        stock_list = [s.__dict__ for s in stocks.values()]
        total = len(stock_list)
        up_count = len([s for s in stock_list if s.get("change_pct", 0) > 0])
        down_count = len([s for s in stock_list if s.get("change_pct", 0) < 0])
        flat_count = total - up_count - down_count
        avg_change = sum(s.get("change_pct", 0) for s in stock_list) / total if total else 0

        top_gainers = sorted(stock_list, key=lambda x: x.get("change_pct", 0), reverse=True)[:10]
        top_losers = sorted(stock_list, key=lambda x: x.get("change_pct", 0))[:10]
        top_volume = sorted(stock_list, key=lambda x: x.get("amount", 0), reverse=True)[:10]

        # 为榜单股票补充行业/板块信息（30只，并发获取，受内存/文件缓存）
        from modules.data_fetcher import get_stock_industry
        def _attach_industry(item):
            try:
                info = get_stock_industry(item["code"])
                item["industry"] = info.get("industry", "")
                item["sector"] = info.get("sector_type", "default")
            except Exception:
                item.setdefault("industry", "")
                item.setdefault("sector", "default")
            return item

        ranked_lists = [top_gainers, top_losers, top_volume]
        for ranked in ranked_lists:
            with ThreadPoolExecutor(max_workers=8) as _exec:
                list(_exec.map(_attach_industry, ranked))

        result = {
            "success": True,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "total": total, "up": up_count, "down": down_count,
                "flat": flat_count, "avg_change": round(avg_change, 2),
            },
            "top_gainers": [{"code": s["code"], "name": s["name"], "price": s["price"],
                             "change_pct": s["change_pct"], "amount": s.get("amount", 0),
                             "industry": s.get("industry", "")} for s in top_gainers],
            "top_losers": [{"code": s["code"], "name": s["name"], "price": s["price"],
                           "change_pct": s["change_pct"], "amount": s.get("amount", 0),
                           "industry": s.get("industry", "")} for s in top_losers],
            "top_volume": [{"code": s["code"], "name": s["name"], "price": s["price"],
                            "change_pct": s["change_pct"], "amount": s.get("amount", 0),
                            "industry": s.get("industry", "")} for s in top_volume],
        }
        _cache.set(_cache_key, result, ttl=MARKET_TTL)
        return jsonify(result)
    except Exception as e:
        log.error(f"获取市场数据失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/api/news")
def api_news():
    """获取新闻热点与板块分析"""
    try:
        from modules.scoring import get_hot_sectors_and_news, _fetch_sina_sectors, SECTOR_KEYWORDS
        from modules.http_client import session, HEADERS

        # 获取板块行情
        industry_sectors = _fetch_sina_sectors('industry')
        concept_sectors = _fetch_sina_sectors('class')

        all_sectors = industry_sectors + concept_sectors

        # 离线模式检测
        if not industry_sectors and not concept_sectors:
            all_sectors = [
                {"name": "半导体", "change_pct": 2.85, "avg_pe": 65.2, "stock_count": 85, "leader_name": "北方华创", "leader_change": 5.25, "code": "hangye_bandaoti"},
                {"name": "医疗器械", "change_pct": 1.95, "avg_pe": 45.2, "stock_count": 120, "leader_name": "迈瑞医疗", "leader_change": 1.85, "code": "hangye_yiliaoqixie"},
                {"name": "锂电池", "change_pct": 3.25, "avg_pe": 35.2, "stock_count": 95, "leader_name": "宁德时代", "leader_change": 2.65, "code": "hangye_lidianchi"},
                {"name": "光伏设备", "change_pct": 2.45, "avg_pe": 22.5, "stock_count": 65, "leader_name": "阳光电源", "leader_change": 4.25, "code": "hangye_guangfushebei"},
                {"name": "生物制品", "change_pct": -1.25, "avg_pe": 28.5, "stock_count": 80, "leader_name": "智飞生物", "leader_change": -1.25, "code": "hangye_shengwuzhipin"},
                {"name": "软件开发", "change_pct": 1.85, "avg_pe": 85.2, "stock_count": 150, "leader_name": "科大讯飞", "leader_change": 2.85, "code": "hangye_ruanjiankaifa"},
            ]

        # 获取新闻
        news_list = []
        try:
            r = session.get("https://feed.mix.sina.com.cn/api/roll/get",
                             params={"pageid": "153", "lid": "2509", "k": "", "r": "0.5", "page": 1},
                             headers=HEADERS, timeout=10)
            d = r.json()
            if d.get('result') and d['result'].get('data'):
                for item in d['result']['data'][:40]:
                    news_list.append({
                        "title": item.get('title', ''),
                        "time": item.get('ctime', ''),
                        "source": item.get('media_name', ''),
                        "summary": item.get('intro', '') or item.get('summary', ''),
                    })
        except Exception as e:
            log.warning(f"获取新闻失败: {e}")

        # 新闻与板块关联分析
        利好词 = ["上涨", "增长", "突破", "超预期", "利好", "政策支持", "补贴", "创新高", "大涨", "暴涨", "加速", "提升", "扩大", "向好", "复苏", "回暖", "走强", "拉升", "涨停", "爆发"]
        利空词 = ["下跌", "下滑", "亏损", "收紧", "制裁", "打压", "暴跌", "跌停", "危机", "风险", "利空", "放缓"]

        for news in news_list:
            affected = []
            text = news.get("title", "") + " " + news.get("summary", "")
            for sector, keywords in SECTOR_KEYWORDS.items():
                match_count = sum(1 for kw in keywords if kw in text)
                if match_count > 0:
                    sector_info = next((s for s in all_sectors if s["name"] == sector or sector in s["name"]), None)
                    if any(kw in text for kw in 利好词):
                        impact = "利好"
                    elif any(kw in text for kw in 利空词):
                        impact = "利空"
                    else:
                        impact = "关注"
                    affected.append({
                        "sector": sector,
                        "impact": impact,
                        "match_count": match_count,
                        "change_pct": sector_info["change_pct"] if sector_info else 0,
                        "leader": sector_info.get("leader_name", "") if sector_info else "",
                    })
            affected.sort(key=lambda x: x["match_count"], reverse=True)
            news["affected_sectors"] = affected[:5]

        relevant_news = [n for n in news_list if n.get("affected_sectors")]

        # 排名
        top_sectors = sorted(industry_sectors, key=lambda x: x.get('change_pct', 0), reverse=True)[:15]
        top_concepts = sorted(concept_sectors, key=lambda x: x.get('change_pct', 0), reverse=True)[:15]
        top_fund_inflow = sorted(industry_sectors, key=lambda x: x.get('amount', x.get('change_pct', 0)), reverse=True)[:10]

        return jsonify({
            "success": True,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "news": relevant_news[:25] if relevant_news else news_list[:25],
            "all_news": news_list[:40],
            "total_news": len(news_list),
            "top_sectors": top_sectors,
            "top_concepts": top_concepts,
            "top_fund_inflow": top_fund_inflow,
            "sector_count": len(industry_sectors),
            "concept_count": len(concept_sectors),
        })
    except Exception as e:
        log.error(f"获取新闻失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/api/sector_stocks")
def api_sector_stocks():
    """获取板块成分股"""
    from modules.http_client import session, EM_HEADERS, DC_HEADERS
    from modules.data_fetcher import get_stock_industry

    def _enrich_industry(stock_list):
        """为股票列表补全 industry 字段（带行业缓存）"""
        def _attach(item):
            if "industry" not in item:
                try:
                    info = get_stock_industry(item["code"])
                    item["industry"] = info.get("industry", "")
                except Exception:
                    item["industry"] = ""
            return item
        with ThreadPoolExecutor(max_workers=8) as _exec:
            list(_exec.map(_attach, stock_list))

    sector_code = request.args.get("code", "")
    sector_name = request.args.get("name", "")
    if not sector_code:
        return jsonify({"success": False, "error": "缺少板块代码"}), 400

    try:
        stocks = []

        # 新浪板块代码
        if sector_code.startswith('gn_') or sector_code.startswith('hangye_'):
            try:
                url = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
                all_stocks = []
                for page in range(1, 6):
                    params = {
                        'page': page, 'num': 50,
                        'sort': 'changepercent', 'asc': 0,
                        'node': sector_code, '_s_r_a': 'page'
                    }
                    r = session.get(url, params=params, timeout=10,
                                    headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'})
                    r.encoding = 'utf-8'
                    page_stocks = r.json()
                    if not page_stocks:
                        break
                    all_stocks.extend(page_stocks)

                for item in all_stocks:
                    try:
                        name = item.get('name', '')
                        if 'ST' in name or '*' in name:
                            continue
                        code = item.get('code', '')
                        price = float(item.get('trade', 0) or 0)
                        if price <= 0 or not code:
                            continue
                        pe_raw = item.get('per', 0) or 0
                        pe_val = 0
                        if pe_raw and pe_raw != '-' and float(pe_raw) > 0 and float(pe_raw) < 10000:
                            pe_val = float(pe_raw)
                        pb_raw = item.get('pb', 0) or 0
                        pb_val = 0
                        if pb_raw and pb_raw != '-':
                            try:
                                pb_val = float(pb_raw)
                            except ValueError:
                                pb_val = 0
                        stocks.append({
                            "code": code, "name": name, "price": price,
                            "change_pct": float(item.get('changepercent', 0) or 0),
                            "amount": float(item.get('amount', 0) or 0),
                            "pe": pe_val, "pb": pb_val, "roe": 0, "gross_margin": 0,
                            "market_cap": float(item.get('nmc', 0) or 0),
                        })
                    except Exception:
                        continue
            except Exception as e:
                log.warning(f"新浪板块API失败: {e}")

            if stocks:
                stocks.sort(key=lambda x: x["change_pct"], reverse=True)
                _enrich_industry(stocks)
                return jsonify({"success": True, "sector_name": sector_name, "stocks": stocks[:50], "total": len(stocks)})
            else:
                return jsonify({"success": False, "error": "无法获取板块成分股"}), 500

        # 东方财富BK代码
        try:
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "pn": 1, "pz": 20, "po": 1, "np": 1,
                "ut": os.environ.get("EM_UT_TOKEN", ""),
                "fltt": 2, "invt": 2, "fid": "f3",
                "fs": f"b:{sector_code}+f:!50",
                "fields": "f2,f3,f12,f14,f20,f162,f167"
            }
            resp = session.get(url, params=params, headers=EM_HEADERS, timeout=5)
            data = resp.json()
            if data.get("data") and data["data"].get("diff"):
                for item in data["data"]["diff"]:
                    try:
                        code = str(item.get("f12", ""))
                        name = item.get("f14", "")
                        if "ST" in name or "*" in name:
                            continue
                        price = float(item.get("f2", 0))
                        if price <= 0:
                            continue
                        pe_raw = item.get("f162", 0)
                        pe_val = 0
                        if pe_raw and pe_raw != "-":
                            try:
                                pe_val = float(pe_raw)
                            except ValueError:
                                pe_val = 0
                        pb_raw = item.get("f167", 0)
                        pb_val = 0
                        if pb_raw and pb_raw != "-":
                            try:
                                pb_val = float(pb_raw)
                            except ValueError:
                                pb_val = 0
                        stocks.append({
                            "code": code, "name": name, "price": price,
                            "change_pct": float(item.get("f3", 0)),
                            "amount": float(item.get("f6", 0)) if item.get("f6", 0) else 0,
                            "pe": pe_val, "pb": pb_val, "roe": 0, "gross_margin": 0,
                            "market_cap": float(item.get("f20", 0)) / 100000000 if item.get("f20", 0) > 0 else 0,
                        })
                    except Exception:
                        continue
                if stocks:
                    stocks.sort(key=lambda x: x["change_pct"], reverse=True)
                    _enrich_industry(stocks)
                    return jsonify({"success": True, "sector_name": sector_name, "stocks": stocks[:20], "total": len(stocks)})
        except Exception:
            pass

        # 备选方案
        try:
            dc_params = {
                'reportName': 'RPT_INDUSTRY_INDEX',
                'columns': 'BOARD_CODE,SECURITY_CODE,INDICATOR_VALUE',
                'filter': f'(BOARD_CODE="{sector_code}")',
                'pageNumber': 1, 'pageSize': 25,
                'source': 'WEB', 'client': 'WEB',
            }
            resp = session.get('https://datacenter-web.eastmoney.com/api/data/v1/get',
                               params=dc_params, headers=DC_HEADERS, timeout=10)
            d = resp.json()
            member_codes = []
            if d.get('success') and d.get('result') and d['result'].get('data'):
                for item in d['result']['data']:
                    sc = item.get('SECURITY_CODE', '')
                    if sc and len(sc) == 6:
                        member_codes.append(sc)

            if member_codes:
                tx_codes = [f"sh{c}" if c.startswith('6') else f"sz{c}" for c in member_codes]
                url = 'http://qt.gtimg.cn/q=' + ','.join(tx_codes)
                tx_resp = session.get(url, timeout=15)
                lines = tx_resp.text.strip().split(';')
                for line in lines:
                    if not line.strip():
                        continue
                    parts = line.split('~')
                    if len(parts) < 50:
                        continue
                    code = parts[2]
                    try:
                        price = float(parts[3]) if parts[3] else 0
                        if price <= 0:
                            continue
                        stocks.append({
                            "code": code, "name": parts[1], "price": price,
                            "change_pct": float(parts[32]) if parts[32] else 0,
                            "amount": float(parts[43]) * 10000 if parts[43] else 0,
                            "pe": float(parts[39]) if parts[39] and parts[39] != '-' and float(parts[39]) < 10000 else 0,
                            "pb": 0, "roe": 0, "gross_margin": 0,
                            "market_cap": float(parts[44]) if parts[44] else 0,
                        })
                    except Exception:
                        continue

            stocks.sort(key=lambda x: x["change_pct"], reverse=True)
        except Exception as e2:
            log.warning(f"备选方案也失败: {e2}")

        if stocks:
            _enrich_industry(stocks)
            return jsonify({"success": True, "sector_name": sector_name, "stocks": stocks[:20], "total": len(stocks)})
        return jsonify({"success": False, "error": "无法获取板块成分股"}), 500
    except Exception as e:
        log.error(f"获取板块成分股失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/api/stock_detail")
def api_stock_detail():
    """获取单个股票详情 + 相关新闻"""
    from modules.http_client import session, HEADERS
    from modules.data_fetcher import get_financial_data
    from modules.scoring import evaluate_stock
    from modules.technical import calculate_technical_indicators
    from modules.data_fetcher import get_stock_industry
    from modules.config import LIQUOR_NAMES, BANK_CODES

    code = request.args.get("code", "")
    if not code:
        return jsonify({"success": False, "error": "缺少股票代码"}), 400

    try:
        # 1. 获取实时行情
        stock_info = _fetch_stock_quote(code)

        # 2. 补充财务数据
        if stock_info:
            _enrich_financial_data(stock_info, code)

        if not stock_info:
            return jsonify({"success": False, "error": "股票不存在或无法获取数据"}), 404

        # 3. 评分
        tech_data = None
        try:
            tech_data = calculate_technical_indicators(code, days=30)
        except Exception as e:
            log.warning(f"技术指标获取失败: {e}")

        eval_result = evaluate_stock(stock_info, tech_data=tech_data)

        if not eval_result:
            is_excluded = any(n in stock_info.get("name", "") for n in LIQUOR_NAMES) or code in BANK_CODES
            if is_excluded:
                return jsonify({"success": False, "error": "该股票属于白酒/银行板块，不在评估范围内", "stock": stock_info}), 200
            return jsonify({"success": False, "error": "股票评分计算失败"}), 404

        score = eval_result.get("score", 0)
        v5_score = eval_result.get("v5_score", 0)
        v5_factors = eval_result.get("v5_factors", {})
        v5_reasons = eval_result.get("v5_reasons", [])
        v5_rec = eval_result.get("v5_recommendation", "")
        dimensions = eval_result.get("dimensions", {})
        buy_sell = eval_result.get("buy_sell")
        reasons = eval_result.get("reasons", [])

        # 4. 分析详情
        analysis = _build_analysis_detail(stock_info, dimensions)

        # 5. 获取相关新闻
        stock_news = _fetch_stock_news(stock_info.get("name", ""))

        # 6. 行业信息
        if not stock_info.get("industry"):
            try:
                industry_info = get_stock_industry(code)
                stock_info["industry"] = industry_info.get("industry", "未知")
                stock_info["sector_type"] = industry_info.get("sector_type", "default")
            except Exception:
                stock_info["industry"] = "未知"

        return jsonify({
            "success": True,
            "stock": stock_info,
            "score": score,
            "v5_score": v5_score,
            "v5_factors": v5_factors,
            "v5_reasons": v5_reasons,
            "v5_recommendation": v5_rec,
            "dimensions": dimensions,
            "analysis": analysis,
            "buy_sell": buy_sell,
            "news": stock_news[:8],
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        log.error(f"获取股票详情失败: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/api/refresh_financials", methods=["POST"])
def api_refresh_financials():
    """刷新指定股票的财务数据"""
    import os
    from modules.data_fetcher import get_financial_data
    from modules.http_client import session
    from modules.config import BASE_DIR

    data = request.get_json(silent=True) or {}
    codes = data.get('codes', [])
    if not codes:
        return api_error("请提供股票代码列表")

    log.info(f"开始刷新 {len(codes)} 只股票的财务数据...")

    offline_path = os.path.join(BASE_DIR, 'offline_stocks.json')
    try:
        with open(offline_path, 'r', encoding='utf-8') as f:
            offline_data = json.load(f)
            offline_stocks = {s['code']: s for s in offline_data.get('stocks', [])}
    except Exception:
        offline_stocks = {}

    updated = 0
    failed = 0
    results = []

    for i, code in enumerate(codes):
        log.debug(f"  [{i+1}/{len(codes)}] 刷新 {code}...")
        try:
            fin = get_financial_data(code)
            tx_code = f"sh{code}" if code.startswith('6') else f"sz{code}"
            tx_resp = session.get(f'http://qt.gtimg.cn/q={tx_code}', timeout=10)
            parts = tx_resp.text.split('~')

            name = parts[1] if len(parts) > 1 else code
            price = float(parts[3]) if len(parts) > 3 and parts[3] else 0
            pe_val = float(parts[39]) if len(parts) > 39 and parts[39] and parts[39] != '-' else 0
            pb_val = float(parts[46]) if len(parts) > 46 and parts[46] and parts[46] != '-' else 0
            cap_yi = float(parts[44]) if len(parts) > 44 and parts[44] and parts[44] != '-' else 0

            if fin and fin.get('roe', 0) > 0:
                existing = offline_stocks.get(code, {})
                existing.update({
                    'code': code, 'name': name, 'price': price,
                    'pe': pe_val if pe_val > 0 else existing.get('pe', 0),
                    'pb': pb_val if pb_val > 0 else existing.get('pb', 0),
                    'market_cap': cap_yi if cap_yi > 0 else existing.get('market_cap', 0),
                    'change_pct': float(parts[32]) if len(parts) > 32 and parts[32] else 0,
                    'roe': fin.get('roe', existing.get('roe', 0)),
                    'gross_margin': fin.get('gross_margin', existing.get('gross_margin', 0)),
                    'net_margin': fin.get('net_margin', existing.get('net_margin', 0)),
                    'rev_growth': fin.get('rev_growth', existing.get('rev_growth', 0)),
                    'profit_growth': fin.get('profit_growth', existing.get('profit_growth', 0)),
                    'debt_ratio': fin.get('debt_ratio', existing.get('debt_ratio', 0)),
                })
                offline_stocks[code] = existing
                updated += 1
                results.append({"code": code, "status": "updated", "name": name})
            else:
                failed += 1
                results.append({"code": code, "status": "no_data", "name": name})
        except Exception as e:
            failed += 1
            results.append({"code": code, "status": "error", "error": str(e)})

    offline_data = {
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(offline_stocks),
        'stocks': list(offline_stocks.values()),
    }
    with open(offline_path, 'w', encoding='utf-8') as f:
        json.dump(offline_data, f, ensure_ascii=False, indent=2)

    return jsonify({"success": True, "updated": updated, "failed": failed, "total": len(codes), "results": results})


def _fetch_stock_quote(code: str) -> Optional[dict]:
    """获取单只股票实时行情"""
    from modules.http_client import session

    stock_info = None
    try:
        tx_code = f"sh{code}" if code.startswith('6') else f"sz{code}"
        url = f'http://qt.gtimg.cn/q={tx_code}'
        tx_resp = session.get(url, timeout=10)
        lines = tx_resp.text.strip().split(';')
        for line in lines:
            if not line.strip():
                continue
            if '=' in line:
                line = line.split('=', 1)[1].strip('"')
            parts = line.split('~')
            if len(parts) < 50:
                continue
            try:
                price = float(parts[3]) if parts[3] else 0
                if price <= 0:
                    continue
                pe_val = 0
                if parts[39] and parts[39] != '-':
                    pe_val = float(parts[39])
                    if pe_val > 10000 or pe_val < 0:
                        pe_val = 0
                pb_val = 0
                if parts[46] and parts[46] != '-':
                    pb_val = float(parts[46])
                turnover_val = 0
                if parts[38] and parts[38] != '-':
                    turnover_val = float(parts[38])
                total_cap_yi = float(parts[44]) if parts[44] and parts[44] != '-' else 0
                stock_info = {
                    "code": code, "name": parts[1], "price": price,
                    "change_pct": float(parts[32]) if parts[32] else 0,
                    "high": float(parts[33]) if parts[33] else 0,
                    "low": float(parts[34]) if parts[34] else 0,
                    "open": float(parts[5]) if parts[5] else 0,
                    "prev_close": float(parts[4]) if parts[4] else 0,
                    "volume": float(parts[37]) if parts[37] else 0,
                    "amount": float(parts[43]) * 10000 if parts[43] else 0,
                    "pe": pe_val, "pb": pb_val, "turnover_rate": turnover_val,
                    "roe": 0, "gross_margin": 0, "net_margin": 0,
                    "rev_growth": 0, "profit_growth": 0, "debt_ratio": 0,
                    "market_cap": total_cap_yi,
                }
                break
            except Exception:
                continue
    except Exception as e:
        log.warning(f"行情获取失败: {e}")
    return stock_info


def _enrich_financial_data(stock_info: dict, code: str) -> None:
    """补充财务数据"""
    from modules.data_fetcher import get_preset_financials

    if not stock_info:
        return
    try:
        from modules.data_fetcher import get_financial_data as _get_fin
        fin_data = _get_fin(code)
    except Exception:
        fin_data = None

    if fin_data:
        for key in ["roe", "gross_margin", "rev_growth", "profit_growth", "debt_ratio", "net_margin", "pb"]:
            if hasattr(fin_data, key):
                val = getattr(fin_data, key, 0)
                if val and val != 0:
                    stock_info[key] = val

    if stock_info.get("pe", 0) == 0 and stock_info.get("pb", 0) > 0 and stock_info.get("roe", 0) > 0:
        stock_info["pe"] = round(stock_info["pb"] / (stock_info["roe"] / 100), 1)

    if stock_info.get("roe", 0) == 0 and stock_info.get("gross_margin", 0) == 0:
        preset_data = get_preset_financials()
        if code in preset_data:
            preset = preset_data[code]
            for k in ["roe", "gross_margin", "net_margin", "rev_growth", "profit_growth", "debt_ratio"]:
                if stock_info.get(k, 0) == 0 and preset.get(k, 0) != 0:
                    stock_info[k] = preset[k]


def _build_analysis_detail(stock_info: dict, dimensions: dict) -> list[dict]:
    """构建分析详情"""
    roe = stock_info.get("roe", 0)
    gross_margin = stock_info.get("gross_margin", 0)
    net_margin = stock_info.get("net_margin", 0)
    rev_growth = stock_info.get("rev_growth", 0)
    profit_growth = stock_info.get("profit_growth", 0)
    pe = stock_info.get("pe", 0)
    pb = stock_info.get("pb", 0)
    debt_ratio = stock_info.get("debt_ratio", 0)

    analysis = []

    if roe >= 20:
        analysis.append({"dim": "盈利能力", "score": round(dimensions.get("profitability", 0)), "max": 35, "detail": f"ROE {roe:.1f}% 优秀（≥20%）", "level": "excellent"})
    elif roe >= 15:
        analysis.append({"dim": "盈利能力", "score": round(dimensions.get("profitability", 0)), "max": 35, "detail": f"ROE {roe:.1f}% 良好（≥15%）", "level": "good"})
    elif roe > 0:
        analysis.append({"dim": "盈利能力", "score": round(dimensions.get("profitability", 0)), "max": 35, "detail": f"ROE {roe:.1f}% 一般", "level": "fair"})
    else:
        analysis.append({"dim": "盈利能力", "score": 0, "max": 35, "detail": "ROE数据缺失", "level": "unknown"})

    avg_growth = max(rev_growth, profit_growth) if rev_growth > 0 and profit_growth > 0 else (rev_growth if rev_growth > 0 else profit_growth)
    if avg_growth >= 20:
        analysis.append({"dim": "成长性", "score": 25, "max": 25, "detail": f"平均增速 {avg_growth:.1f}% 优秀（≥20%）", "level": "excellent"})
    elif avg_growth >= 15:
        analysis.append({"dim": "成长性", "score": 20, "max": 25, "detail": f"平均增速 {avg_growth:.1f}% 良好（≥15%）", "level": "good"})
    elif avg_growth >= 10:
        analysis.append({"dim": "成长性", "score": 15, "max": 25, "detail": f"平均增速 {avg_growth:.1f}% 一般（≥10%）", "level": "fair"})
    elif avg_growth > 0:
        analysis.append({"dim": "成长性", "score": 8, "max": 25, "detail": f"平均增速 {avg_growth:.1f}% 较低", "level": "poor"})
    else:
        analysis.append({"dim": "成长性", "score": 0, "max": 25, "detail": "成长性数据缺失", "level": "unknown"})

    if debt_ratio > 0 and debt_ratio < 1000:
        if debt_ratio <= 50:
            analysis.append({"dim": "财务健康", "score": 20, "max": 20, "detail": f"资产负债率 {debt_ratio:.1f}% 优秀（≤50%）", "level": "excellent"})
        elif debt_ratio <= 70:
            analysis.append({"dim": "财务健康", "score": 12, "max": 20, "detail": f"资产负债率 {debt_ratio:.1f}% 一般（≤70%）", "level": "fair"})
        else:
            analysis.append({"dim": "财务健康", "score": 5, "max": 20, "detail": f"资产负债率 {debt_ratio:.1f}% 偏高", "level": "poor"})
    else:
        analysis.append({"dim": "财务健康", "score": 10, "max": 20, "detail": "资产负债率数据缺失", "level": "unknown"})

    if pe > 0 and pe < 1000:
        level = "excellent" if pe <= 12 else "good" if pe <= 20 else "fair" if pe <= 35 else "poor"
        analysis.append({"dim": "估值", "score": round(dimensions.get("valuation", 0)), "max": 20, "detail": f"PE {pe:.1f}", "level": level})
    else:
        analysis.append({"dim": "估值", "score": 8, "max": 20, "detail": "无PE数据", "level": "unknown"})

    return analysis


def _fetch_stock_news(stock_name: str) -> list[dict]:
    """获取相关新闻"""
    from modules.http_client import session, HEADERS

    stock_news = []
    try:
        r = session.get("https://feed.mix.sina.com.cn/api/roll/get",
                         params={"pageid": "153", "lid": "2509", "k": "", "r": "0.5", "page": 1},
                         headers=HEADERS, timeout=10)
        d = r.json()
        if d.get('result') and d['result'].get('data'):
            for item in d['result']['data'][:30]:
                title = item.get('title', '')
                intro = item.get('intro', '') or ''
                text = title + ' ' + intro
                if stock_name in text:
                    impact = "关注"
                    if any(w in text for w in ["上涨", "增长", "突破", "超预期", "利好", "大涨", "暴涨"]):
                        impact = "利好"
                    elif any(w in text for w in ["下跌", "亏损", "下滑", "收紧", "暴跌", "利空"]):
                        impact = "利空"
                    stock_news.append({
                        "title": title,
                        "time": item.get('ctime', ''),
                        "source": item.get('media_name', ''),
                        "summary": intro,
                        "impact": impact,
                    })
    except Exception as e:
        log.warning(f"获取相关新闻失败: {e}")
    return stock_news[:8]


@api_bp.route("/api/auction/compare", methods=["POST"])
def api_auction_compare():
    """集合竞价对比"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return api_error("请求体不能为空")
        from web_app import run_auction_compare
        result = run_auction_compare(data)
        return api_success(result)
    except Exception as e:
        log.error(f"集合竞价对比失败: {e}", exc_info=True)
        return api_error(f"对比失败: {e}")


@api_bp.route("/api/auction_compare")
def api_auction_compare_get():
    """集合竞价对比（GET版本）"""
    try:
        from modules.auction_picker import compare_auction
        result = compare_auction({"codes": []})
        return api_success(result)
    except Exception as e:
        log.error(f"集合竞价对比失败: {e}", exc_info=True)
        return api_error(f"对比失败: {e}")


@api_bp.route("/api/auction_compare_execute")
def api_auction_compare_execute():
    """执行集合竞价对比选股"""
    try:
        from modules.auction_picker import run_auction_picker
        result = run_auction_picker()
        return api_success(result)
    except Exception as e:
        log.error(f"集合竞价对比执行失败: {e}", exc_info=True)
        return api_error(f"执行失败: {e}")


@api_bp.route("/api/cb_arbitrage")
def api_cb_arbitrage():
    """可转债套利数据 - 转股价值 vs 转债价格 (60秒缓存 via cache_manager)"""
    try:
        from modules.http_client import session, HEADERS, DC_HEADERS
        from modules.cache_manager import cache as _cache
        from modules.cache_config import CB_ARBITRAGE_TTL

        _cache_key = 'cb_arbitrage'
        cached = _cache.get(_cache_key)
        if cached is not None:
            return jsonify(cached)

        all_cb_rows = []
        page = 1
        while True:
            url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
            params = {
                "reportName": "RPT_BOND_CB_LIST",
                "columns": "ALL",
                "pageNumber": page,
                "pageSize": 500,
                "sortTypes": -1,
                "sortColumns": "SECURITY_CODE",
                "source": "WEB",
                "client": "WEB",
            }
            resp = session.get(url, params=params, headers=DC_HEADERS, timeout=10)
            data = resp.json()

            if not data.get("success") or not data.get("result", {}).get("data"):
                break

            rows = data["result"]["data"]
            all_cb_rows.extend(rows)

            total_count = data["result"].get("count", 0)
            if len(all_cb_rows) >= total_count or len(rows) < 500:
                break
            page += 1

        if not all_cb_rows:
            return jsonify({"error": "无法获取可转债数据", "data": [], "update_time": datetime.now().strftime("%H:%M:%S")})

        cb_list = []
        for row in all_cb_rows:
            code = row.get("SECURITY_CODE", "")
            name = row.get("SECURITY_NAME_ABBR", "")
            stock_code = row.get("CONVERT_STOCK_CODE", "")
            transfer_price = row.get("INITIAL_TRANSFER_PRICE")
            rating = row.get("RATING", "")

            if not code or not stock_code or not transfer_price:
                continue
            sc_str = str(stock_code)
            if not (sc_str.startswith("0") or sc_str.startswith("3") or sc_str.startswith("6")):
                continue

            cb_list.append({
                "code": code,
                "name": name,
                "stock_code": stock_code,
                "transfer_price": float(transfer_price) if transfer_price else 0,
                "rating": rating or "",
            })

        bond_codes = []
        for cb in cb_list:
            c = str(cb["code"])
            if c.startswith("11"):
                bond_codes.append(f"sh{c}")
            elif c.startswith("12") or c.startswith("13"):
                bond_codes.append(f"sz{c}")
            else:
                continue

        stock_codes = set()
        for cb in cb_list:
            sc = cb["stock_code"]
            if sc:
                if sc.startswith("6"):
                    stock_codes.add(f"sh{sc}")
                elif sc.startswith("0") or sc.startswith("3"):
                    stock_codes.add(f"sz{sc}")

        all_codes = list(bond_codes) + list(stock_codes)
        batch_size = 50
        quote_map = {}

        for i in range(0, len(all_codes), batch_size):
            batch = ",".join(all_codes[i:i+batch_size])
            try:
                r = session.get(f"https://qt.gtimg.cn/q={batch}", headers=HEADERS, timeout=15)
                lines = r.text.strip().split(";")
                for line in lines:
                    line = line.strip()
                    if not line or "~" not in line:
                        continue
                    parts = line.split("~")
                    # 从 var_name 提取正确的 qcode（如 "v_sh110001" → "sh110001"）
                    var_name = parts[0].split("=")[0].strip() if "=" in parts[0] else ""
                    qcode = var_name[2:] if var_name.startswith("v_") else ""
                    if qcode:
                        qdata = {
                            "name": parts[1] if len(parts) > 1 else "",
                            "price": float(parts[3]) if len(parts) > 3 and parts[3] else 0,
                            "pre_close": float(parts[4]) if len(parts) > 4 and parts[4] else 0,
                            "change_pct": float(parts[32]) if len(parts) > 32 and parts[32] else 0,
                            "volume": float(parts[36]) if len(parts) > 36 and parts[36] else 0,
                            "amount": float(parts[37]) if len(parts) > 37 and parts[37] else 0,
                        }
                        quote_map[qcode] = qdata
            except Exception:
                continue

        items = []
        for cb in cb_list:
            bc = str(cb["code"])
            sc = str(cb["stock_code"])
            tp = cb["transfer_price"]

            bq = {}
            if bc.startswith("11"):
                bq = quote_map.get(f"sh{bc}", {})
            elif bc.startswith("12") or bc.startswith("13"):
                bq = quote_map.get(f"sz{bc}", {})

            sq = {}
            if sc.startswith("6"):
                sq = quote_map.get(f"sh{sc}", {})
            elif sc.startswith("0") or sc.startswith("3"):
                sq = quote_map.get(f"sz{sc}", {})

            bp = bq.get("price", 0)
            sp = sq.get("price", 0)

            if tp <= 0 or bp <= 0 or sp <= 0:
                continue

            cv = round((100 / tp * sp), 4)
            pr = round(((bp / cv - 1) * 100), 2) if cv > 0 else None
            # 涨幅差值 = 转债涨跌幅 - 正股涨跌幅
            bond_chg = bq.get("change_pct", 0) or 0
            stock_chg = sq.get("change_pct", 0) or 0
            diff = round(bond_chg - stock_chg, 2)

            items.append({
                "rank": 0,
                "stock_name": sq.get("name", ""),
                "stock_code": sc,
                "stock_change": sq.get("change_pct", 0),
                "bond_name": bq.get("name", ""),
                "bond_code": bc,
                "bond_change": bq.get("change_pct", 0),
                "diff": diff,
                "rating": cb.get("rating", ""),
                "conv_price": round(tp, 2),
                "conversion_value": cv,
                "bond_price": bp,
                "stock_price": sp,
            })

        items.sort(key=lambda x: x.get("diff") or -999, reverse=True)

        # 为正股补全 industry 字段（前端模板有期望，API 之前未提供）
        def _attach_industry(item):
            try:
                info = get_stock_industry(item.get("stock_code", ""))
                item["industry"] = info.get("industry", "")
            except Exception:
                item.setdefault("industry", "")
            return item
        with ThreadPoolExecutor(max_workers=8) as _exec:
            list(_exec.map(_attach_industry, items))

        for i, item in enumerate(items):
            item["rank"] = i + 1

        result = {
            "data": items,
            "total": len(items),
            "update_time": datetime.now().strftime("%H:%M:%S"),
        }
        _cache.set(_cache_key, result, ttl=CB_ARBITRAGE_TTL)
        return jsonify(result)
    except Exception as e:
        log.error(f"获取可转债数据失败: {e}", exc_info=True)
        return jsonify({"error": str(e), "data": [], "update_time": datetime.now().strftime("%H:%M:%S")})


@api_bp.route("/api/cache/stats")
def api_cache_stats():
    """Cache statistics for monitoring — hit rate, keys, memory estimate."""
    from modules.cache_manager import cache
    return jsonify(cache.stats())


@api_bp.route("/api/industries")
def api_industries():
    """批量查询股票行业（逗号分隔 codes 参数）→ 返回 {code: industry} 映射。
    主要用于静态 JSON 数据（如 backtest_report.html）补全板块显示。
    """
    from modules.data_fetcher import get_stock_industry

    codes_raw = request.args.get("codes", "")
    if not codes_raw:
        return jsonify({"success": False, "error": "缺少 codes 参数", "map": {}})

    codes = [c.strip() for c in codes_raw.split(",") if c.strip()]
    if not codes:
        return jsonify({"success": False, "error": "codes 为空", "map": {}})

    # 限制单次最多 200 只，避免滥用
    if len(codes) > 200:
        codes = codes[:200]

    result_map: dict = {}
    def _lookup(code: str):
        try:
            info = get_stock_industry(code)
            return code, info.get("industry", "")
        except Exception:
            return code, ""

    with ThreadPoolExecutor(max_workers=8) as _exec:
        for code, industry in _exec.map(_lookup, codes):
            result_map[code] = industry

    return jsonify({"success": True, "map": result_map})