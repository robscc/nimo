"""Integration tests for inter-agent communication via ZMQ.

Tests the hybrid MessageBus (DB audit + ZMQ real-time delivery)
and CronDaemon notification through ZMQ.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.agents.message_bus import MessageBus
from agentpal.database import Base
from agentpal.models.message import AgentMessage, MessageStatus, MessageType
from agentpal.zmq_bus.manager import AgentDaemonManager
from agentpal.zmq_bus.protocol import Envelope
from agentpal.zmq_bus.protocol import MessageType as ZmqMessageType


# ── Helpers ────────────────────────────────────────────────


def _unique_addrs() -> tuple[str, str]:
    """Generate unique inproc addresses to avoid collisions between tests."""
    tag = uuid.uuid4().hex[:8]
    return (
        f"inproc://test-router-{tag}",
        f"inproc://test-events-{tag}",
    )


# ── Fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_engine():
    """Create an in-memory SQLite engine with all tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    """Provide an async DB session for each test."""
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def zmq_manager():
    """Create and start an AgentDaemonManager with unique inproc addresses."""
    router_addr, events_addr = _unique_addrs()
    manager = AgentDaemonManager(
        router_addr=router_addr,
        events_addr=events_addr,
        pa_idle_timeout=300,
        sub_idle_timeout=60,
    )

    with patch.object(manager, "_start_cron_daemon", new_callable=AsyncMock):
        await manager.start()

    yield manager

    await manager.stop()


# ── Tests: MessageBus Hybrid Mode ──────────────────────────


class TestMessageBusHybridMode:
    """Test MessageBus with both DB audit and ZMQ real-time delivery."""

    @pytest.mark.asyncio
    async def test_message_bus_writes_to_db(self, db_session: AsyncSession):
        """MessageBus.send() writes message to DB even without ZMQ."""
        bus = MessageBus(db=db_session, zmq_manager=None)

        parent_session_id = f"session-{uuid.uuid4().hex[:8]}"

        msg = await bus.send(
            from_agent="coder",
            to_agent="main",
            parent_session_id=parent_session_id,
            content="Task completed: wrote hello.py",
            message_type=MessageType.NOTIFY,
        )

        assert msg is not None
        assert msg.id is not None
        assert msg.from_agent == "coder"
        assert msg.to_agent == "main"
        assert msg.status == MessageStatus.PENDING

        # Verify it's retrievable
        pending = await bus.receive_pending("main", parent_session_id)
        assert len(pending) == 1
        assert pending[0]["content"] == "Task completed: wrote hello.py"

    @pytest.mark.asyncio
    async def test_message_bus_hybrid_mode(
        self, db_session: AsyncSession, zmq_manager: AgentDaemonManager
    ):
        """MessageBus with zmq_manager: both DB write AND ZMQ delivery happen.

        Verifies that:
        1. Message is written to DB (audit trail)
        2. ZMQ send_to_agent is called for real-time delivery
        """
        parent_session_id = f"session-{uuid.uuid4().hex[:8]}"

        # Track ZMQ calls by spying on send_to_agent
        original_send = zmq_manager.send_to_agent
        zmq_calls: list[tuple[str, Envelope]] = []

        async def spy_send(target_identity: str, envelope: Envelope):
            zmq_calls.append((target_identity, envelope))
            await original_send(target_identity, envelope)

        zmq_manager.send_to_agent = spy_send

        bus = MessageBus(db=db_session, zmq_manager=zmq_manager)

        msg = await bus.send(
            from_agent="sub:coder:task-123",
            to_agent="main",
            parent_session_id=parent_session_id,
            content="Code review completed",
            message_type=MessageType.NOTIFY,
            metadata={"files": ["hello.py"]},
        )

        # 1. Verify DB write
        assert msg is not None
        assert msg.from_agent == "sub:coder:task-123"

        pending = await bus.receive_pending("main", parent_session_id)
        assert len(pending) == 1
        assert pending[0]["content"] == "Code review completed"

        # 2. Verify ZMQ delivery was attempted
        assert len(zmq_calls) == 1, f"Expected 1 ZMQ call, got {len(zmq_calls)}"
        target, envelope = zmq_calls[0]
        assert target == f"pa:{parent_session_id}", (
            f"Expected target pa:{parent_session_id}, got {target}"
        )
        assert envelope.msg_type == ZmqMessageType.AGENT_NOTIFY
        assert envelope.payload["content"] == "Code review completed"
        assert envelope.payload["from_agent"] == "sub:coder:task-123"

    @pytest.mark.asyncio
    async def test_message_bus_zmq_failure_falls_back_to_db(
        self, db_session: AsyncSession,
    ):
        """If ZMQ delivery fails, message is still saved in DB."""
        parent_session_id = f"session-{uuid.uuid4().hex[:8]}"

        # Create a mock manager that raises on send_to_agent
        mock_zmq = MagicMock()
        mock_zmq.send_to_agent = AsyncMock(
            side_effect=Exception("ZMQ connection error")
        )

        bus = MessageBus(db=db_session, zmq_manager=mock_zmq)

        # This should NOT raise despite ZMQ failure
        msg = await bus.send(
            from_agent="coder",
            to_agent="main",
            parent_session_id=parent_session_id,
            content="Important update",
        )

        assert msg is not None
        assert msg.status == MessageStatus.PENDING

        # Message should still be in DB
        pending = await bus.receive_pending("main", parent_session_id)
        assert len(pending) == 1
        assert pending[0]["content"] == "Important update"

    @pytest.mark.asyncio
    async def test_message_bus_target_resolution(self, db_session: AsyncSession):
        """Test MessageBus._resolve_target_identity for different agent names."""
        session_id = "test-session-123"

        # main → PA daemon
        assert MessageBus._resolve_target_identity("main", session_id) == f"pa:{session_id}"

        # Already-qualified identities pass through
        assert MessageBus._resolve_target_identity("pa:other", session_id) == "pa:other"
        assert MessageBus._resolve_target_identity("sub:coder:task-1", session_id) == "sub:coder:task-1"
        assert MessageBus._resolve_target_identity("cron:scheduler", session_id) == "cron:scheduler"

        # Unknown agent name falls back to PA
        assert MessageBus._resolve_target_identity("coder", session_id) == f"pa:{session_id}"


