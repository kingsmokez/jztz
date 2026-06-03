"""
量价共振·尾盘选股 - 多数据源代理服务器
启动: python server.py
访问: http://localhost:5577

数据源:
  股票列表: akshare
  实时行情: 新浪财经 (hq.sinajs.cn)
  流通市值: 腾讯财经 (qt.gtimg.cn) - 单位:亿元
  K线数据: 腾讯财经 (web.ifzq.gtimg.cn)
  指数行情: 东方财富 (push2.eastmoney.com)
"""
from flask import Flask, request, jsonify, send_from_directory
import requests as rq
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
import time
import re
import os
import ssl
import urllib3

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__, static_folder='static', static_url_path='')

# 自定义SSL适配器 - 解决Windows SSL连接问题
class SSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3
        except AttributeError:
            pass
        try:
            ctx.options |= ssl.OP_NO_COMPRESSION
        except AttributeError:
            pass
        try:
            ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        except Exception:
            pass
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)

# 创建全局session，禁用SSL验证和代理
session = rq.Session()
session.mount('https://', SSLAdapter())
session.verify = False
session.trust_env = False  # 关键：禁用环境代理
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Connection': 'keep-alive',
})

HEADERS_SINA = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://finance.sina.com.cn/',
    'Accept': '*/*',
}

HEADERS_TX = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://gu.qq.com/',
}

HEADERS_EM = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://quote.eastmoney.com/',
}

_cache = {}


@app.after_request
def cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    return resp


@app.route('/')
def idx():
    return send_from_directory('static', 'index.html')


