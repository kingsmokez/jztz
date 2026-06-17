"""Health check endpoints for liveness/readiness probes (k8s/Docker)."""
from __future__ import annotations

import os
import time
from typing import Any, Dict

from flask import Blueprint, jsonify

from modules.cache_manager import cache

health_bp = Blueprint("health", __name__, url_prefix="/api")


def _check_eastmoney(timeout: float = 3.0) -> Dict[str, Any]:
    """Ping Eastmoney quote API. Non-critical (offline fallback exists)."""
    try:
        import requests
        r = requests.get(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params={"pn": 1, "pz": 1, "fid": "f3"},
            timeout=timeout,
        )
        return {
            "status": "ok" if r.status_code == 200 else "error",
            "http": r.status_code,
            "latency_ms": int(r.elapsed.total_seconds() * 1000),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)[:120]}


def _check_filesystem() -> Dict[str, Any]:
    """Verify log/data dirs are writable. Critical."""
    details: Dict[str, str] = {}
    ok = True
    for d in ("logs", "data"):
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, ".health_check")
            with open(test, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(test)
            details[d] = "writable"
        except Exception as e:
            ok = False
            details[d] = f"error: {str(e)[:60]}"
    return {"status": "ok" if ok else "error", **details}


def _check_cache() -> Dict[str, Any]:
    """Verify cache manager responds. Critical."""
    try:
        n = cache.size()
        return {"status": "ok", "keys": n}
    except Exception as e:
        return {"status": "error", "error": str(e)[:120]}


@health_bp.get("/live")
def liveness():
    """Liveness probe — process is alive. Always 200."""
    return jsonify({"status": "alive", "ts": int(time.time())}), 200


@health_bp.get("/ready")
def readiness():
    """Readiness probe — 200 only if critical deps OK."""
    fs = _check_filesystem()
    cm = _check_cache()
    eastmoney = _check_eastmoney(timeout=2.0)
    components = {
        "eastmoney": eastmoney,
        "filesystem": fs,
        "cache": cm,
    }
    # Eastmoney is non-critical (we have offline fallback).
    is_ready = fs["status"] == "ok" and cm["status"] == "ok"
    return jsonify({
        "status": "ready" if is_ready else "not_ready",
        "components": components,
        "ts": int(time.time()),
    }), (200 if is_ready else 503)


@health_bp.get("/health")
def health():
    """Detailed health info — never 5xx even when downstream is down."""
    fs = _check_filesystem()
    cm = _check_cache()
    eastmoney = _check_eastmoney(timeout=3.0)
    components = {
        "eastmoney": eastmoney,
        "filesystem": fs,
        "cache": cm,
    }
    # 熔断器状态 (每个 upstream)
    try:
        from modules.circuit_breaker import get_all_states
        circuits = get_all_states()
    except Exception:
        circuits = {}
    overall = "ok" if all(c.get("status") == "ok" for c in components.values()) else "degraded"
    return jsonify({
        "status": overall,
        "version": "v17",
        "components": components,
        "circuits": circuits,
        "ts": int(time.time()),
    }), 200
