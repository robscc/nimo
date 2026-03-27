"""单元测试 — AgentScheduler 核心逻辑（mock Process）。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpal.scheduler.config import SchedulerConfig
from agentpal.scheduler.scheduler import AgentScheduler
from agentpal.scheduler.state import AgentProcessInfo, AgentState


@pytest.fixture
def inproc_config():
    """使用 inproc 地址避免文件系统副作用。"""
    return SchedulerConfig(
        router_addr="inproc://test-scheduler-router",
        events_addr="inproc://test-scheduler-events",
        pa_idle_timeout=10,
        sub_idle_timeout=5,
        reaper_interval=2,
        health_check_interval=2,
    )


class TestAgentSchedulerInit:
    """AgentScheduler 初始化测试。"""

    def test_init_with_defaults(self):
        scheduler = AgentScheduler()
        assert scheduler._config is not None
        assert scheduler._running is False

    def test_init_with_custom_config(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        assert scheduler._config.pa_idle_timeout == 10
        assert scheduler._config.sub_idle_timeout == 5


class TestAgentSchedulerStats:
    """AgentScheduler 状态查询测试。"""

    def test_empty_stats(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        scheduler._started_at = 1000.0  # 设置启动时间
        stats = scheduler.get_stats()
        assert stats["total_processes"] == 0
        assert stats["pa_count"] == 0
        assert stats["sub_agent_count"] == 0
        assert stats["cron_count"] == 0
        assert stats["by_state"] == {}

    def test_list_agents_empty(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        agents = scheduler.list_agents()
        assert agents == []

    def test_get_agent_not_found(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        assert scheduler.get_agent("nonexistent") is None


class TestAgentSchedulerLifecycle:
    """AgentScheduler 生命周期测试（使用 inproc）。"""

    @pytest.mark.asyncio
    async def test_start_stop(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        # Patch out CronDaemon to avoid hanging
        with patch.object(scheduler, "_start_cron_daemon", new_callable=AsyncMock):
            await scheduler.start()
            assert scheduler._running is True
            assert scheduler._ctx is not None
            assert scheduler._router is not None
            assert scheduler._xpub is not None

            await scheduler.stop()
            assert scheduler._running is False
            assert scheduler._ctx is None

    @pytest.mark.asyncio
    async def test_stats_after_start(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        with patch.object(scheduler, "_start_cron_daemon", new_callable=AsyncMock):
            await scheduler.start()
            try:
                stats = scheduler.get_stats()
                assert stats["uptime_seconds"] >= 0
                assert stats["total_processes"] >= 0
            finally:
                await scheduler.stop()


class TestAgentSchedulerProcessInfo:
    """AgentScheduler ProcessInfo 管理测试。"""

    def test_ensure_process_info_creates(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        info = scheduler._ensure_process_info(
            process_id="pa:test-1",
            agent_type="pa",
            session_id="test-1",
        )
        assert info.process_id == "pa:test-1"
        assert info.agent_type == "pa"
        assert info.session_id == "test-1"
        assert info.state == AgentState.RUNNING

    def test_ensure_process_info_updates_existing(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        info1 = scheduler._ensure_process_info(
            process_id="pa:test-1",
            agent_type="pa",
        )
        old_active = info1.last_active_at

        import time
        time.sleep(0.01)

        info2 = scheduler._ensure_process_info(
            process_id="pa:test-1",
            agent_type="pa",
        )
        assert info1 is info2
        assert info2.last_active_at >= old_active

    def test_get_agent_after_ensure(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        scheduler._ensure_process_info(
            process_id="pa:test-1",
            agent_type="pa",
        )
        info = scheduler.get_agent("pa:test-1")
        assert info is not None
        assert info.process_id == "pa:test-1"

    def test_list_agents_with_process_info(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        scheduler._ensure_process_info(
            process_id="pa:session-1",
            agent_type="pa",
            session_id="session-1",
        )
        scheduler._ensure_process_info(
            process_id="sub:coder:task-1",
            agent_type="sub_agent",
            task_id="task-1",
            agent_name="coder",
        )
        agents = scheduler.list_agents()
        assert len(agents) == 2
        types = {a.agent_type for a in agents}
        assert types == {"pa", "sub_agent"}

    def test_stats_with_process_info(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        scheduler._started_at = 1000.0
        scheduler._ensure_process_info("pa:s1", "pa")
        scheduler._ensure_process_info("pa:s2", "pa")
        scheduler._ensure_process_info("sub:c:t1", "sub_agent")

        stats = scheduler.get_stats()
        assert stats["total_processes"] == 3
        assert stats["pa_count"] == 2
        assert stats["sub_agent_count"] == 1


class TestAgentSchedulerCompat:
    """AgentScheduler 兼容旧 API 测试。"""

    def test_pa_daemon_count(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        assert scheduler.pa_daemon_count == 0

    def test_sub_daemon_count(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        assert scheduler.sub_daemon_count == 0

    def test_get_pa_daemon_not_found(self, inproc_config):
        scheduler = AgentScheduler(inproc_config)
        assert scheduler.get_pa_daemon("nonexistent") is None
