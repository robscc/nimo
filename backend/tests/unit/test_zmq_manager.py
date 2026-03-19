"""AgentDaemonManager lifecycle unit tests.

The real PA/Sub/Cron daemon implementations require LLM + DB, so we mock
them out and focus on the manager's own socket management and daemon registry.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import zmq
import zmq.asyncio

from agentpal.zmq_bus.event_subscriber import EventSubscriber
from agentpal.zmq_bus.manager import AgentDaemonManager


# Patch target — `ensure_pa_daemon` uses a local import:
#   from agentpal.zmq_bus.pa_daemon import PersonalAssistantDaemon
PA_DAEMON_CLS = "agentpal.zmq_bus.pa_daemon.PersonalAssistantDaemon"


# ── Helpers / Fakes ──────────────────────────────────────

class FakePADaemon:
    """Lightweight stand-in for PersonalAssistantDaemon."""

    def __init__(self, session_id: str) -> None:
        self.identity = f"pa:{session_id}"
        self.session_id = session_id
        self.is_running = True
        self.last_active_at = 0.0

    async def start(self, ctx, router_addr, events_addr):
        self.is_running = True

    async def stop(self):
        self.is_running = False


class FakeCronDaemon:
    """Lightweight stand-in for CronDaemon."""

    def __init__(self):
        self.is_running = True

    async def start(self, ctx, router_addr, events_addr):
        self.is_running = True

    async def stop(self):
        self.is_running = False


# ── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def unique_addrs():
    """Generate unique inproc addresses per test."""
    import uuid

    uid = uuid.uuid4().hex[:8]
    return {
        "router": f"inproc://mgr-router-{uid}",
        "events": f"inproc://mgr-events-{uid}",
    }


@pytest.fixture
async def manager(unique_addrs):
    """Create, start, and yield an AgentDaemonManager; stop after test.

    PA/Sub/Cron daemon creation is mocked so no LLM or DB is needed.
    """
    mgr = AgentDaemonManager(
        router_addr=unique_addrs["router"],
        events_addr=unique_addrs["events"],
    )

    # Patch _start_cron_daemon to avoid importing heavy CronDaemon deps
    with patch.object(mgr, "_start_cron_daemon", new_callable=AsyncMock):
        await mgr.start()
        yield mgr
        if mgr._running:
            await mgr.stop()


# ── Tests ────────────────────────────────────────────────


class TestAgentDaemonManager:
    """Manager start/stop and daemon registry."""

    @pytest.mark.asyncio
    async def test_manager_start_stop(self, manager):
        """Manager should be running after start, clean after stop."""
        assert manager._running is True
        assert manager._ctx is not None
        assert manager._router is not None

        await manager.stop()

        assert manager._running is False
        assert manager._ctx is None
        assert manager._router is None

    @pytest.mark.asyncio
    async def test_ensure_pa_daemon_creates_daemon(self, manager):
        """ensure_pa_daemon should create a new PA daemon for an unknown session."""
        with patch(PA_DAEMON_CLS, side_effect=lambda sid: FakePADaemon(sid)):
            daemon = await manager.ensure_pa_daemon("sess-001")

        assert daemon is not None
        assert daemon.identity == "pa:sess-001"
        assert manager.pa_daemon_count == 1

    @pytest.mark.asyncio
    async def test_ensure_pa_daemon_reuses_existing(self, manager):
        """Calling ensure_pa_daemon twice with the same session_id returns the same daemon."""
        with patch(PA_DAEMON_CLS, side_effect=lambda sid: FakePADaemon(sid)):
            d1 = await manager.ensure_pa_daemon("sess-002")
            d2 = await manager.ensure_pa_daemon("sess-002")

        assert d1 is d2
        assert manager.pa_daemon_count == 1

    @pytest.mark.asyncio
    async def test_create_event_subscriber(self, manager):
        """create_event_subscriber should return an EventSubscriber instance."""
        sub = manager.create_event_subscriber(
            topic="session:abc123",
            filter_msg_id="msg-xyz",
        )

        assert isinstance(sub, EventSubscriber)

    @pytest.mark.asyncio
    async def test_manager_pa_daemon_count(self, manager):
        """Creating multiple PA daemons should be reflected in pa_daemon_count."""
        with patch(PA_DAEMON_CLS, side_effect=lambda sid: FakePADaemon(sid)):
            await manager.ensure_pa_daemon("s1")
            await manager.ensure_pa_daemon("s2")
            await manager.ensure_pa_daemon("s3")

        assert manager.pa_daemon_count == 3

    @pytest.mark.asyncio
    async def test_manager_stop_cleans_daemons(self, manager):
        """stop() should clear all daemon registries."""
        with patch(PA_DAEMON_CLS, side_effect=lambda sid: FakePADaemon(sid)):
            await manager.ensure_pa_daemon("s-a")
            await manager.ensure_pa_daemon("s-b")

        assert manager.pa_daemon_count == 2

        await manager.stop()

        assert manager.pa_daemon_count == 0
        assert manager.sub_daemon_count == 0

    @pytest.mark.asyncio
    async def test_manager_get_pa_daemon(self, manager):
        """get_pa_daemon returns the daemon or None."""
        assert manager.get_pa_daemon("nonexistent") is None

        with patch(PA_DAEMON_CLS, side_effect=lambda sid: FakePADaemon(sid)):
            await manager.ensure_pa_daemon("sess-get")

        d = manager.get_pa_daemon("sess-get")
        assert d is not None
        assert d.identity == "pa:sess-get"

    @pytest.mark.asyncio
    async def test_manager_zmq_context_property(self, manager):
        """zmq_context property should return the live context."""
        ctx = manager.zmq_context
        assert ctx is not None
        assert isinstance(ctx, zmq.asyncio.Context)
