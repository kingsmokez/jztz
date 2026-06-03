"""Tests for modules.scheduler (background job scheduler)."""

from __future__ import annotations

import time

from modules.scheduler import Scheduler


def test_scheduler_init():
    s = Scheduler()
    assert s._jobs == {}
    assert s._running is False


def test_add_job_basic():
    s = Scheduler()
    calls = []

    def job():
        calls.append(time.time())

    s.add_job("test", job, interval_seconds=60)
    assert "test" in s._jobs
    assert s._jobs["test"]["interval"] == 60
    assert s._jobs["test"]["last_run"] == 0.0


def test_add_job_overwrites_existing():
    s = Scheduler()

    def job_a():
        pass

    def job_b():
        pass

    s.add_job("dup", job_a, interval_seconds=10)
    s.add_job("dup", job_b, interval_seconds=20)
    # Last write wins
    assert s._jobs["dup"]["func"] is job_b
    assert s._jobs["dup"]["interval"] == 20


def test_add_job_run_immediately_true():
    s = Scheduler()
    calls = []

    def job():
        calls.append(1)

    s.add_job("immediate", job, interval_seconds=60, run_immediately=True)
    assert calls == [1]


def test_add_job_run_immediately_exception_is_logged():
    s = Scheduler()

    def job():
        raise RuntimeError("boom")

    # Should not propagate
    s.add_job("bad", job, interval_seconds=60, run_immediately=True)


def test_remove_job():
    s = Scheduler()
    s.add_job("x", lambda: None, interval_seconds=10)
    assert "x" in s._jobs
    s.remove_job("x")
    assert "x" not in s._jobs


def test_remove_job_missing_no_error():
    s = Scheduler()
    s.remove_job("never_added")  # should not raise


def test_stop_sets_running_false():
    s = Scheduler()
    s._running = True
    s.stop()
    assert s._running is False


def test_run_executes_due_job():
    s = Scheduler()
    calls = []

    def job():
        calls.append(1)

    s.add_job("due", job, interval_seconds=0)
    # Manually push last_run into the past
    s._jobs["due"]["last_run"] = 0.0
    # Bypass the time.sleep loop: run one tick manually
    now = time.time()
    with s._lock:
        snapshot = dict(s._jobs)
    for name, jobinfo in snapshot.items():
        if now - jobinfo["last_run"] >= jobinfo["interval"]:
            jobinfo["func"]()
            if name in s._jobs:
                s._jobs[name]["last_run"] = now
    assert calls == [1]


def test_run_does_not_re_execute_recent_job():
    s = Scheduler()
    calls = []

    def job():
        calls.append(1)

    s.add_job("fresh", job, interval_seconds=60)
    s._jobs["fresh"]["last_run"] = time.time()  # just ran
    now = time.time()
    with s._lock:
        snapshot = dict(s._jobs)
    for name, jobinfo in snapshot.items():
        if now - jobinfo["last_run"] >= jobinfo["interval"]:
            jobinfo["func"]()
    assert calls == []


def test_run_swallows_job_exception():
    s = Scheduler()

    def bad():
        raise ValueError("nope")

    s.add_job("bad", bad, interval_seconds=0)
    s._jobs["bad"]["last_run"] = 0.0
    now = time.time()
    with s._lock:
        snapshot = dict(s._jobs)
    for name, jobinfo in snapshot.items():
        if now - jobinfo["last_run"] >= jobinfo["interval"]:
            try:
                jobinfo["func"]()
            except Exception:
                pass


def test_start_background_returns_thread():
    s = Scheduler()
    t = s.start_background()
    assert t.is_alive()
    assert t.daemon is True
    assert t.name == "scheduler"
    s.stop()
    t.join(timeout=2)