def parse_sina_quote(text):
    stocks = {}
    pattern = r'var hq_str_(\w+)="(.*?)";'
    for match in re.finditer(pattern, text):
        code = match.group(1)
        content = match.group(2)
        if not content:
            continue
        fields = content.split(',')
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
            pct_change = round((price - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0
            pure_code = code[2:]
            market = '1' if code.startswith('sh') else '0'
            stocks[pure_code] = {
                'f2': price,
                'f3': pct_change,
                'f4': round(price - pre_close, 2),
                'f5': volume,
                'f6': amount,
                'f7': round((high - low) / pre_close * 100, 2) if pre_close > 0 else 0,
                'f8': 0,
                'f9': 0,
                'f10': 0,
                'f12': pure_code,
                'f13': market,
                'f14': fields[0],
                'f15': high,
                'f16': low,
                'f17': open_price,
                'f18': pre_close,
                'f20': 0,
                'f21': 0,
                'f23': 0,
            }
        except (ValueError, IndexError):
            continue
    return stocks


def get_sina_quote(codes):
    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
    try:
        r = session.get(url, headers=HEADERS_SINA, timeout=15)
        print(f"  [新浪] {len(codes)}只, status={r.status_code}, len={len(r.text)}")
        return parse_sina_quote(r.text)
    except Exception as e:
        print(f"  [新浪] 失败({len(codes)}只): {e}")
        return {}


def get_tencent_market_cap(codes):
    url = f"https://qt.gtimg.cn/q={','.join(codes)}"
    try:
        r = session.get(url, headers=HEADERS_TX, timeout=15)
        caps = {}
        pattern = r'v_(\w+)="(.*?)";'
        for match in re.finditer(pattern, r.text):
            full_code = match.group(1)
            content = match.group(2)
            if not content:
                continue
            fields = content.split('~')
            pure_code = full_code[2:]
            try:
                circ_mv = float(fields[45]) if len(fields) > 45 and fields[45] else 0
                caps[pure_code] = circ_mv
            except (ValueError, IndexError):
                caps[pure_code] = 0
        return caps
    except Exception as e:
        print(f"  [腾讯市值] 失败: {e}")
        return {}


def get_tencent_kline(symbol, count=60):
    try:
        r = session.get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
                    params={'param': f'{symbol},day,,,{count},qfq'},
                    timeout=10)
        d = r.json()
        if d.get('code') != 0:
            return None
        data = d.get('data', {})
        stock_key = list(data.keys())[0] if data else None
        if not stock_key:
            return None
        qfqday = data[stock_key].get('qfqday') or data[stock_key].get('day') or []
        klines = []
        for row in qfqday:
            if len(row) >= 6:
                klines.append(f"{row[0]},{row[1]},{row[2]},{row[3]},{row[4]},{row[5]}")
        return {'data': {'klines': klines}}
    except Exception as e:
        print(f"  [腾讯K线] 失败 {symbol}: {e}")
        return None


@app.route('/api/stock_list')
def stock_list():
    pn = request.args.get('pn', '1')
    pz = request.args.get('pz', '5000')

    key = f'list_{pn}_{pz}'
    if key in _cache and time.time() - _cache[key][0] < 60:
        return jsonify(_cache[key][1])

    try:
        import akshare as ak
        print(f"[stock_list] 获取股票代码列表...")
        code_df = ak.stock_info_a_code_name()
        print(f"[stock_list] 共 {len(code_df)} 只股票")

        start = (int(pn) - 1) * int(pz)
        end = start + int(pz)
        page_df = code_df.iloc[start:end]
        print(f"[stock_list] 本页 {len(page_df)} 只")

        sina_codes = []
        for _, row in page_df.iterrows():
            code = row['code']
            if code.startswith('6'):
                sina_codes.append(f'sh{code}')
            else:
                sina_codes.append(f'sz{code}')

        all_data = {}
        batch_size = 500
        for i in range(0, len(sina_codes), batch_size):
            batch = sina_codes[i:i+batch_size]
            print(f"[stock_list] 行情批次 {i//batch_size+1}: {len(batch)}只")
            batch_data = get_sina_quote(batch)
            all_data.update(batch_data)
            time.sleep(0.3)

        print(f"[stock_list] 获取市值数据...")
        cap_data = {}
        for i in range(0, len(sina_codes), batch_size):
            batch = sina_codes[i:i+batch_size]
            batch_caps = get_tencent_market_cap(batch)
            cap_data.update(batch_caps)
            time.sleep(0.3)

        for code, info in all_data.items():
            cap_yi = cap_data.get(code, 0)
            info['f21'] = cap_yi * 1e8

        diff = list(all_data.values())
        print(f"[stock_list] 最终获取 {len(diff)} 只行情数据 (含市值)")
        result = {'data': {'diff': diff, 'total': len(code_df)}}
        _cache[key] = (time.time(), result)
        return jsonify(result)
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        print(f"[stock_list] 失败:\n{err_msg}")
        return jsonify({'error': str(e), 'data': {'diff': [], 'total': 0}}), 500


@app.route('/api/kline')
def kline():
    code = request.args.get('code', '')
    market = request.args.get('market', '1')
    lmt = request.args.get('lmt', '60')
    if not code:
        return jsonify({'error': 'no code'}), 400

    key = f'kline_{market}_{code}'
    if key in _cache and time.time() - _cache[key][0] < 300:
        return jsonify(_cache[key][1])

    symbol = f'sh{code}' if market == '1' else f'sz{code}'
    data = get_tencent_kline(symbol, int(lmt))
    if data:
        _cache[key] = (time.time(), data)
        return jsonify(data)
    return jsonify({'error': 'kline fetch failed'}), 500


@app.route('/api/kline_batch', methods=['POST', 'OPTIONS'])
def kline_batch():
    if request.method == 'OPTIONS':
        return jsonify({})
    data = request.get_json(force=True)
    codes = data.get('codes', [])
    results = {}
    for item in codes[:200]:
        code = item.get('code', '')
        market = item.get('market', '1')
        key = f'kline_{market}_{code}'
        if key in _cache and time.time() - _cache[key][0] < 300:
            results[code] = _cache[key][1]
            continue
        symbol = f'sh{code}' if market == '1' else f'sz{code}'
        kd = get_tencent_kline(symbol, 60)
        results[code] = kd
        if kd:
            _cache[key] = (time.time(), kd)
        time.sleep(0.05)
    return jsonify(results)


@app.route('/api/index')
def index_quote():
    secid = request.args.get('secid', '1.000300')
    fields = request.args.get('fields', 'f43,f57,f58,f60,f170')

    # 方案1: 尝试东方财富
    try:
        r = session.get('https://push2.eastmoney.com/api/qt/stock/get',
                    params={'secid': secid, 'fields': fields},
                    timeout=10)
        if r.status_code == 200:
            return jsonify(r.json())
    except Exception as e:
        print(f"[东财指数] 失败: {e}")

    # 方案2: 使用腾讯接口获取指数
    try:
        # secid格式: 1.000300 -> sh000300
        market, code = secid.split('.')
        tx_code = f"sh{code}" if market == '1' else f"sz{code}"
        r = session.get(f"https://qt.gtimg.cn/q={tx_code}", timeout=10)
        if r.status_code == 200:
            # 解析腾讯数据
            pattern = r'v_(\w+)="(.*?)";'
            for match in re.finditer(pattern, r.text):
                content = match.group(2)
                if content:
                    fields_list = content.split('~')
                    if len(fields_list) > 10:
                        # 腾讯字段映射
                        return jsonify({
                            'data': {
                                'f43': float(fields_list[3]) if fields_list[3] else 0,  # 当前价
                                'f57': code,  # 代码
                                'f58': fields_list[1],  # 名称
                                'f60': float(fields_list[4]) if fields_list[4] else 0,  # 今开
                                'f170': float(fields_list[32]) if len(fields_list) > 32 and fields_list[32] else 0,  # 涨跌幅
                            }
                        })
    except Exception as e:
        print(f"[腾讯指数] 失败: {e}")

    # 方案3: 返回默认数据
    return jsonify({
        'data': {
            'f43': 0,
            'f57': '000300',
            'f58': '沪深300',
            'f60': 0,
            'f170': 0,
        },
        'error': 'API暂时不可用'
    })


if __name__ == '__main__':
    print('=' * 50)
    print(' 量价共振·尾盘选股 - 多数据源代理服务器')
    print(' 数据源: akshare + 新浪(行情) + 腾讯(市值+K线) + 东财(指数)')
    print(' 打开浏览器访问: http://localhost:5577')
    print('=' * 50)
    app.run(host='0.0.0.0', port=5577, debug=False)
