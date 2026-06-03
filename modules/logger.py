"""统一日志配置 — 文件 + 控制台 + 请求 ID 串联"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from typing import Optional

from flask import g, has_request_context

_request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """注入 request_id 到 LogRecord (用于跨函数日志串联)"""

    def filter(self, record: logging.LogRecord) -> bool:
        # 优先从 context 取, 再次从 flask.g 取 (请求作用域), 最后 "-"
        rid = _request_id_var.get() or "-"
        if rid == "-" and has_request_context():
            rid = getattr(g, "request_id", "-") or "-"
        record.request_id = rid
        return True


def setup_logger(
    name: str = "stock_picker",
    level: int = logging.INFO,
    log_dir: str = "logs",
    log_file: str = "app.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 10,
) -> logging.Logger:
    """创建或返回已配置的 logger; 同名 logger 重复调用不会重复添加 handler"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(request_id)s] %(name)s %(filename)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    rid_filter = RequestIdFilter()

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(rid_filter)
    logger.addHandler(console)

    # 滚动文件 (10MB × 10)
    try:
        os.makedirs(log_dir, exist_ok=True)
        file_path = os.path.join(log_dir, log_file)
        fh = RotatingFileHandler(
            file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        fh.addFilter(rid_filter)
        logger.addHandler(fh)
    except Exception:
        # 文件日志失败不应让 app 崩溃 (例如权限/磁盘满)
        pass

    return logger


def set_request_id(rid: Optional[str] = None) -> str:
    """设置当前 request_id. 若未提供则生成 16 位 hex. 返回实际使用的 rid."""
    rid = rid or uuid.uuid4().hex[:16]
    _request_id_var.set(rid)
    return rid


def get_request_id() -> str:
    """获取当前 request_id. 无则返回 '-'."""
    return _request_id_var.get() or "-"


log = setup_logger()
