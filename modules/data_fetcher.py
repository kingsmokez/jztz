"""数据获取模块 - 使用统一数据模型和日志"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import requests

from modules.config import Config, DataConfig
from modules.logger import log
from modules.models import StockQuote, FinancialData, filter_eligible_stocks, _safe_float

_session: Optional[requests.Session] = None
_config = Config().data

# 缓存
_realtime_cache: dict = {}
_realtime_cache_time: float = 0
_financial_cache: dict = {}
_financial_cache_time: float = 0


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": "https://gu.qq.com/",
        })
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20,
            pool_maxsize=30,
            max_retries=3,
        )
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
    return _session


def _get_all_stock_codes() -> list[str]:
    """获取全部A股代码列表（沪深主板+创业板，排除北交所和ST）

    策略：从东方财富财务数据接口获取有财务数据的A股，API失败时用离线库降级兜底
    """
    dc_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': 'https://data.eastmoney.com/',
    }

    try:
        actual_codes: set[str] = set()
        base_url = 'https://datacenter-web.eastmoney.com/api/data/v1/get'

        # 用最新季度数据获取A股列表（QDATE过滤确保每只股票只出现一次）
        current_year = datetime.now().year
        # 尝试最近几个季度，找到有数据的最新季度
        qdate_filters = []
        for yr in range(current_year, current_year - 2, -1):
            for q in range(4, 0, -1):
                qdate_filters.append(f'{yr}Q{q}')

        for qdate in qdate_filters:
            try:
                test_params = {
                    'reportName': 'RPT_LICO_FN_CPD',
                    'columns': 'SECURITY_CODE',
                    'filter': f'(QDATE="{qdate}")',
                    'pageNumber': 1, 'pageSize': 1,
                    'source': 'WEB', 'client': 'WEB',
                }
                tr = _get_session().get(base_url, params=test_params, headers=dc_headers, timeout=10)
                td = tr.json()
                if td.get('success') and td.get('result', {}).get('count', 0) > 1000:
                    # 找到有效季度，开始分页获取
                    page_size = 500  # API限制每页最多500条
                    for page in range(1, 20):
                        params = {
                            'reportName': 'RPT_LICO_FN_CPD',
                            'columns': 'SECURITY_CODE,SECURITY_NAME_ABBR',
                            'filter': f'(QDATE="{qdate}")',
                            'pageNumber': page,
                            'pageSize': page_size,
                            'sortTypes': '-1',
                            'sortColumns': 'SECURITY_CODE',
                            'source': 'WEB',
                            'client': 'WEB',
                        }
                        resp = _get_session().get(base_url, params=params, headers=dc_headers, timeout=15)
                        d = resp.json()
                        if not (d.get('success') and d.get('result') and d['result'].get('data')):
                            break
                        for row in d['result']['data']:
                            code = str(row.get('SECURITY_CODE', ''))
                            name = str(row.get('SECURITY_NAME_ABBR', ''))
                            # 排除非主流交易池：北交所(4/8/9开头)、B股(2开头)、ST等；
                            # 科创板(688/689)属于A股主池，保留给策略层按20%涨跌幅处理。
                            if code[0] in ('2', '4', '8', '9'):
                                continue
                            if 'ST' in name or '*' in name:
                                continue
                            if len(code) == 6 and code.isdigit():
                                actual_codes.add(code)
                        if len(d['result']['data']) < page_size:
                            break
                    if actual_codes:
                        log.info(f"从东方财富{qdate}获取到 {len(actual_codes)} 只A股代码")
                        return sorted(actual_codes)
            except Exception as e:
                log.debug(f"尝试季度{qdate}失败: {e}")
                continue

        if actual_codes:
            result = sorted(actual_codes)
            log.info(f"从东方财富获取到 {len(result)} 只A股代码")
            return result

    except Exception as e:
        log.error(f"获取股票代码列表失败: {e}")

    # API失败时，用离线库降级兜底
    preset = get_preset_financials()
    if preset and len(preset) > 50:
        codes = [c for c in preset.keys() if not c.startswith('9')]
        if codes:
            log.warning(f"东方财富API失败，使用离线库降级获取 {len(codes)} 只股票代码")
            return codes

    return []



# ========== 重试/超时保护工具 ==========

def _retry_with_timeout(func, max_retries: int = 3, timeout: float = 10.0, *args, **kwargs):
    """带重试和超时保护的函数调用

    用于 akshare 等不稳定 API 调用的保护。
    """
    import concurrent.futures
    last_error = None
    for attempt in range(max_retries):
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(func, *args, **kwargs)
                return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            last_error = TimeoutError(f"{func.__name__} 超时 ({timeout}s), 第{attempt+1}次")
            log.warning(f"重试 {attempt+1}/{max_retries}: {func.__name__} 超时")
        except Exception as e:
            last_error = e
            log.warning(f"重试 {attempt+1}/{max_retries}: {func.__name__} 失败: {e}")
    if last_error:
        log.error(f"{func.__name__} 全部重试失败: {last_error}")
    return None


def _fetch_em_sectors_fallback(category: str = "industry") -> list[dict]:
    """东方财富板块API备用数据源（当新浪API失败时使用）

    category: 'industry' 或 'concept'
    """
    from modules.http_client import session, EM_HEADERS
    sectors = []
    try:
        # 东方财富板块接口
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        if category == "industry":
            fs = "m:90+t:2"
        else:
            fs = "m:90+t:3"
        params = {
            "pn": 1, "pz": 100,
            "po": 1, "np": 1,
            "fltt": 2, "invt": 2,
            "fid": "f3",
            "fs": fs,
            "fields": "f12,f14,f2,f3,f104,f105",
        }
        resp = session.get(url, params=params, headers=EM_HEADERS, timeout=10)
        d = resp.json()
        if d.get("data") and d["data"].get("diff"):
            for item in d["data"]["diff"]:
                try:
                    sectors.append({
                        "code": str(item.get("f12", "")),
                        "name": str(item.get("f14", "")),
                        "change_pct": float(item.get("f3", 0)),
                        "stock_count": int(item.get("f104", 0)),
                    })
                except Exception:
                    continue
    except Exception as e:
        log.warning(f"东方财富板块API失败: {e}")
    return sectors

def get_realtime_quotes(codes: Optional[list[str]] = None) -> dict[str, StockQuote]:
    """获取实时行情，返回统一StockQuote模型

    策略：
    1. 有缓存且未过期则直接返回
    2. 无指定codes时，先获取全A股代码列表
    3. 用腾讯API批量拉行情（每批80只）
    4. 腾讯API失败时，从离线库构建行情数据兜底
    """
    global _realtime_cache, _realtime_cache_time

    now = time.time()
    if _realtime_cache and (now - _realtime_cache_time) < _config.realtime_cache_ttl:
        if codes is None:
            return _realtime_cache
        return {c: _realtime_cache[c] for c in codes if c in _realtime_cache}

    all_codes = codes or _get_all_stock_codes()
    if not all_codes:
        # 兜底：从离线库获取代码
        preset = get_preset_financials()
        if preset:
            all_codes = [c for c in preset.keys() if not c.startswith('9')]
            log.warning(f"股票代码列表为空，使用离线库 {len(all_codes)} 只")
        if not all_codes:
            log.error("无法获取任何股票代码")
            return {}

    quotes: dict[str, StockQuote] = {}
    batch_size = 80

    # 构建腾讯API所需的代码列表（sh6/sz0前缀）
    tx_code_map = {}
    for code in all_codes:
        if code.startswith('6'):
            tx_code_map[f"sh{code}"] = code
        else:
            tx_code_map[f"sz{code}"] = code

    tx_codes = list(tx_code_map.keys())

    # 将批次分组，用线程池并发拉取（4000+股票约50批次，并发8线程约6轮）
    batches = [tx_codes[i:i + batch_size] for i in range(0, len(tx_codes), batch_size)]

    def _fetch_batch(batch_idx: int, batch: list[str]) -> dict[str, StockQuote]:
        """拉取单批行情数据（含重试机制）"""
        batch_quotes: dict[str, StockQuote] = {}
        max_retries = 3
        import time as _time
        
        for attempt in range(1, max_retries + 1):
            try:
                url = 'https://qt.gtimg.cn/q=' + ','.join(batch)
                resp = _get_session().get(url, timeout=_config.timeout)
                break  # 成功，退出重试循环
            except requests.Timeout:
                if attempt < max_retries:
                    wait = 2 ** attempt  # 2s, 4s 指数退避
                    log.warning(f"获取行情超时: 批次 {batch_idx}, 第{attempt}次重试, 等待{wait}s")
                    _time.sleep(wait)
                    continue
                else:
                    log.warning(f"获取行情超时: 批次 {batch_idx}, 已重试{max_retries}次")
                    return batch_quotes
            except requests.RequestException as e:
                if attempt < max_retries and '429' in str(e):
                    _time.sleep(5)  # 限流等待5s
                    log.warning(f"获取行情被限流: 批次 {batch_idx}, 等待5s后重试")
                    continue
                elif attempt < max_retries:
                    wait = 2 ** attempt
                    log.warning(f"获取行情失败: 批次 {batch_idx}, {e}, 第{attempt}次重试")
                    _time.sleep(wait)
                    continue
                else:
                    log.error(f"获取行情失败: 批次 {batch_idx}, {e}, 已重试{max_retries}次")
                    return batch_quotes
        
        try:
            # 腾讯API返回GBK编码，优先用GBK解码
            try:
                text = resp.content.decode('gbk', errors='replace')
            except Exception:
                text = resp.text

            for line in text.strip().split(";"):
                line = line.strip()
                if not line or "~" not in line:
                    continue
                try:
                    parts = line.split("~")
                    if len(parts) < 48:
                        continue
                    raw_code = parts[2] if len(parts) > 2 else ""
                    if not raw_code or len(raw_code) != 6:
                        continue

                    # 过滤北交所/B股；科创板保留给策略层处理
                    if raw_code.startswith('9'):
                        continue

                    quote = StockQuote.from_tencent_parts(raw_code, parts)
                    if quote:
                        batch_quotes[raw_code] = quote
                except (IndexError, ValueError) as e:
                    log.debug(f"解析行情行失败: {e}")
                    continue

        except Exception as e:
            log.error(f"解析行情数据异常: 批次 {batch_idx}, {e}")
        return batch_quotes

    max_workers = min(8, len(batches)) if batches else 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_batch, idx, batch): idx for idx, batch in enumerate(batches)}
        for future in as_completed(futures):
            try:
                batch_quotes = future.result()
                quotes.update(batch_quotes)
            except Exception as e:
                log.warning(f"行情批次执行异常: {e}")

    # 如果腾讯API全部失败，用离线库兜底
    if not quotes:
        preset = get_preset_financials()
        if preset:
            log.warning("腾讯行情API全部失败，使用离线库构建行情数据")
            for code, fin in preset.items():
                if code.startswith('9'):
                    continue
                name = fin.get('name', '')
                if 'ST' in name or '*' in name:
                    continue
                # 用离线数据构建 StockQuote（无实时行情的降级版本）
                try:
                    quotes[code] = StockQuote(
                        code=code,
                        name=name,
                        price=float(fin.get('price', 0) or 0),
                        change_pct=float(fin.get('change_pct', 0) or 0),
                        volume=0,
                        amount=float(fin.get('market_cap', 0) * 1e8) if fin.get('market_cap', 0) else 0,
                        turnover=float(fin.get('turnover_rate', fin.get('turnover', 0)) or 0),
                        pe=float(fin.get('pe', 0) or 0),
                        pb=float(fin.get('pb', 0) or 0),
                        market_cap=float(fin.get('market_cap', 0) or 0),
                        high=0, low=0, open=0, prev_close=0,
                    )
                except Exception as e:
                    log.debug(f"离线数据构建失败: {code}, {e}")

    if not codes:
        _realtime_cache = quotes
        _realtime_cache_time = now

    log.info(f"获取行情完成: {len(quotes)} 只股票")
    return quotes


def get_eligible_quotes(codes: Optional[list[str]] = None) -> dict[str, StockQuote]:
    """获取可选股范围内的行情"""
    all_quotes = get_realtime_quotes(codes)
    return filter_eligible_stocks(all_quotes)


def get_financial_data(codes: list[str], use_cache: bool = True) -> dict[str, FinancialData]:
    """获取财务数据 - 使用旧版双API策略

    API1: RPT_F10_FINANCE_MAINFINADATA → ROE/毛利率/资产负债率/净利率
    API2: RPT_LICO_FN_CPD → 营收同比/净利同比
    """
    global _financial_cache, _financial_cache_time

    now = time.time()
    if use_cache and _financial_cache and (now - _financial_cache_time) < _config.financial_cache_ttl:
        cached = {c: _financial_cache[c] for c in codes if c in _financial_cache}
        if len(cached) == len(codes):
            return cached

    results: dict[str, FinancialData] = {}
    base_url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    dc_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://data.eastmoney.com/",
        "Accept": "*/*",
    }

    def fetch_one(code: str) -> Optional[FinancialData]:
        roe = 0.0
        gross_margin = 0.0
        debt_ratio = 0.0
        net_margin = 0.0
        rev_growth = 0.0
        profit_growth = 0.0
        pe = 0.0
        pb = 0.0
        name = ""

        try:
            params1 = {
                "reportName": "RPT_F10_FINANCE_MAINFINADATA",
                "columns": "REPORT_DATE_NAME,ROEJQ,XSMLL,ZCFZL,XSJLL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": 1,
                "pageSize": 1,
                "source": "WEB",
                "client": "WEB",
            }
            resp = _get_session().get(base_url, params=params1, headers=dc_headers, timeout=5)
            d = resp.json()
            if d.get("success") and d.get("result") and d["result"].get("data"):
                item = d["result"]["data"][0]
                if item.get("ROEJQ") is not None:
                    roe = float(item["ROEJQ"])
                if item.get("XSMLL") is not None:
                    gross_margin = float(item["XSMLL"])
                if item.get("ZCFZL") is not None:
                    debt_ratio = float(item["ZCFZL"])
                if item.get("XSJLL") is not None:
                    net_margin = float(item["XSJLL"])
        except Exception:
            pass

        try:
            params2 = {
                "reportName": "RPT_LICO_FN_CPD",
                "columns": "DATAYEAR,DATEMMDD,WEIGHTAVG_ROE,YSTZ,SJLTZ,XSMLL",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": 1,
                "pageSize": 1,
                "source": "WEB",
                "client": "WEB",
            }
            resp = _get_session().get(base_url, params=params2, headers=dc_headers, timeout=5)
            d = resp.json()
            if d.get("success") and d.get("result") and d["result"].get("data"):
                item = d["result"]["data"][0]
                if item.get("YSTZ") is not None:
                    rev_growth = float(item["YSTZ"])
                if item.get("SJLTZ") is not None:
                    profit_growth = float(item["SJLTZ"])
        except Exception:
            pass

        if roe == 0 and gross_margin == 0 and debt_ratio == 0 and rev_growth == 0:
            return None

        return FinancialData(
            code=code,
            name=name,
            pe=pe,
            pb=pb,
            roe=roe,
            market_cap=0,
            revenue_growth=rev_growth,
            profit_growth=profit_growth,
            debt_ratio=debt_ratio,
            gross_margin=gross_margin,
            net_margin=net_margin,
        )

    with ThreadPoolExecutor(max_workers=_config.calibrate_threads) as executor:
        futures = {executor.submit(fetch_one, code): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                result = future.result()
                if result:
                    results[code] = result
            except Exception as e:
                log.warning(f"获取财务数据异常: {code}, {e}")

    _financial_cache.update(results)
    _financial_cache_time = now

    log.info(f"获取财务数据完成: {len(results)}/{len(codes)}")
    return results



def get_financial_data_batch(codes: list[str]) -> dict[str, FinancialData]:
    """批量获取财务数据 - 使用东方财富分页API一次性获取

    相比逐只调用，API调用次数从 ~6000 降至 ~12。
    每页500条，只获取需要的字段。
    """
    results: dict[str, FinancialData] = {}
    code_set = set(codes)
    base_url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    dc_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://data.eastmoney.com/",
        "Accept": "*/*",
    }

    # API1: 主财务数据 (ROE/毛利率/负债率/净利率)
    page = 1
    while True:
        try:
            params = {
                "reportName": "RPT_F10_FINANCE_MAINFINADATA",
                "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,ROEJQ,XSMLL,ZCFZL,XSJLL",
                "pageNumber": page,
                "pageSize": 500,
                "source": "WEB",
                "client": "WEB",
            }
            resp = _get_session().get(base_url, params=params, headers=dc_headers, timeout=10)
            d = resp.json()
            if not d.get("success") or not d.get("result") or not d["result"].get("data"):
                break

            for item in d["result"]["data"]:
                code = item.get("SECURITY_CODE", "")
                if code not in code_set:
                    continue
                roe = float(item["ROEJQ"]) if item.get("ROEJQ") is not None else 0
                gross_margin = float(item["XSMLL"]) if item.get("XSMLL") is not None else 0
                debt_ratio = float(item["ZCFZL"]) if item.get("ZCFZL") is not None else 0
                net_margin = float(item["XSJLL"]) if item.get("XSJLL") is not None else 0
                name = item.get("SECURITY_NAME_ABBR", "")

                if code not in results:
                    results[code] = FinancialData(
                        code=code, name=name, pe=0, pb=0, roe=roe,
                        market_cap=0, revenue_growth=0, profit_growth=0,
                        debt_ratio=debt_ratio, gross_margin=gross_margin,
                        net_margin=net_margin,
                    )
                else:
                    fd = results[code]
                    if name: fd.name = name
                    if roe: fd.roe = roe
                    if gross_margin: fd.gross_margin = gross_margin
                    if debt_ratio: fd.debt_ratio = debt_ratio
                    if net_margin: fd.net_margin = net_margin

            total_count = d["result"].get("count", 0)
            if page * 500 >= total_count:
                break
            page += 1
        except Exception as e:
            log.warning(f"批量财务数据API1第{page}页失败: {e}")
            break

    # API2: 营收/净利增速
    page = 1
    while True:
        try:
            params = {
                "reportName": "RPT_LICO_FN_CPD",
                "columns": "SECURITY_CODE,YSTZ,SJLTZ",
                "pageNumber": page,
                "pageSize": 500,
                "source": "WEB",
                "client": "WEB",
            }
            resp = _get_session().get(base_url, params=params, headers=dc_headers, timeout=10)
            d = resp.json()
            if not d.get("success") or not d.get("result") or not d["result"].get("data"):
                break

            for item in d["result"]["data"]:
                code = item.get("SECURITY_CODE", "")
                if code not in code_set:
                    continue
                rev_growth = float(item["YSTZ"]) if item.get("YSTZ") is not None else 0
                profit_growth = float(item["SJLTZ"]) if item.get("SJLTZ") is not None else 0

                if code in results:
                    if rev_growth: results[code].revenue_growth = rev_growth
                    if profit_growth: results[code].profit_growth = profit_growth
                elif rev_growth or profit_growth:
                    results[code] = FinancialData(
                        code=code, name="", pe=0, pb=0, roe=0,
                        market_cap=0, revenue_growth=rev_growth,
                        profit_growth=profit_growth, debt_ratio=0,
                        gross_margin=0, net_margin=0,
                    )

            total_count = d["result"].get("count", 0)
            if page * 500 >= total_count:
                break
            page += 1
        except Exception as e:
            log.warning(f"批量财务数据API2第{page}页失败: {e}")
            break

    # 过滤掉全为0的记录
    results = {k: v for k, v in results.items()
               if v.roe or v.gross_margin or v.debt_ratio or v.revenue_growth or v.profit_growth}

    log.info(f"批量财务数据: {len(results)}/{len(codes)} 只")
    return results

def search_stock(keyword: str) -> list[dict]:
    """搜索股票"""
    try:
        url = _config.smartbox_url.format(keyword=keyword, token=_config.smartbox_token)
        resp = _get_session().get(url, timeout=5)
        resp.encoding = "utf-8"
        results = []
        for line in resp.text.strip().split(";"):
            line = line.strip()
            if "~" not in line:
                continue
            parts = line.split("~")
            if len(parts) >= 7:
                results.append({
                    "code": parts[2],
                    "name": parts[1],
                    "type": parts[3],
                    "market": parts[4] if len(parts) > 4 else "",
                })
        return results
    except requests.Timeout:
        log.warning(f"搜索超时: {keyword}")
        return []
    except requests.RequestException as e:
        log.error(f"搜索失败: {keyword}, {e}")
        return []



def search_stock_fuzzy(query: str) -> list[dict]:
    """模糊搜索股票（名称+代码），返回匹配的股票列表（含行情和评分）

    统一搜索接口，供 POST /api/search 和 GET /api/search_stock 共用。
    """
    import os
    from modules.http_client import session, HEADERS, EM_HEADERS
    from modules.scoring import evaluate_stock

    query = query.strip()
    if not query or len(query) < 1:
        return []

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
                lines_resp = resp.text.strip().split(';')
                for line in lines_resp:
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
                        pb = float(parts[46]) if parts[46] and parts[46] != '-' else 0
                    except (ValueError, TypeError):
                        pb = 0

                    for ms in matched_stocks:
                        if ms["code"] == code:
                            ms.update({
                                "name": parts[1] or ms["name"],
                                "price": price, "change_pct": change_pct,
                                "market_cap": total_cap_yi,
                                "pe": pe, "pb": pb,
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
                "score": 0,
                "dimensions": {"profitability": 0, "growth": 0, "health": 0, "valuation": 0, "cashflow": 0},
                "reasons": [], "buy_sell": None,
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results

def clear_cache() -> None:
    """清除所有缓存"""
    global _realtime_cache, _realtime_cache_time, _financial_cache, _financial_cache_time
    _realtime_cache = {}
    _realtime_cache_time = 0
    _financial_cache = {}
    _financial_cache_time = 0
    log.info("数据缓存已清除")


# === 兼容旧版API的辅助函数 ===

def get_financial_data_fast(code: str) -> Optional[dict]:
    """获取单个股票的简要财务数据（快速版，兼容旧版API）"""
    result = get_financial_data([code])
    if code in result:
        f = result[code]
        return {
            "code": f.code,
            "roe": f.roe,
            "gross_margin": f.gross_margin,
            "debt_ratio": f.debt_ratio,
            "pb": f.pb,
            "net_margin": f.net_margin,
            "rev_growth": f.revenue_growth,
            "profit_growth": f.profit_growth,
        }
    return None


def get_preset_financials() -> dict:
    """加载预设的离线财务数据"""
    import os
    offline_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'offline_stocks.json')
    try:
        with open(offline_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {s['code']: s for s in data.get('stocks', [])}
    except Exception:
        return {}


def get_stock_industry(code: str) -> dict:
    """获取股票所属行业

    策略：
    1. 先从本地缓存查找（data/industry_cache.json）
    2. 本地缓存没有，尝试从东方财富API获取
    3. API也失败，返回"其他"
    """
    global _industry_cache, _industry_cache_time

    # 1. 检查内存缓存
    now = time.time()
    if code in _industry_cache and (now - _industry_cache_time) < 86400:
        return _industry_cache[code]

    # 2. 检查本地文件缓存
    try:
        import os
        import json
        cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'industry_cache.json')
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as f:
                local_cache = json.load(f)
            if code in local_cache:
                result = {"industry": local_cache[code], "sector_type": "default"}
                _industry_cache[code] = result
                _industry_cache_time = now
                return result
    except Exception:
        pass

    # 3. 尝试从东方财富F10 CompanySurvey API获取行业信息
    try:
        market_prefix = "SH" if code.startswith("6") else "SZ"
        full_code = f"{market_prefix}{code}"
        url = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax"
        resp = _get_session().get(url, params={"code": full_code}, timeout=_config.timeout,
                                   headers={"User-Agent": "Mozilla/5.0", "Referer": "https://emweb.securities.eastmoney.com/"})
        data = resp.json()
        jbzl = data.get("jbzl", [])
        if jbzl:
            row = jbzl[0]
            industry_raw = row.get("INDUSTRYCSRC1", "")
            if industry_raw:
                industry = _shorten_industry(industry_raw)
                result = {"industry": industry, "sector_type": "default"}
                _industry_cache[code] = result
                _industry_cache_time = now
                _save_industry_to_cache(code, industry)
                return result
    except Exception as e:
        log.debug(f"获取行业信息失败(F10): {code}, {e}")

    # 4. 兜底返回"其他"
    return {"industry": "其他", "sector_type": "default"}


def fetch_sina_sectors(category: str) -> list[dict]:
    """获取新浪板块行情"""
    sectors = []
    try:
        url = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
        node_map = {"industry": "hangye_ZA01", "class": "gn_hwqc"}
        node = node_map.get(category, "gn_hwqc")

        for page in range(1, 4):
            params = {
                'page': page, 'num': 40,
                'sort': 'changepercent', 'asc': 0,
                'node': node, '_s_r_a': 'page'
            }
            r = _get_session().get(url, params=params, timeout=10,
                                     headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'})
            r.encoding = 'utf-8'
            data = r.json()
            if not data:
                break
            for item in data:
                try:
                    name = item.get('name', '')
                    change_pct = float(item.get('changepercent', 0) or 0)
                    sectors.append({
                        'name': name,
                        'change_pct': change_pct,
                        'code': item.get('code', ''),
                    })
                except Exception:
                    continue
    except Exception as e:
        log.warning(f"获取新浪板块失败: {e}")
    return sectors


def _shorten_industry(industry_raw: str) -> str:
    """将CSRC行业分类名精简为2-4字短名，适合UI紧凑展示"""
    if "-" in industry_raw:
        sub = industry_raw.split("-")[-1]
    else:
        sub = industry_raw
    # 常见行业精简映射
    short_map = {
        "电力、热力生产和供应业": "电力", "水的生产和供应业": "水务",
        "燃气生产和供应业": "燃气",
        "有色金属冶炼和压延加工业": "有色", "黑色金属冶炼和压延加工业": "钢铁",
        "化学原料和化学制品制造业": "化工", "医药制造业": "医药",
        "专用设备制造业": "机械", "通用设备制造业": "装备",
        "计算机、通信和其他电子设备制造业": "电子", "电气机械和器材制造业": "电气",
        "汽车制造业": "汽车", "铁路、船舶、航空航天和其他运输设备制造业": "交运设备",
        "农副食品加工业": "农业", "食品制造业": "食品",
        "酒、饮料和精制茶制造业": "饮料", "纺织业": "纺织",
        "纺织服装、服饰业": "服装", "皮革、毛皮、羽毛及其制品和制鞋业": "皮革",
        "木材加工和木、竹、藤、棕、草制品业": "木材",
        "家具制造业": "家具", "造纸和纸制品业": "造纸",
        "印刷和记录媒介复制业": "印刷", "文教、工美、体育和娱乐用品制造业": "文娱用品",
        "石油加工、炼焦和核燃料加工业": "石化", "非金属矿物制品业": "建材",
        "金属制品业": "金属制品", "仪器仪表制造业": "仪表",
        "废弃资源综合利用业": "环保", "金属制品、机械和设备修理业": "设备修理",
        "房地产": "地产", "建筑业": "建筑",
        "批发和零售业": "商贸", "交通运输、仓储和邮政业": "交运",
        "住宿和餐饮业": "酒店餐饮", "信息传输、软件和信息技术服务业": "IT",
        "金融业": "金融", "租赁和商务服务业": "租赁商务",
        "科学研究和技术服务业": "科研服务", "水利、环境和公共设施管理业": "公共设施",
        "居民服务、修理和其他服务业": "居民服务",
        "教育": "教育", "卫生和社会工作": "医疗",
        "文化、体育和娱乐业": "文体娱乐", "公共管理、社会保障和社会组织": "公共管理",
        "国际组织": "国际",
        "农林牧渔业": "农业", "采矿业": "矿业",
        "制造业": "制造", "软件和信息技术服务业": "软件",
        "通信和其他电子设备制造业": "通信",
        "其他制造业": "其他制造", "研究和试验发展": "科研", "专业技术服务业": "技术服务",
        "互联网和相关服务": "互联网", "软件和信息技术服务业": "软件",
        "资本市场服务": "证券", "货币金融服务": "银行", "保险业": "保险",
        "开采专业及辅助性活动": "矿业服务", "开采专业": "矿业服务",
    }
    return short_map.get(sub, sub[:4] if len(sub) > 4 else sub)


def _save_industry_to_cache(code: str, industry: str) -> None:
    """将行业信息写入本地缓存文件以持久化"""
    try:
        import os, json
        cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'industry_cache.json')
        local_cache = {}
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as f:
                local_cache = json.load(f)
        local_cache[code] = industry
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(local_cache, f, ensure_ascii=False)
    except Exception:
        pass



def _batch_save_industry_cache(updates: dict[str, str]) -> None:
    """批量将行业信息写入本地缓存文件（原子写入）

    Args:
        updates: {code: industry} 字典
    """
    if not updates:
        return
    try:
        import os
        import json
        import tempfile
        cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'industry_cache.json')
        local_cache = {}
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as f:
                local_cache = json.load(f)
        # 合并更新
        local_cache.update(updates)
        # 原子写入：先写临时文件，再rename
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(suffix='.json', dir=os.path.dirname(cache_path))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(local_cache, f, ensure_ascii=False)
            os.replace(tmp_path, cache_path)
        except Exception:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    except Exception as e:
        log.debug(f"批量保存行业缓存失败: {e}")


def preload_industry_cache(codes: list[str]) -> dict[str, dict]:
    """批量预加载行业缓存，避免逐只调用API

    策略：
    1. 先加载本地文件缓存到内存
    2. 对未缓存的代码，使用东方财富板块成分API批量获取
    3. 对仍无法获取的，回退到逐只调用get_stock_industry

    Args:
        codes: 需要预加载的股票代码列表

    Returns:
        已缓存的行业信息字典 {code: {"industry": str, "sector_type": str}}
    """
    global _industry_cache, _industry_cache_time

    if not codes:
        return {}

    now = time.time()
    result: dict[str, dict] = {}
    need_fetch: list[str] = []

    # 1. 先从内存缓存获取
    for code in codes:
        if code in _industry_cache and (now - _industry_cache_time) < 86400:
            result[code] = _industry_cache[code]
        else:
            need_fetch.append(code)

    if not need_fetch:
        log.info(f"行业缓存预加载: 全部 {len(codes)} 只已在内存缓存中")
        return result

    # 2. 加载本地文件缓存
    local_cache = {}
    try:
        import os
        cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'industry_cache.json')
        if os.path.exists(cache_path):
            with open(cache_path, 'r', encoding='utf-8') as f:
                local_cache = json.load(f)
    except Exception as e:
        log.debug(f"加载本地行业缓存失败: {e}")

    # 从本地缓存补充
    still_need: list[str] = []
    for code in need_fetch:
        if code in local_cache:
            industry_info = {"industry": local_cache[code], "sector_type": "default"}
            _industry_cache[code] = industry_info
            result[code] = industry_info
        else:
            still_need.append(code)

    if not still_need:
        _industry_cache_time = now
        log.info(f"行业缓存预加载: 内存 {len(result) - len(need_fetch)} + 文件 {len(need_fetch)} = {len(result)} 只")
        return result

    # 3. 尝试批量获取：通过东方财富板块成分API
    # 使用板块→股票映射，反向构建股票→板块映射
    batch_updates: dict[str, str] = {}
    still_need_set = set(still_need)
    try:
        from modules.http_client import session, EM_HEADERS

        # 获取行业板块列表
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": 200,  # 行业板块数量
            "po": 1, "np": 1,
            "fltt": 2, "invt": 2,
            "fid": "f3",
            "fs": "m:90+t:2",  # 行业板块
            "fields": "f12,f14",  # 板块代码、板块名称
        }
        resp = session.get(url, params=params, headers=EM_HEADERS, timeout=10)
        d = resp.json()

        if d.get("data") and d["data"].get("diff"):
            sectors = d["data"]["diff"]
            log.info(f"行业缓存预加载: 获取到 {len(sectors)} 个行业板块，开始获取成分股...")

            # 对每个板块获取成分股
            for sector in sectors:
                sector_code = str(sector.get("f12", ""))
                sector_name = str(sector.get("f14", ""))
                if not sector_code or not sector_name:
                    continue

                # 如果该板块的所有股票都已缓存，跳过
                # （简单判断：仍需获取的股票数 > 0 才继续）
                if not still_need_set:
                    break

                # 获取板块成分股
                try:
                    constituent_url = "https://push2.eastmoney.com/api/qt/clist/get"
                    constituent_params = {
                        "pn": 1, "pz": 500,  # 每个板块最多500只
                        "po": 1, "np": 1,
                        "fltt": 2, "invt": 2,
                        "fid": "f3",
                        "fs": f"b:{sector_code}",
                        "fields": "f12",  # 股票代码
                    }
                    c_resp = session.get(constituent_url, params=constituent_params, headers=EM_HEADERS, timeout=8)
                    c_data = c_resp.json()

                    if c_data.get("data") and c_data.get("data", {}).get("diff"):
                        for stock_item in c_data["data"]["diff"]:
                            stock_code = str(stock_item.get("f12", ""))
                            if stock_code and stock_code in still_need_set:
                                # 精简行业名称
                                short_industry = _shorten_industry(sector_name)
                                batch_updates[stock_code] = short_industry
                                industry_info = {"industry": short_industry, "sector_type": "default"}
                                _industry_cache[stock_code] = industry_info
                                result[stock_code] = industry_info
                                still_need_set.discard(stock_code)
                except Exception as e:
                    log.debug(f"获取板块 {sector_name} 成分股失败: {e}")
                    continue

            # 批量保存到文件缓存
            if batch_updates:
                _batch_save_industry_cache(batch_updates)
                log.info(f"行业缓存预加载: 批量获取 {len(batch_updates)} 只行业信息")

    except Exception as e:
        log.warning(f"批量获取行业信息失败: {e}")

    # 4. 对仍未获取的股票，回退到逐只调用（但会利用已填充的内存缓存）
    final_need = [c for c in still_need if c not in batch_updates]
    if final_need:
        log.info(f"行业缓存预加载: {len(final_need)} 只需逐只获取行业信息...")
        # 使用线程池并发获取
        from concurrent.futures import ThreadPoolExecutor

        def _fetch_one(code: str) -> tuple[str, dict]:
            try:
                info = get_stock_industry(code)
                return (code, info)
            except Exception:
                return (code, {"industry": "其他", "sector_type": "default"})

        with ThreadPoolExecutor(max_workers=8) as executor:
            for code, info in executor.map(_fetch_one, final_need):
                result[code] = info

    _industry_cache_time = now
    fetched_count = len(final_need) if final_need else 0
    log.info(f"行业缓存预加载完成: 总计 {len(codes)} 只, 已缓存 {len(result)} 只, 逐只获取 {fetched_count} 只")
    return result


# 行业缓存
_industry_cache: dict = {}
_industry_cache_time: float = 0
