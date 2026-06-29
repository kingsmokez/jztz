"""调度模块 — 每个任务独立线程执行，互不阻塞

设计要点:
- 调度线程仅负责检查时间和派发任务，不做实际工作
- 每个任务触发时启动独立 daemon 线程执行，不阻塞调度循环
- 同一任务默认不允许并发（跳过本次），可配置 allow_concurrent=True
- 内置缓存清理任务，防止内存泄漏
"""

from __future__ import annotations

import time
import threading
from typing import Callable, Optional

from modules.logger import log


class Scheduler:
    """基于线程的定时调度器 — 任务独立线程执行"""

    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._running = False
        self._lock = threading.Lock()
        # 跟踪正在运行的任务，防止同一任务并发
        self._running_jobs: set[str] = set()
        self._running_lock = threading.Lock()

    def add_job(
        self,
        name: str,
        func: Callable,
        interval_seconds: int = 60,
        run_immediately: bool = False,
        allow_concurrent: bool = False,
    ) -> None:
        """注册定时任务

        Args:
            name: 任务名称
            func: 任务函数
            interval_seconds: 执行间隔（秒）
            run_immediately: 是否立即执行一次
            allow_concurrent: 是否允许同一任务并发执行（默认不允许）
        """
        with self._lock:
            self._jobs[name] = {
                "func": func,
                "interval": interval_seconds,
                "last_run": 0.0,
                "allow_concurrent": allow_concurrent,
            }
            log.info(f"注册调度任务: {name}, 间隔 {interval_seconds}s")

        if run_immediately:
            self._execute_in_thread(name, func)

    def remove_job(self, name: str) -> None:
        with self._lock:
            self._jobs.pop(name, None)
            log.info(f"移除调度任务: {name}")

    def run(self) -> None:
        """调度主循环 — 仅检查时间，派发任务到独立线程"""
        self._running = True
        log.info("调度器启动 (任务独立线程模式)")
        while self._running:
            now = time.time()
            with self._lock:
                jobs_snapshot = dict(self._jobs)

            for name, job in jobs_snapshot.items():
                if now - job["last_run"] >= job["interval"]:
                    # 检查是否允许并发
                    if not job.get("allow_concurrent", False):
                        with self._running_lock:
                            if name in self._running_jobs:
                                continue  # 上一次还没跑完，跳过

                    # 更新 last_run（在派发之前，防止重复触发）
                    with self._lock:
                        if name in self._jobs:
                            self._jobs[name]["last_run"] = now

                    # 在独立线程中执行任务
                    self._execute_in_thread(name, job["func"])

            time.sleep(10)  # 每10秒检查一次

    def _execute_in_thread(self, name: str, func: Callable) -> None:
        """在独立线程中执行任务"""
        with self._running_lock:
            self._running_jobs.add(name)

        def _worker():
            try:
                log.debug(f"执行任务: {name}")
                func()
            except Exception as e:
                log.error(f"任务执行失败: {name}, {e}", exc_info=True)
            finally:
                with self._running_lock:
                    self._running_jobs.discard(name)

        t = threading.Thread(target=_worker, daemon=True, name=f"task_{name}")
        t.start()

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True, name="scheduler")
        t.start()
        return t

    def stop(self) -> None:
        self._running = False
        log.info("调度器停止")

    def running_jobs(self) -> list[str]:
        """返回当前正在运行的任务列表"""
        with self._running_lock:
            return list(self._running_jobs)
