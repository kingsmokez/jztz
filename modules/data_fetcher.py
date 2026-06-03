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
                            # 排除非A股：北交所(4/8/9开头)、科创板(688/689开头)、B股(2开头)、ST等
                            if code[0] in ('2', '4', '8', '9') or code.startswith(('688', '689')):
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
        """拉取单批行情数据"""
        batch_quotes: dict[str, StockQuote] = {}
        try:
            url = 'https://qt.gtimg.cn/q=' + ','.join(batch)
            resp = _get_session().get(url, timeout=_config.timeout)
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

                    # 过滤北交所和科创板
                    if raw_code.startswith('9') or raw_code.startswith('688'):
                        continue

                    quote = StockQuote.from_tencent_parts(raw_code, parts)
                    if quote:
                        batch_quotes[raw_code] = quote
                except (IndexError, ValueError) as e:
                    log.debug(f"解析行情行失败: {e}")
                    continue

        except requests.Timeout:
            log.warning(f"获取行情超时: 批次 {batch_idx}")
        except requests.RequestException as e:
            log.error(f"获取行情失败: 批次 {batch_idx}, {e}")
        return batch_quotes

    # 使用线程池并发拉取，8线程并行
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
                if code.startswith('9') or code.startswith('688'):
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
            resp = _get_session().get(base_url, params=params1, headers=dc_headers, timeout=3)
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
            resp = _get_session().get(base_url, params=params2, headers=dc_headers, timeout=3)
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


# 行业缓存
_industry_cache: dict = {}
_industry_cache_time: float = 0