class TestMessageBusConversation:
    """Test conversation history retrieval through MessageBus."""

    @pytest.mark.asyncio
    async def test_get_conversation_between_agents(self, db_session: AsyncSession):
        """Verify conversation history between two agents is correctly retrieved."""
        bus = MessageBus(db=db_session)
        parent_session_id = f"session-{uuid.uuid4().hex[:8]}"

        # Agent A sends to Agent B
        await bus.send(
            from_agent="main",
            to_agent="coder",
            parent_session_id=parent_session_id,
            content="Please write tests",
            message_type=MessageType.REQUEST,
        )
        await db_session.flush()

        # Agent B responds
        await bus.send(
            from_agent="coder",
            to_agent="main",
            parent_session_id=parent_session_id,
            content="Tests written: 5 passed",
            message_type=MessageType.RESPONSE,
        )
        await db_session.flush()

        # Retrieve conversation
        convo = await bus.get_conversation(
            parent_session_id=parent_session_id,
            agent_a="main",
            agent_b="coder",
        )

        assert len(convo) == 2
        assert convo[0]["from_agent"] == "main"
        assert convo[1]["from_agent"] == "coder"


# ── Tests: CronDaemon Notify via ZMQ ──────────────────────


class TestCronDaemonNotifyViaZmq:
    """Test CronDaemon sends AGENT_NOTIFY through ZMQ."""

    @pytest.mark.asyncio
    async def test_cron_daemon_notify_via_zmq(self, zmq_manager: AgentDaemonManager):
        """Verify CronDaemon sends AGENT_NOTIFY through ZMQ when notifying PA.

        We manually instantiate a CronDaemon, call _notify_via_zmq,
        and verify the PA daemon receives the notification.
        """
        from agentpal.zmq_bus.cron_daemon import CronDaemon

        target_session_id = f"session-{uuid.uuid4().hex[:8]}"

        # Track messages routed through ROUTER
        routed_messages: list[Envelope] = []

        original_send = zmq_manager._send_to_daemon

        async def capture_send(target_identity: str, envelope: Envelope):
            routed_messages.append(envelope)
            await original_send(target_identity, envelope)

        zmq_manager._send_to_daemon = capture_send

        # Create a CronDaemon and start it
        cron = CronDaemon()
        # Patch out scheduler loop and heartbeat to avoid DB deps
        with patch.object(cron, "_scheduler_loop", new_callable=AsyncMock), \
             patch.object(cron, "_ensure_heartbeat_job", new_callable=AsyncMock):
            await cron.start(
                zmq_manager._ctx,
                zmq_manager._router_addr,
                zmq_manager._xsub_addr,
            )

        try:
            # Create a mock DB session for the notification
            mock_db = AsyncMock()
            mock_db.add = MagicMock()
            mock_db.flush = AsyncMock()

            # Mock MemoryRecord import inside _notify_via_zmq
            with patch("agentpal.zmq_bus.cron_daemon.uuid") as mock_uuid:
                mock_uuid.uuid4.return_value.hex = "abcd1234"
                mock_uuid.uuid4.return_value.__str__ = lambda self: "mock-uuid"

                await cron._notify_via_zmq(
                    job_name="Daily Report",
                    result="Report generated: 10 items processed",
                    agent_name="reporter",
                    target_session_id=target_session_id,
                    db=mock_db,
                )

            # Give time for message routing
            await asyncio.sleep(0.3)

            # Verify: should have sent AGENT_NOTIFY through DEALER→ROUTER
            notify_msgs = [
                m for m in routed_messages
                if m.msg_type == ZmqMessageType.AGENT_NOTIFY
            ]
            assert len(notify_msgs) >= 1, (
                f"Expected at least 1 AGENT_NOTIFY, got {len(notify_msgs)}. "
                f"All messages: {[(m.msg_type, m.target) for m in routed_messages]}"
            )

            notify = notify_msgs[0]
            assert notify.target == f"pa:{target_session_id}"
            assert notify.payload["job_name"] == "Daily Report"
            assert "Report generated" in notify.payload["result"]
            assert notify.payload["agent_name"] == "reporter"
            assert notify.payload["type"] == "cron_result"

        finally:
            await cron.stop()

    @pytest.mark.asyncio
    async def test_cron_daemon_publishes_session_event(
        self, zmq_manager: AgentDaemonManager
    ):
        """CronDaemon also publishes STREAM_EVENT via PUB for SSE push."""
        from agentpal.zmq_bus.cron_daemon import CronDaemon

        target_session_id = f"session-{uuid.uuid4().hex[:8]}"
        topic = f"session:{target_session_id}"

        cron = CronDaemon()
        with patch.object(cron, "_scheduler_loop", new_callable=AsyncMock), \
             patch.object(cron, "_ensure_heartbeat_job", new_callable=AsyncMock):
            await cron.start(
                zmq_manager._ctx,
                zmq_manager._router_addr,
                zmq_manager._xsub_addr,
            )

        try:
            # Subscribe to session events BEFORE publishing.
            # Must ensure the SUB socket is created and the subscription
            # has propagated through the XPUB→XSUB proxy chain.
            subscriber = zmq_manager.create_event_subscriber(topic=topic)
            await subscriber._ensure_socket()
            # The proxy polls with 1s timeout, so we need enough time
            # for subscription message to go SUB→XPUB→XSUB and back.
            await asyncio.sleep(0.2)

            mock_db = AsyncMock()
            mock_db.add = MagicMock()
            mock_db.flush = AsyncMock()

            with patch("agentpal.zmq_bus.cron_daemon.uuid") as mock_uuid:
                mock_uuid.uuid4.return_value.hex = "abcd1234"
                mock_uuid.uuid4.return_value.__str__ = lambda self: "mock-uuid"

                await cron._notify_via_zmq(
                    job_name="Nightly Backup",
                    result="Backup completed: 500MB",
                    agent_name="backup-agent",
                    target_session_id=target_session_id,
                    db=mock_db,
                )

            # Collect events from subscriber
            events: list[dict[str, Any]] = []
            try:
                deadline = asyncio.get_event_loop().time() + 5.0
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        event = await asyncio.wait_for(
                            subscriber.__anext__(), timeout=2.0
                        )
                        if event.get("type") == "heartbeat":
                            continue
                        events.append(event)
                        # We expect at most 1-2 events
                        if len(events) >= 2:
                            break
                    except (StopAsyncIteration, asyncio.TimeoutError):
                        break
            finally:
                await subscriber.close()

            # Should have received a new_message event
            new_msg_events = [e for e in events if e.get("type") == "new_message"]
            assert len(new_msg_events) >= 1, (
                f"Expected new_message event, got event types: "
                f"{[e.get('type') for e in events]}"
            )

            msg_data = new_msg_events[0]["message"]
            assert msg_data["role"] == "assistant"
            assert "Nightly Backup" in msg_data["content"]
            assert "Backup completed" in msg_data["content"]

        finally:
            await cron.stop()

    @pytest.mark.asyncio
    async def test_cron_daemon_notify_without_target_session(
        self, zmq_manager: AgentDaemonManager
    ):
        """When no target_session_id, CronDaemon sends to generic 'pa:__cron__'."""
        from agentpal.zmq_bus.cron_daemon import CronDaemon

        routed_messages: list[Envelope] = []
        original_send = zmq_manager._send_to_daemon

        async def capture_send(target_identity: str, envelope: Envelope):
            routed_messages.append(envelope)
            await original_send(target_identity, envelope)

        zmq_manager._send_to_daemon = capture_send

        cron = CronDaemon()
        with patch.object(cron, "_scheduler_loop", new_callable=AsyncMock), \
             patch.object(cron, "_ensure_heartbeat_job", new_callable=AsyncMock):
            await cron.start(
                zmq_manager._ctx,
                zmq_manager._router_addr,
                zmq_manager._xsub_addr,
            )

        try:
            mock_db = AsyncMock()

            await cron._notify_via_zmq(
                job_name="Cleanup Job",
                result="Cleaned 50 files",
                agent_name="cleaner",
                target_session_id=None,  # No target session
                db=mock_db,
            )

            await asyncio.sleep(0.3)

            notify_msgs = [
                m for m in routed_messages
                if m.msg_type == ZmqMessageType.AGENT_NOTIFY
            ]
            assert len(notify_msgs) >= 1

            notify = notify_msgs[0]
            assert notify.target == "pa:__cron__"
            assert notify.session_id == "__cron__"
            assert notify.payload["job_name"] == "Cleanup Job"

        finally:
            await cron.stop()
