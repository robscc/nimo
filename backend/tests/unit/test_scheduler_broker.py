"""单元测试 — SchedulerBroker 核心逻辑（mock Process）。"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from agentpal.scheduler.broker import ManagedProcess, SchedulerBroker
from agentpal.scheduler.config import SchedulerConfig
from agentpal.scheduler.state import AgentProcessInfo, AgentState
from agentpal.zmq_bus.protocol import Envelope, MessageType


@pytest.fixture
def mock_sockets():
    """创建 mock ZMQ sockets。"""
    router = AsyncMock()
    xpub = AsyncMock()
    xsub = AsyncMock()
    return router, xpub, xsub


@pytest.fixture
def broker_config():
    """创建测试用 SchedulerConfig。"""
    return SchedulerConfig(
        router_addr="ipc:///tmp/test-broker-router.sock",
        events_addr="ipc:///tmp/test-broker-events.sock",
        pa_idle_timeout=10,
        sub_idle_timeout=5,
        reaper_interval=2,
        health_check_interval=2,
        process_start_timeout=5,
    )


@pytest.fixture
def broker(broker_config, mock_sockets):
    """创建 SchedulerBroker 实例（不启动后台循环）。"""
    router, xpub, xsub = mock_sockets
    return SchedulerBroker(
        config=broker_config,
        router_socket=router,
        xpub_socket=xpub,
        xsub_socket=xsub,
        xsub_addr="ipc:///tmp/test-broker-events-internal.sock",
    )


class TestSchedulerBrokerInit:
    """SchedulerBroker 初始化测试。"""

    def test_init_with_sockets(self, broker):
        assert broker._running is False
        assert broker._processes == {}
        assert broker._register_events == {}

    def test_config_stored(self, broker, broker_config):
        assert broker._config is broker_config
        assert broker._config.pa_idle_timeout == 10


class TestSchedulerBrokerStats:
    """SchedulerBroker 状态查询测试。"""

    def test_empty_stats(self, broker):
        broker._started_at = 1000.0
        stats = broker.get_stats()
        assert stats["total_processes"] == 0
        assert stats["pa_count"] == 0
        assert stats["sub_agent_count"] == 0
        assert stats["cron_count"] == 0

    def test_list_agents_empty(self, broker):
        agents = broker.list_agents()
        assert agents == []

    def test_get_agent_not_found(self, broker):
        assert broker.get_agent("nonexistent") is None


class TestSchedulerBrokerProcessInfo:
    """SchedulerBroker 进程管理测试。"""

    def test_managed_process(self):
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True
        mock_proc.pid = 12345

        info = AgentProcessInfo(
            process_id="pa:test-1",
            agent_type="pa",
            state=AgentState.RUNNING,
        )
        managed = ManagedProcess(process=mock_proc, info=info)

        assert managed.is_alive is True
        assert managed.pid == 12345

    def test_list_agents_with_processes(self, broker):
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True

        info1 = AgentProcessInfo(
            process_id="pa:session-1",
            agent_type="pa",
            state=AgentState.RUNNING,
        )
        info2 = AgentProcessInfo(
            process_id="sub:coder:task-1",
            agent_type="sub_agent",
            state=AgentState.IDLE,
        )

        broker._processes["pa:session-1"] = ManagedProcess(process=mock_proc, info=info1)
        broker._processes["sub:coder:task-1"] = ManagedProcess(process=mock_proc, info=info2)

        agents = broker.list_agents()
        assert len(agents) == 2
        types = {a.agent_type for a in agents}
        assert types == {"pa", "sub_agent"}

    def test_stats_with_processes(self, broker):
        broker._started_at = 1000.0
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True

        for pid, atype in [("pa:s1", "pa"), ("pa:s2", "pa"), ("sub:c:t1", "sub_agent")]:
            info = AgentProcessInfo(
                process_id=pid,
                agent_type=atype,
                state=AgentState.RUNNING,
            )
            broker._processes[pid] = ManagedProcess(process=mock_proc, info=info)

        stats = broker.get_stats()
        assert stats["total_processes"] == 3
        assert stats["pa_count"] == 2
        assert stats["sub_agent_count"] == 1


class TestSchedulerBrokerConfigReload:
    """SchedulerBroker 配置重载测试。"""

    @pytest.mark.asyncio
    async def test_broadcast_config_reload_empty(self, broker):
        """没有子进程时广播不出错。"""
        await broker.broadcast_config_reload()

    @pytest.mark.asyncio
    async def test_broadcast_config_reload_sends_to_all(self, broker):
        """有子进程时向所有子进程发送 CONFIG_RELOAD。"""
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True

        for pid in ["pa:s1", "cron:scheduler"]:
            info = AgentProcessInfo(process_id=pid, agent_type="pa", state=AgentState.RUNNING)
            broker._processes[pid] = ManagedProcess(process=mock_proc, info=info)

        await broker.broadcast_config_reload()

        # 应该发送了 2 次消息
        assert broker._router.send_multipart.call_count == 2


class TestSchedulerBrokerStopAgent:
    """SchedulerBroker 停止 Agent 测试。"""

    @pytest.mark.asyncio
    async def test_stop_nonexistent_agent(self, broker):
        result = await broker.stop_agent("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_stop_existing_agent(self, broker):
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = False  # 模拟进程已退出

        info = AgentProcessInfo(
            process_id="pa:test-1",
            agent_type="pa",
            state=AgentState.RUNNING,
        )
        broker._processes["pa:test-1"] = ManagedProcess(process=mock_proc, info=info)

        result = await broker.stop_agent("pa:test-1")
        assert result is True
