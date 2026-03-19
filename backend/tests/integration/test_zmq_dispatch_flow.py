"""Integration tests for ZMQ SubAgent dispatch flow.

Tests SubAgent task dispatch through ZMQ:
- SubAgentDaemon creation and registration
- Task event publishing (task.started, task.completed, etc.)
- Event subscriber receiving task events

NOTE: The XSUB→XPUB event proxy in AgentDaemonManager polls with a 1s timeout.
For tests that subscribe to PUB events, we must ensure subscriptions propagate
before events are published. We do this by:
1. Starting the daemon (connects PUB to XSUB)
2. Creating the subscriber (connects SUB to XPUB)
3. Waiting 0.2s for subscription propagation
4. THEN dispatching the task (which triggers event publishing)
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
from agentpal.zmq_bus.sub_daemon import SubAgentDaemon


# ── Helpers ────────────────────────────────────────────────


def _unique_addrs() -> tuple[str, str]:
    """Generate unique inproc addresses to avoid collisions between tests."""
    tag = uuid.uuid4().hex[:8]
    return (
        f"inproc://test-router-{tag}",
        f"inproc://test-events-{tag}",
    )


def _make_dispatch_envelope(
    task_id: str,
    parent_session_id: str,
    agent_name: str,
    identity: str,
) -> Envelope:
    """Create a DISPATCH_TASK envelope."""
    return Envelope(
        msg_type=MessageType.DISPATCH_TASK,
        source=f"pa:{parent_session_id}",
        target=identity,
        session_id=parent_session_id,
        payload={
            "task_id": task_id,
            "task_prompt": "Test task",
            "parent_session_id": parent_session_id,
            "model_config": {},
            "role_prompt": "",
            "max_tool_rounds": 8,
        },
    )


async def _collect_events(
    subscriber, timeout: float = 8.0, stop_on: tuple[str, ...] = ("task.completed", "task.failed"),
) -> list[dict[str, Any]]:
    """Collect events from subscriber until stop event or timeout."""
    events: list[dict[str, Any]] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            event = await asyncio.wait_for(subscriber.__anext__(), timeout=3.0)
            if event.get("type") == "heartbeat":
                continue
            events.append(event)
            event_type = event.get("event_type", "")
            if event_type in stop_on:
                break
        except (StopAsyncIteration, asyncio.TimeoutError):
            break
    return events


# ── Fixtures ───────────────────────────────────────────────


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


# ── Tests ──────────────────────────────────────────────────


class TestDispatchCreatesSubDaemon:
    """Test that dispatching a task creates a SubAgentDaemon in manager."""

    @pytest.mark.asyncio
    async def test_dispatch_creates_sub_daemon(self, zmq_manager: AgentDaemonManager):
        """Dispatch task via manager, verify SubAgentDaemon is registered."""
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        parent_session_id = f"session-{uuid.uuid4().hex[:8]}"
        agent_name = "coder"

        assert zmq_manager.sub_daemon_count == 0

        with patch.object(
            SubAgentDaemon, "_run_task",
            new_callable=AsyncMock,
            return_value="Task completed successfully",
        ):
            daemon = await zmq_manager.create_sub_daemon(
                agent_name=agent_name,
                task_id=task_id,
                task_prompt="Write a hello world function",
                parent_session_id=parent_session_id,
                role_prompt="You are a coder.",
                max_tool_rounds=4,
            )

            assert daemon is not None
            assert daemon.is_running
            assert daemon.identity == f"sub:{agent_name}:{task_id}"
            assert zmq_manager.sub_daemon_count == 1

            identity = f"sub:{agent_name}:{task_id}"
            assert identity in zmq_manager._sub_daemons

    @pytest.mark.asyncio
    async def test_dispatch_multiple_sub_daemons(self, zmq_manager: AgentDaemonManager):
        """Dispatch multiple tasks, verify each creates its own daemon."""
        parent_session_id = f"session-{uuid.uuid4().hex[:8]}"

        with patch.object(
            SubAgentDaemon, "_run_task",
            new_callable=AsyncMock,
            return_value="Done",
        ):
            task_ids = []
            for i in range(3):
                task_id = f"task-{uuid.uuid4().hex[:8]}"
                task_ids.append(task_id)
                await zmq_manager.create_sub_daemon(
                    agent_name=f"agent-{i}",
                    task_id=task_id,
                    task_prompt=f"Task {i}",
                    parent_session_id=parent_session_id,
                )

            assert zmq_manager.sub_daemon_count == 3
            identities = set(zmq_manager._sub_daemons.keys())
            assert len(identities) == 3


class TestDispatchTaskEvents:
    """Test that task dispatch publishes events to ZMQ PUB.

    These tests manually start the daemon, create the subscriber, wait for
    subscription propagation, and THEN dispatch the task. This avoids the
    race condition where events are published before the subscriber is ready.
    """

    @pytest.mark.asyncio
    async def test_dispatch_task_events_published(self, zmq_manager: AgentDaemonManager):
        """Dispatch task → task.started + task.completed events received."""
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        parent_session_id = f"session-{uuid.uuid4().hex[:8]}"
        agent_name = "researcher"
        identity = f"sub:{agent_name}:{task_id}"
        topic = f"task:{task_id}"

        with patch.object(
            SubAgentDaemon, "_run_task",
            new_callable=AsyncMock,
            return_value="Research result: Found 42 papers",
        ):
            # 1. Start daemon (PUB connects to XSUB)
            daemon = SubAgentDaemon(agent_name=agent_name, task_id=task_id)
            await daemon.start(
                zmq_manager._ctx,
                zmq_manager._router_addr,
                zmq_manager._xsub_addr,
            )
            zmq_manager._sub_daemons[identity] = daemon

            # 2. Create subscriber (SUB connects to XPUB)
            subscriber = zmq_manager.create_event_subscriber(topic=topic)
            await subscriber._ensure_socket()

            # 3. Wait for subscription propagation
            await asyncio.sleep(0.2)

            # 4. Dispatch task
            envelope = _make_dispatch_envelope(
                task_id, parent_session_id, agent_name, identity,
            )
            envelope.payload["task_prompt"] = "Research topic X"
            await zmq_manager.send_to_agent(identity, envelope)

            # 5. Collect events
            try:
                events = await _collect_events(subscriber)
            finally:
                await subscriber.close()

            # Verify
            event_types = [e.get("event_type") for e in events]
            assert "task.started" in event_types, f"Expected task.started in {event_types}"
            assert "task.completed" in event_types, f"Expected task.completed in {event_types}"

            started = next(e for e in events if e.get("event_type") == "task.started")
            assert started["data"]["task_id"] == task_id
            assert started["data"]["agent_name"] == agent_name

            completed = next(e for e in events if e.get("event_type") == "task.completed")
            assert completed["data"]["task_id"] == task_id
            assert "Research result" in completed["data"].get("result", "")

    @pytest.mark.asyncio
    async def test_dispatch_task_failure_publishes_error_event(
        self, zmq_manager: AgentDaemonManager
    ):
        """When _run_task raises → task.started + task.failed events received."""
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        parent_session_id = f"session-{uuid.uuid4().hex[:8]}"
        agent_name = "coder"
        identity = f"sub:{agent_name}:{task_id}"
        topic = f"task:{task_id}"

        with patch.object(
            SubAgentDaemon, "_run_task",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Database connection lost"),
        ):
            # 1. Start daemon
            daemon = SubAgentDaemon(agent_name=agent_name, task_id=task_id)
            await daemon.start(
                zmq_manager._ctx,
                zmq_manager._router_addr,
                zmq_manager._xsub_addr,
            )
            zmq_manager._sub_daemons[identity] = daemon

            # 2. Create subscriber
            subscriber = zmq_manager.create_event_subscriber(topic=topic)
            await subscriber._ensure_socket()

            # 3. Wait for subscription propagation
            await asyncio.sleep(0.2)

            # 4. Dispatch task
            envelope = _make_dispatch_envelope(
                task_id, parent_session_id, agent_name, identity,
            )
            envelope.payload["task_prompt"] = "Do something"
            await zmq_manager.send_to_agent(identity, envelope)

            # 5. Collect events
            try:
                events = await _collect_events(subscriber)
            finally:
                await subscriber.close()

            # Verify
            event_types = [e.get("event_type") for e in events]
            assert "task.started" in event_types, f"Expected task.started in {event_types}"
            assert "task.failed" in event_types, f"Expected task.failed in {event_types}"

            failed = next(e for e in events if e.get("event_type") == "task.failed")
            assert "Database connection lost" in failed["data"].get("error", "")

    @pytest.mark.asyncio
    async def test_dispatch_sub_daemon_sends_response_to_pa(
        self, zmq_manager: AgentDaemonManager
    ):
        """After task completion, SubAgentDaemon sends AGENT_RESPONSE to PA daemon."""
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        parent_session_id = f"session-{uuid.uuid4().hex[:8]}"
        agent_name = "coder"

        notifications_received: list[Envelope] = []

        pa_daemon = await zmq_manager.ensure_pa_daemon(parent_session_id)

        async def capture_handle(envelope: Envelope):
            if envelope.msg_type == MessageType.AGENT_RESPONSE:
                notifications_received.append(envelope)

        pa_daemon.handle_message = capture_handle

        with patch.object(
            SubAgentDaemon, "_run_task",
            new_callable=AsyncMock,
            return_value="Task done",
        ):
            await zmq_manager.create_sub_daemon(
                agent_name=agent_name,
                task_id=task_id,
                task_prompt="Do work",
                parent_session_id=parent_session_id,
            )

            # Wait for full round-trip:
            # SubAgent DEALER → ROUTER → PA DEALER → PA handle_message
            await asyncio.sleep(1.5)

        assert len(notifications_received) >= 1, (
            "PA daemon should have received AGENT_RESPONSE from SubAgent"
        )
        resp = notifications_received[0]
        assert resp.payload.get("task_id") == task_id
        assert resp.payload.get("status") in ("done", "failed")
