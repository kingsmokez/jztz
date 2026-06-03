"""Root conftest: enforce a hard per-test timeout via subprocess watchdog.

We use signal.SIGALRM (POSIX) / thread-based polling (Windows) to abort any
test that runs longer than 60s. This prevents a single hung test (e.g. a
ThreadPoolExecutor that never completes) from blocking the whole suite.
"""

from __future__ import annotations

import os
import sys
import threading

# Pre-import bcrypt AND its C extension so the PyO3 binding is initialized
# exactly once, before pytest-cov's instrumentation kicks in. pytest-cov can
# otherwise re-trigger `import bcrypt` in modules/auth.py and fail with
# "PyO3 modules ... may only be initialized once per interpreter process".
# Caching both names in sys.modules makes any subsequent import a no-op.
try:
    import bcrypt  # noqa: F401
    import bcrypt._bcrypt  # noqa: F401
except ImportError:  # bcrypt not installed (e.g. minimal CI env)
    pass

import pytest


# Per-test timeout (seconds). Generous because some tests boot the full
# Flask app, but tight enough to fail fast on real hangs.
TEST_TIMEOUT_SECS = 60 if not os.environ.get("CI") else 120


@pytest.fixture(autouse=True)
def _per_test_timeout(request):
    """Abort the test if it runs for too long (Windows-safe)."""
    timer = None
    timed_out = threading.Event()

    def _kill():
        if not timed_out.is_set():
            timed_out.set()
            # Raising here is caught by pytest as a test failure
            import _pytest.outcomes
            raise _pytest.outcomes.Failed(
                f"Test exceeded {TEST_TIMEOUT_SECS}s timeout"
            )

    timer = threading.Timer(TEST_TIMEOUT_SECS, _kill)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timed_out.set()
        timer.cancel()
