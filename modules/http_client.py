"""HTTP客户端模块 - 统一请求管理"""

from __future__ import annotations

from typing import Optional

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import os
from modules.config import Config
from modules.logger import log

_config = Config().data

_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    """获取全局requests Session（带连接池和重试）"""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.verify = os.environ.get('SSL_VERIFY', 'true').lower() != 'false'
        _session.trust_env = False
        _session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Referer": "https://gu.qq.com/",
        })
        retry_strategy = Retry(total=2, backoff_factor=1)
        adapter = HTTPAdapter(max_retries=retry_strategy)
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
    return _session


def safe_get(url: str, timeout: int = 0, **kwargs) -> Optional[requests.Response]:
    """安全GET请求 - 统一异常处理"""
    actual_timeout = timeout or _config.timeout
    try:
        resp = get_session().get(url, timeout=actual_timeout, **kwargs)
        resp.raise_for_status()
        return resp
    except requests.Timeout:
        log.warning(f"请求超时: {url}")
        return None
    except requests.HTTPError as e:
        log.warning(f"HTTP错误: {url}, status={e.response.status_code}")
        return None
    except requests.RequestException as e:
        log.error(f"请求失败: {url}, {e}")
        return None


# 兼容旧代码：模块级别导出
session = get_session()

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
EM_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://quote.eastmoney.com/'
}
DC_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Referer': 'https://data.eastmoney.com/'
}