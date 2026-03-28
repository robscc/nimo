"""单元测试 — Worker entry point。"""

from __future__ import annotations

import pytest

from agentpal.scheduler.worker import _create_daemon, _setup_worker_logging


class TestWorkerCreateDaemon:
    """Worker daemon 创建测试。"""

    def test_create_pa_daemon(self):
        daemon = _create_daemon(
            agent_type="pa",
            identity="pa:test-session",
            session_id="test-session",
        )
        from agentpal.zmq_bus.pa_daemon import PersonalAssistantDaemon

        assert isinstance(daemon, PersonalAssistantDaemon)

    def test_create_sub_daemon(self):
        daemon = _create_daemon(
            agent_type="sub_agent",
            identity="sub:coder:task-1",
            agent_name="coder",
            task_id="task-1",
        )
        from agentpal.zmq_bus.sub_daemon import SubAgentDaemon

        assert isinstance(daemon, SubAgentDaemon)

    def test_create_cron_daemon(self):
        daemon = _create_daemon(
            agent_type="cron",
            identity="cron:scheduler",
        )
        from agentpal.zmq_bus.cron_daemon import CronDaemon

        assert isinstance(daemon, CronDaemon)

    def test_unknown_agent_type_raises(self):
        with pytest.raises(ValueError, match="Unknown agent_type"):
            _create_daemon(
                agent_type="unknown",
                identity="unknown:test",
            )


class TestWorkerLogging:
    """Worker 日志设置测试。"""

    def test_setup_logging_no_crash(self):
        """确保日志设置不会崩溃。"""
        _setup_worker_logging("test:logging-setup")
