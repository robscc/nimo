"""单元测试 — Scheduler 进程入口冒烟测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from agentpal.scheduler.process import _setup_scheduler_logging


class TestSchedulerProcessLogging:
    """Scheduler 进程日志设置测试。"""

    def test_setup_logging_no_crash(self):
        """确保日志设置不会崩溃。"""
        _setup_scheduler_logging()


class TestSchedulerProcessEntry:
    """Scheduler 进程入口测试。"""

    def test_scheduler_process_main_importable(self):
        """确保 scheduler_process_main 可以被 import。"""
        from agentpal.scheduler.process import scheduler_process_main

        assert callable(scheduler_process_main)

    def test_scheduler_async_main_importable(self):
        """确保 _scheduler_async_main 可以被 import。"""
        from agentpal.scheduler.process import _scheduler_async_main

        assert callable(_scheduler_async_main)
