"""Tests for modules.logger — file rotation, request_id propagation, format."""
import logging
import os
import re

import pytest
from logging.handlers import RotatingFileHandler

from modules.logger import (
    log,
    setup_logger,
    set_request_id,
    get_request_id,
    _request_id_var,
)


def test_log_is_logger_instance():
    assert isinstance(log, logging.Logger)
    assert log.name == "stock_picker"


def test_setup_logger_idempotent():
    """Calling setup_logger twice should not double-add handlers."""
    initial = len(log.handlers)
    result = setup_logger()
    assert result is log
    assert len(log.handlers) == initial


def test_log_format_includes_request_id():
    set_request_id("test-rid-12345")
    # Find a console handler to format against
    console = [h for h in log.handlers if not isinstance(h, RotatingFileHandler)]
    if not console:
        pytest.skip("no console handler configured")
    formatter = console[0].formatter
    assert formatter is not None
    assert "%(request_id)s" in formatter._fmt


def test_set_request_id_auto_generates():
    rid = set_request_id()
    assert isinstance(rid, str)
    assert len(rid) == 16
    assert get_request_id() == rid


def test_set_request_id_explicit():
    rid = set_request_id("custom-id-12345")
    assert rid == "custom-id-12345"
    assert get_request_id() == "custom-id-12345"


def test_get_request_id_default_dash():
    _request_id_var.set(None)
    assert get_request_id() == "-"


def test_file_handler_created():
    """Logger should have a RotatingFileHandler pointing to logs/app.log."""
    rh = [h for h in log.handlers if isinstance(h, RotatingFileHandler)]
    assert len(rh) >= 1
    assert rh[0].baseFilename.endswith("app.log")


def test_log_dir_exists():
    assert os.path.isdir("logs"), "logs/ directory should be created by setup_logger"


def test_request_id_unique_across_calls():
    rids = {set_request_id() for _ in range(5)}
    assert len(rids) == 5


def test_log_writes_to_file():
    """Writing a log message produces content in logs/app.log with request_id."""
    set_request_id("file-rid-uniq-99")
    log.info("test_message_for_file_output")
    # Force flush
    for h in log.handlers:
        h.flush()
    log_path = "logs/app.log"
    assert os.path.exists(log_path), f"expected {log_path} to exist"
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "test_message_for_file_output" in content
    assert "file-rid-uniq-99" in content


def test_handler_uses_rotating_file_settings():
    """Verify rotation parameters: 10MB max, 10 backups."""
    rh = [h for h in log.handlers if isinstance(h, RotatingFileHandler)][0]
    assert rh.maxBytes == 10 * 1024 * 1024
    assert rh.backupCount == 10


def test_request_id_in_log_record():
    """RequestIdFilter injects request_id into the LogRecord."""
    set_request_id("rid-xyz-789")
    rec = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=None, exc_info=None,
    )
    # Apply all filters
    for h in log.handlers:
        for f in h.filters:
            f.filter(rec)
    assert getattr(rec, "request_id", None) == "rid-xyz-789"


def test_request_id_dash_when_unset():
    """When no request_id set, log records get '-'."""
    _request_id_var.set(None)
    rec = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=None, exc_info=None,
    )
    for h in log.handlers:
        for f in h.filters:
            f.filter(rec)
    assert getattr(rec, "request_id", None) == "-"
