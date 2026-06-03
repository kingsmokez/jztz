"""Prometheus /api/metrics endpoint.

Exposes application + process metrics in Prometheus text format
(``Content-Type: text/plain; version=0.0.4``).

Custom domain metrics live in ``modules/metrics``.  A periodic
``_ProcessCollector`` update keeps process CPU / RSS / uptime fresh
on every scrape (cheap — single ``open`` of /proc).

Example::

    $ curl -s http://localhost:5000/api/metrics | head
    # HELP jztz_cache_hits_total Number of cache lookups that returned a fresh value.
    # TYPE jztz_cache_hits_total counter
    jztz_cache_hits_total{key_prefix="quotes"} 17.0
"""
from __future__ import annotations

import time
from typing import Any

from flask import Blueprint, Response, g, request

from modules.metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    _ProcessCollector,
    generate_latest,
)

# CONTENT_TYPE_LATEST already includes the charset.  Pass the bare
# media type to Flask so it doesn't append a second charset.
CONTENT_TYPE = "text/plain"
CONTENT_TYPE_VERSION = "version=0.0.4"

metrics_bp = Blueprint("metrics", __name__, url_prefix="/api")

_process = _ProcessCollector()


def _endpoint_label() -> str:
    """Flask's url_rule is None for 404s; use a stable label."""
    rule = request.url_rule
    return rule.rule if rule is not None else "<unknown>"


@metrics_bp.before_app_request
def _start_timer() -> None:
    g._metrics_start = time.perf_counter()


@metrics_bp.after_app_request
def _record_metrics(response: Any) -> Any:
    start = getattr(g, "_metrics_start", None)
    if start is None:
        return response
    duration = time.perf_counter() - start
    endpoint = _endpoint_label()
    HTTP_REQUESTS.labels(
        method=request.method,
        endpoint=endpoint,
        status=str(response.status_code),
    ).inc()
    HTTP_REQUEST_DURATION.labels(
        method=request.method,
        endpoint=endpoint,
    ).observe(duration)
    return response


@metrics_bp.get("/metrics")
def metrics() -> Response:
    """Return Prometheus text-format metrics."""
    _process.update()
    # Explicit Content-Type so Prometheus scrapers see the version
    # token they expect.  The (body, status, headers) tuple form
    # avoids Flask's auto-suffixing of ``; charset=utf-8``.
    return Response(
        generate_latest(),
        status=200,
        headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
    )
