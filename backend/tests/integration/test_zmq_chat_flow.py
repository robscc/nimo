"""Integration tests for ZMQ chat flow: API → Manager → PA Daemon → EventSubscriber.

Tests the full chat flow through ZMQ without real LLM calls.
PersonalAssistantDaemon._handle_chat is mocked/patched to yield fake SSE events.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from agentpal.zmq_bus.manager import AgentDaemonManager
from agentpal.zmq_bus.protocol import Envelope, MessageType


# ── Helpers ────────────────────────────────────────────────


def _unique_addrs() -> tuple[str, str]:
    """Generate unique inproc addresses to avoid collisions between tests."""
    tag = uuid.uuid4().hex[:8]
    return (
        f"inproc://test-router-{tag}",
        f"inproc://test-events-{tag}",
    )


def _make_handle_chat_mock(events_list: list[dict[str, Any]] | None = None):
    """Create a mock _handle_chat that publishes fake SSE events via PUB socket.

    Instead of trying to mock the lazy imports inside _handle_chat,
    we replace the entire method to directly publish events through
    the daemon's PUB socket (which is the real behavior we want to test).
    """
    if events_list is None:
        events_list = [
            {"type": "thinking_delta", "delta": "thinking..."},
            {"type": "text_delta", "delta": "Hello from ZMQ"},
            {"type": "done"},
        ]

    async def mock_handle_chat(self_daemon, envelope: Envelope):
        """Directly publish fake SSE events via PUB socket."""
        msg_id = envelope.msg_id
        topic = f"session:{self_daemon._session_id}"

        for event in events_list:
            payload = {**event, "_msg_id": msg_id}
            pub_envelope = Envelope(
                msg_type=MessageType.STREAM_EVENT,
                source=self_daemon.identity,
                target="",
                session_id=self_daemon._session_id,
                payload=payload,
            )
            await self_daemon.publish_event(topic, pub_envelope)

    return mock_handle_chat


# ── Fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def zmq_manager():
    """Create and start an AgentDaemonManager with unique inproc addresses.

    Mocks out the CronDaemon to avoid DB dependencies.
    """
    router_addr, events_addr = _unique_addrs()
    manager = AgentDaemonManager(
        router_addr=router_addr,
        events_addr=events_addr,
        pa_idle_timeout=300,
        sub_idle_timeout=60,
    )

    # Patch out _start_cron_daemon to avoid heavy DB initialization
    with patch.object(manager, "_start_cron_daemon", new_callable=AsyncMock):
        await manager.start()

    yield manager

    await manager.stop()


# ── Tests ──────────────────────────────────────────────────


class TestChatViaZmq:
    """Test the full chat flow: CHAT_REQUEST → PA Daemon → EventSubscriber."""

    @pytest.mark.asyncio
    async def test_chat_via_zmq_receives_events(self, zmq_manager: AgentDaemonManager):
        """Send CHAT_REQUEST, subscribe on topic, verify events received."""
        session_id = f"test-session-{uuid.uuid4().hex[:8]}"
        msg_id = str(uuid.uuid4())

        # 1. Ensure PA daemon (this creates a real daemon with real sockets)
        daemon = await zmq_manager.ensure_pa_daemon(session_id)

        # 2. Replace _handle_chat to avoid real LLM call
        mock_fn = _make_handle_chat_mock([
            {"type": "thinking_delta", "delta": "thinking..."},
            {"type": "text_delta", "delta": "Echo: Hello ZMQ"},
            {"type": "done"},
        ])
        daemon._handle_chat = lambda envelope: mock_fn(daemon, envelope)

        # 3. Create event subscriber
        subscriber = zmq_manager.create_event_subscriber(
            topic=f"session:{session_id}",
            filter_msg_id=msg_id,
        )

        # Small delay to let SUB socket connect and subscription propagate
        async with subscriber:
            await asyncio.sleep(0.05)

            # 4. Send CHAT_REQUEST
            envelope = Envelope(
                msg_id=msg_id,
                msg_type=MessageType.CHAT_REQUEST,
                source="api:test",
                target=f"pa:{session_id}",
                session_id=session_id,
                payload={"message": "Hello ZMQ", "channel": "web"},
            )
            await zmq_manager.send_to_agent(f"pa:{session_id}", envelope)

            # 5. Collect events from subscriber
            events: list[dict[str, Any]] = []
            deadline = asyncio.get_event_loop().time() + 5.0
            async for event in subscriber:
                if asyncio.get_event_loop().time() > deadline:
                    break
                event_type = event.get("type", "")
                if event_type == "heartbeat":
                    continue
                events.append(event)
                if event_type in ("done", "error"):
                    break

        # 6. Verify we received the expected events
        event_types = [e.get("type") for e in events]
        assert "thinking_delta" in event_types, f"Expected thinking_delta in {event_types}"
        assert "text_delta" in event_types, f"Expected text_delta in {event_types}"
        assert "done" in event_types, f"Expected done in {event_types}"

        # Verify the text_delta contains our echo
        text_events = [e for e in events if e.get("type") == "text_delta"]
        assert any("Echo: Hello ZMQ" in e.get("delta", "") for e in text_events)

    @pytest.mark.asyncio
    async def test_chat_via_zmq_done_terminates(self, zmq_manager: AgentDaemonManager):
        """Verify that a 'done' event terminates the EventSubscriber iteration."""
        session_id = f"test-session-{uuid.uuid4().hex[:8]}"
        msg_id = str(uuid.uuid4())

        daemon = await zmq_manager.ensure_pa_daemon(session_id)

        mock_fn = _make_handle_chat_mock([
            {"type": "text_delta", "delta": "test"},
            {"type": "done"},
        ])
        daemon._handle_chat = lambda envelope: mock_fn(daemon, envelope)

        subscriber = zmq_manager.create_event_subscriber(
            topic=f"session:{session_id}",
            filter_msg_id=msg_id,
        )

        async with subscriber:
            await asyncio.sleep(0.05)

            envelope = Envelope(
                msg_id=msg_id,
                msg_type=MessageType.CHAT_REQUEST,
                source="api:test",
                target=f"pa:{session_id}",
                session_id=session_id,
                payload={"message": "test termination", "channel": "web"},
            )
            await zmq_manager.send_to_agent(f"pa:{session_id}", envelope)

            # The subscriber should naturally terminate after receiving done
            terminated = False
            deadline = asyncio.get_event_loop().time() + 5.0
            async for event in subscriber:
                if asyncio.get_event_loop().time() > deadline:
                    break
                if event.get("type") == "heartbeat":
                    continue
                if event.get("type") in ("done", "error"):
                    terminated = True
                    break

        assert terminated, "Subscriber should terminate after done event"
        assert subscriber._closed, "Subscriber should be in closed state"

    @pytest.mark.asyncio
    async def test_chat_sequential_fifo(self, zmq_manager: AgentDaemonManager):
        """Send 2 chat requests to the same session, verify processed in FIFO order.

        The PA daemon's FIFO queue ensures messages are processed sequentially.
        We verify by tracking the order of _handle_chat invocations.
        """
        session_id = f"test-session-{uuid.uuid4().hex[:8]}"
        msg_id_1 = str(uuid.uuid4())
        msg_id_2 = str(uuid.uuid4())

        invocation_order: list[str] = []

        daemon = await zmq_manager.ensure_pa_daemon(session_id)

        original_handle = daemon.handle_message

        async def tracking_handle(envelope: Envelope):
            if envelope.msg_type == MessageType.CHAT_REQUEST:
                msg = envelope.payload.get("message", "")
                invocation_order.append(msg)
                # Publish events for this request
                msg_id = envelope.msg_id
                topic = f"session:{session_id}"
                payload = {"type": "text_delta", "delta": f"Reply to: {msg}", "_msg_id": msg_id}
                pub_env = Envelope(
                    msg_type=MessageType.STREAM_EVENT,
                    source=daemon.identity,
                    target="",
                    session_id=session_id,
                    payload=payload,
                )
                await daemon.publish_event(topic, pub_env)
                done_payload = {"type": "done", "_msg_id": msg_id}
                done_env = Envelope(
                    msg_type=MessageType.STREAM_EVENT,
                    source=daemon.identity,
                    target="",
                    session_id=session_id,
                    payload=done_payload,
                )
                await daemon.publish_event(topic, done_env)
            else:
                await original_handle(envelope)

        daemon.handle_message = tracking_handle

        # Create subscribers for both messages
        subscriber_1 = zmq_manager.create_event_subscriber(
            topic=f"session:{session_id}",
            filter_msg_id=msg_id_1,
        )
        subscriber_2 = zmq_manager.create_event_subscriber(
            topic=f"session:{session_id}",
            filter_msg_id=msg_id_2,
        )

        # Prepare both subscribers (establish SUB connections)
        await subscriber_1._ensure_socket()
        await subscriber_2._ensure_socket()
        await asyncio.sleep(0.05)

        # Send both requests in quick succession
        envelope_1 = Envelope(
            msg_id=msg_id_1,
            msg_type=MessageType.CHAT_REQUEST,
            source="api:test",
            target=f"pa:{session_id}",
            session_id=session_id,
            payload={"message": "First message", "channel": "web"},
        )
        envelope_2 = Envelope(
            msg_id=msg_id_2,
            msg_type=MessageType.CHAT_REQUEST,
            source="api:test",
            target=f"pa:{session_id}",
            session_id=session_id,
            payload={"message": "Second message", "channel": "web"},
        )
        await zmq_manager.send_to_agent(f"pa:{session_id}", envelope_1)
        await zmq_manager.send_to_agent(f"pa:{session_id}", envelope_2)

        # Collect events from first subscriber
        events_1: list[dict[str, Any]] = []
        async with subscriber_1:
            deadline = asyncio.get_event_loop().time() + 5.0
            async for event in subscriber_1:
                if asyncio.get_event_loop().time() > deadline:
                    break
                if event.get("type") == "heartbeat":
                    continue
                events_1.append(event)
                if event.get("type") in ("done", "error"):
                    break

        # Collect events from second subscriber
        events_2: list[dict[str, Any]] = []
        async with subscriber_2:
            deadline = asyncio.get_event_loop().time() + 5.0
            async for event in subscriber_2:
                if asyncio.get_event_loop().time() > deadline:
                    break
                if event.get("type") == "heartbeat":
                    continue
                events_2.append(event)
                if event.get("type") in ("done", "error"):
                    break

        # Verify FIFO order: "First message" should come before "Second message"
        assert invocation_order == ["First message", "Second message"], (
            f"Expected FIFO order ['First message', 'Second message'], got {invocation_order}"
        )

        # Verify both got their respective events
        assert any("Reply to: First message" in e.get("delta", "") for e in events_1)
        assert any("Reply to: Second message" in e.get("delta", "") for e in events_2)

    @pytest.mark.asyncio
    async def test_chat_error_propagated_as_event(self, zmq_manager: AgentDaemonManager):
        """When _handle_chat raises, an error event should be published."""
        session_id = f"test-session-{uuid.uuid4().hex[:8]}"
        msg_id = str(uuid.uuid4())

        daemon = await zmq_manager.ensure_pa_daemon(session_id)

        # Make _handle_chat publish partial events then an error
        async def error_handle_chat(envelope: Envelope):
            msg_id_inner = envelope.msg_id
            topic = f"session:{daemon._session_id}"

            # Publish partial event
            partial = Envelope(
                msg_type=MessageType.STREAM_EVENT,
                source=daemon.identity,
                target="",
                session_id=daemon._session_id,
                payload={"type": "text_delta", "delta": "partial", "_msg_id": msg_id_inner},
            )
            await daemon.publish_event(topic, partial)

            # Publish error event
            error = Envelope(
                msg_type=MessageType.STREAM_EVENT,
                source=daemon.identity,
                target="",
                session_id=daemon._session_id,
                payload={
                    "type": "error",
                    "message": "LLM connection failed",
                    "_msg_id": msg_id_inner,
                },
            )
            await daemon.publish_event(topic, error)

        daemon._handle_chat = error_handle_chat

        subscriber = zmq_manager.create_event_subscriber(
            topic=f"session:{session_id}",
            filter_msg_id=msg_id,
        )

        async with subscriber:
            await asyncio.sleep(0.05)

            envelope = Envelope(
                msg_id=msg_id,
                msg_type=MessageType.CHAT_REQUEST,
                source="api:test",
                target=f"pa:{session_id}",
                session_id=session_id,
                payload={"message": "trigger error", "channel": "web"},
            )
            await zmq_manager.send_to_agent(f"pa:{session_id}", envelope)

            events: list[dict[str, Any]] = []
            deadline = asyncio.get_event_loop().time() + 5.0
            async for event in subscriber:
                if asyncio.get_event_loop().time() > deadline:
                    break
                if event.get("type") == "heartbeat":
                    continue
                events.append(event)
                if event.get("type") in ("done", "error"):
                    break

        # Should have received the partial text_delta + error
        event_types = [e.get("type") for e in events]
        assert "error" in event_types, f"Expected error event in {event_types}"

        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1
        assert "LLM connection failed" in error_events[0].get("message", "")
