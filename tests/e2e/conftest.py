"""E2E conftest: spin up the Flask app on a free port for the test session.

Two usage modes:
  1. Server already running:
       pytest tests/e2e/ --base-url=http://127.0.0.1:5559
  2. Auto-start (default in CI):
       pytest tests/e2e/
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any

import pytest
import requests

try:
    from werkzeug.serving import make_server
except ImportError:  # pragma: no cover
    make_server = None  # type: ignore[assignment]


def _find_free_port() -> int:
    """Bind to port 0, read the OS-assigned port, release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url(request) -> str:
    """Return the base URL of the Flask app under test.

    Order of resolution:
      1. --base-url CLI option (assume the server is already running)
      2. Otherwise: auto-start a Flask app on a free port in a background thread.
    """
    cli_url = request.config.getoption("--base-url", default=None)
    if cli_url and cli_url != "http://127.0.0.1:5559":
        # User-supplied URL; trust that the server is up
        yield cli_url
        return

    if make_server is None:
        pytest.skip("werkzeug is not available; cannot start a test server")

    port = _find_free_port()
    from web_app import create_app

    app = create_app()
    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, name="flask-test", daemon=True)
    thread.start()
    # Wait for the server to accept connections (max 5s)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        server.shutdown()
        pytest.fail(f"Flask test server failed to start on port {port}")

    url = f"http://127.0.0.1:{port}"
    try:
        yield url
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.fixture(scope="session")
def http_session(base_url: str) -> requests.Session:
    """Reusable HTTP session for E2E tests (no browser)."""
    s = requests.Session()
    s.headers.update({"User-Agent": "jztz-v17-e2e/1.0"})
    return s


@pytest.fixture()
def http(http_session: requests.Session, base_url: str) -> Any:
    """Convenience fixture: HTTP client bound to the test base URL."""

    class _Http:
        def get(self, path: str, **kw):
            return http_session.get(f"{base_url}{path}", timeout=10, **kw)

        def post(self, path: str, **kw):
            return http_session.post(f"{base_url}{path}", timeout=10, **kw)

        def head(self, path: str, **kw):
            return http_session.head(f"{base_url}{path}", timeout=10, **kw)

    return _Http()
