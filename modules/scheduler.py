"""调度模块 - 使用APScheduler替代time.sleep轮询"""

from __future__ import annotations

import time
import threading
from typing import Callable, Optional

from modules.logger import log


class Scheduler:
    """基于线程的定时调度器"""

    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._running = False
        self._lock = threading.Lock()

    def add_job(
        self,
        name: str,
        func: Callable,
        interval_seconds: int = 60,
        run_immediately: bool = False,
    ) -> None:
        with self._lock:
            self._jobs[name] = {
                "func": func,
                "interval": interval_seconds,
                "last_run": 0.0,
            }
            log.info(f"注册调度任务: {name}, 间隔 {interval_seconds}s")

        if run_immediately:
            try:
                func()
            except Exception as e:
                log.error(f"立即执行任务失败: {name}, {e}")

    def remove_job(self, name: str) -> None:
        with self._lock:
            self._jobs.pop(name, None)
            log.info(f"移除调度任务: {name}")

    def run(self) -> None:
        self._running = True
        log.info("调度器启动")
        while self._running:
            now = time.time()
            with self._lock:
                jobs_snapshot = dict(self._jobs)

            for name, job in jobs_snapshot.items():
                if now - job["last_run"] >= job["interval"]:
                    try:
                        log.debug(f"执行任务: {name}")
                        job["func"]()
                        with self._lock:
                            if name in self._jobs:
                                self._jobs[name]["last_run"] = now
                    except Exception as e:
                        log.error(f"任务执行失败: {name}, {e}")

            time.sleep(10)  # 每10秒检查一次，替代60秒轮询

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True, name="scheduler")
        t.start()
        return t

    def stop(self) -> None:
        self._running = False
        log.info("调度器停止")
