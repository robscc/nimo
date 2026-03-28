"""单元测试 — SchedulerClient（mock Process + ZMQ）。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpal.scheduler.client import SchedulerClient
from agentpal.scheduler.config import SchedulerConfig
from agentpal.scheduler.state import AgentProcessInfo, AgentState


@pytest.fixture
def client_config():
    """创建测试用 SchedulerConfig。"""
    return SchedulerConfig(
        router_addr="ipc:///tmp/test-client-router.sock",
        events_addr="ipc:///tmp/test-client-events.sock",
        scheduler_start_timeout=5,
    )


class TestSchedulerClientInit:
    """SchedulerClient 初始化测试。"""

    def test_init_with_defaults(self):
        client = SchedulerClient()
        assert client._running is False
        assert client._config is not None

    def test_init_with_custom_config(self, client_config):
        client = SchedulerClient(client_config)
        assert client._config.router_addr == "ipc:///tmp/test-client-router.sock"


class TestSchedulerClientCompat:
    """SchedulerClient 兼容旧 API 测试。"""

    def test_pa_daemon_count_empty(self, client_config):
        client = SchedulerClient(client_config)
        assert client.pa_daemon_count == 0

    def test_sub_daemon_count_empty(self, client_config):
        client = SchedulerClient(client_config)
        assert client.sub_daemon_count == 0

    def test_get_pa_daemon_returns_none(self, client_config):
        client = SchedulerClient(client_config)
        assert client.get_pa_daemon("nonexistent") is None

    def test_zmq_context_none_before_start(self, client_config):
        client = SchedulerClient(client_config)
        assert client.zmq_context is None


class TestSchedulerClientStats:
    """SchedulerClient 状态查询测试（使用缓存）。"""

    def test_empty_stats(self, client_config):
        client = SchedulerClient(client_config)
        stats = client.get_stats()
        assert stats["total_processes"] == 0
        assert stats["pa_count"] == 0
        assert stats["sub_agent_count"] == 0

    def test_list_agents_empty(self, client_config):
        client = SchedulerClient(client_config)
        agents = client.list_agents()
        assert agents == []

    def test_get_agent_not_found(self, client_config):
        client = SchedulerClient(client_config)
        assert client.get_agent("nonexistent") is None

    def test_list_agents_from_cache(self, client_config):
        client = SchedulerClient(client_config)
        client._cached_agents = [
            {
                "process_id": "pa:session-1",
                "agent_type": "pa",
                "state": "running",
                "session_id": "session-1",
                "task_id": None,
                "agent_name": None,
                "os_pid": 1234,
            },
            {
                "process_id": "cron:scheduler",
                "agent_type": "cron",
                "state": "idle",
                "session_id": None,
                "task_id": None,
                "agent_name": None,
                "os_pid": 5678,
            },
        ]

        agents = client.list_agents()
        assert len(agents) == 2
        assert agents[0].process_id == "pa:session-1"
        assert agents[0].agent_type == "pa"
        assert agents[0].state == AgentState.RUNNING
        assert agents[1].process_id == "cron:scheduler"
        assert agents[1].agent_type == "cron"

    def test_get_agent_from_cache(self, client_config):
        client = SchedulerClient(client_config)
        client._cached_agents = [
            {
                "process_id": "pa:session-1",
                "agent_type": "pa",
                "state": "running",
                "session_id": "session-1",
                "task_id": None,
                "agent_name": None,
                "os_pid": 1234,
            },
        ]

        info = client.get_agent("pa:session-1")
        assert info is not None
        assert info.process_id == "pa:session-1"

    def test_get_stats_from_cache(self, client_config):
        client = SchedulerClient(client_config)
        client._cached_stats = {
            "total_processes": 3,
            "pa_count": 1,
            "sub_agent_count": 1,
            "cron_count": 1,
            "by_state": {"running": 2, "idle": 1},
            "total_memory_mb": 0.0,
            "uptime_seconds": 42.0,
        }

        stats = client.get_stats()
        assert stats["total_processes"] == 3
        assert stats["pa_count"] == 1

    def test_pa_daemon_count_from_cache(self, client_config):
        client = SchedulerClient(client_config)
        client._cached_agents = [
            {"agent_type": "pa", "process_id": "pa:s1", "state": "running"},
            {"agent_type": "pa", "process_id": "pa:s2", "state": "running"},
            {"agent_type": "cron", "process_id": "cron:scheduler", "state": "idle"},
        ]
        assert client.pa_daemon_count == 2

    def test_sub_daemon_count_from_cache(self, client_config):
        client = SchedulerClient(client_config)
        client._cached_agents = [
            {"agent_type": "sub_agent", "process_id": "sub:c:t1", "state": "running"},
        ]
        assert client.sub_daemon_count == 1


class TestSchedulerClientBroadcast:
    """SchedulerClient 广播测试。"""

    @pytest.mark.asyncio
    async def test_broadcast_without_dealer(self, client_config):
        """DEALER 未就绪时广播不出错。"""
        client = SchedulerClient(client_config)
        # 不调用 start()，_dealer 是 None
        await client.broadcast_config_reload()
        # 不应抛异常
