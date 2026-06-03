"""Playwright config for E2E tests.

Run:
  pip install playwright pytest-playwright
  playwright install chromium
  pytest tests/e2e/ --base-url=http://127.0.0.1:5559
"""

BASE_URL = "http://127.0.0.1:5559"
TIMEOUT_MS = 30_000
RETRIES = 1
HEADLESS = True
VIEWPORT = {"width": 1280, "height": 720}


def pytest_configure(config):
    """Register E2E markers."""
    config.addinivalue_line("markers", "e2e: end-to-end tests (slow, needs running server)")


def pytest_addoption(parser):
    parser.addoption(
        "--base-url",
        action="store",
        default=BASE_URL,
        help="Base URL of the running Flask app for E2E tests",
    )
    parser.addoption(
        "--e2e-timeout",
        action="store",
        default=TIMEOUT_MS,
        type=int,
        help="Per-test timeout in milliseconds",
    )
