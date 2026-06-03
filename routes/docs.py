"""OpenAPI / Swagger UI blueprint.

Serves the machine-readable OpenAPI 3.0 spec at ``/api/openapi.json``
and a self-contained Swagger UI page at ``/api/docs``.

The HTML is served from disk (no CDN dependency) so it works in
fully-offline environments.  Swagger UI assets are loaded from
unpkg.com by the browser; if the user is offline, the HTML still
loads and shows a warning — the JSON spec remains fetchable.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from flask import Blueprint, Response, current_app

docs_bp = Blueprint("docs", __name__, url_prefix="/api")


def _load_spec() -> Dict[str, Any]:
    """Load the OpenAPI spec from docs/openapi.json.

    Cached after first successful load.  Falls back to an empty spec
    if the file is missing so the blueprint still mounts.
    """
    if hasattr(_load_spec, "_cache"):
        return _load_spec._cache  # type: ignore[attr-defined]
    spec_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "docs",
        "openapi.json",
    )
    try:
        with open(spec_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        current_app.logger.warning(f"openapi.json not found at {spec_path}")
        data = {"openapi": "3.0.3", "info": {"title": "jztz_v17", "version": "v20"}, "paths": {}}
    _load_spec._cache = data  # type: ignore[attr-defined]
    return data


# Pin a specific Swagger UI version for reproducibility.
_SWAGGER_UI_VERSION = "5.17.14"
_SWAGGER_UI_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>jztz_v17 API Docs</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@{ver}/swagger-ui.css">
  <style>
    body {{ margin: 0; padding: 0; }}
    .topbar {{ display: none; }}
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@{ver}/swagger-ui-bundle.js" crossorigin></script>
  <script>
    window.onload = function() {{
      window.ui = SwaggerUIBundle({{
        url: "{spec_url}",
        dom_id: "#swagger-ui",
        deepLinking: true,
        presets: [
          SwaggerUIBundle.presets.apis
        ],
        layout: "BaseLayout"
      }});
    }};
  </script>
</body>
</html>
"""


@docs_bp.get("/openapi.json")
def openapi_json() -> Response:
    """Return the OpenAPI 3.0 specification as JSON."""
    return Response(
        json.dumps(_load_spec(), ensure_ascii=False, indent=2),
        status=200,
        mimetype="application/json",
    )


@docs_bp.get("/docs")
def swagger_ui() -> Response:
    """Serve the Swagger UI HTML page."""
    spec_url = "/api/openapi.json"
    html = _SWAGGER_UI_HTML.format(ver=_SWAGGER_UI_VERSION, spec_url=spec_url)
    return Response(html, status=200, mimetype="text/html; charset=utf-8")


@docs_bp.get("/docs/")
def swagger_ui_slash() -> Response:
    """Handle trailing-slash variant for browsers that append one."""
    return swagger_ui()
