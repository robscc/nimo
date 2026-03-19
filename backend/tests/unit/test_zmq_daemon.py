"""AgentDaemon base-class unit tests — real ZMQ inproc sockets."""

from __future__ import annotations

import asyncio
import time

import pytest
import zmq
import zmq.asyncio

from agentpal.zmq_bus.daemon import AgentDaemon
from agentpal.zmq_bus.protocol import Envelope, MessageType


# ── Concrete subclass for testing ────────────────────────

class RecordingDaemon(AgentDaemon):
    """Minimal subclass that records every handled message."""

    def __init__(self, identity: str) -> None:
        super().__init__(identity)
        self.handled: list[Envelope] = []

    async def handle_message(self, envelope: Envelope) -> None:
        self.handled.append(envelope)


# ── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def zmq_ctx():
    """Create a shared ZMQ async context; terminate after test."""
    ctx = zmq.asyncio.Context()
    yield ctx
    ctx.term()


@pytest.fixture
def inproc_addrs():
    """Generate unique inproc addresses per test to avoid collisions."""
    import uuid

    uid = uuid.uuid4().hex[:8]
    return {
        "router": f"inproc://test-router-{uid}",
        "events": f"inproc://test-events-{uid}",
    }


@pytest.fixture
async def router_socket(zmq_ctx, inproc_addrs):
    """Bind a ROUTER socket that the daemon's DEALER will connect to."""
    sock = zmq_ctx.socket(zmq.ROUTER)
    sock.setsockopt(zmq.LINGER, 0)
    sock.bind(inproc_addrs["router"])
    yield sock
    sock.close(linger=0)


@pytest.fixture
async def xsub_socket(zmq_ctx, inproc_addrs):
    """Bind an XSUB socket that the daemon's PUB will connect to."""
    sock = zmq_ctx.socket(zmq.XSUB)
    sock.setsockopt(zmq.LINGER, 0)
    sock.bind(inproc_addrs["events"])
    yield sock
    sock.close(linger=0)


@pytest.fixture
async def daemon(zmq_ctx, inproc_addrs, router_socket, xsub_socket):
    """Start a RecordingDaemon connected to the test ROUTER/XSUB sockets."""
    d = RecordingDaemon(identity="test-daemon")
    await d.start(zmq_ctx, inproc_addrs["router"], inproc_addrs["events"])
    yield d
    if d.is_running:
        await d.stop()


# ── Helpers ──────────────────────────────────────────────

def _make_envelope(
    msg_type: MessageType = MessageType.CHAT_REQUEST,
    source: str = "router",
    target: str = "test-daemon",
    payload: dict | None = None,
) -> Envelope:
    return Envelope(
        msg_type=msg_type,
        source=source,
        target=target,
        payload=payload or {"text": "hello"},
    )


async def _send_via_router(
    router: zmq.asyncio.Socket,
    identity: str,
    envelope: Envelope,
) -> None:
    """Simulate the manager sending a message through ROUTER to a daemon."""
    await router.send_multipart([
        identity.encode("utf-8"),
        b"",
        envelope.serialize(),
    ])


# ── Tests ────────────────────────────────────────────────


class TestAgentDaemon:
    """AgentDaemon lifecycle, FIFO processing, PUB, and DEALER."""

    @pytest.mark.asyncio
    async def test_daemon_start_stop(self, daemon):
        """Start → is_running is True; stop → is_running is False."""
        assert daemon.is_running is True

        await daemon.stop()

        assert daemon.is_running is False

    @pytest.mark.asyncio
    async def test_daemon_fifo_processing(self, daemon, router_socket):
        """Send 3 messages; they should be processed in FIFO order."""
        envs = []
        for i in range(3):
            e = _make_envelope(payload={"seq": i})
            envs.append(e)
            await _send_via_router(router_socket, "test-daemon", e)

        # Wait for all 3 to be handled (with timeout)
        deadline = time.monotonic() + 5.0
        while len(daemon.handled) < 3 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

        assert len(daemon.handled) == 3
        for idx, handled in enumerate(daemon.handled):
            assert handled.payload["seq"] == idx

    @pytest.mark.asyncio
    async def test_daemon_publish_event(self, zmq_ctx, inproc_addrs, router_socket, xsub_socket):
        """publish_event sends topic + envelope via PUB socket.

        We bypass the normal PUB-connect pattern and instead:
        1. Have the daemon PUB *bind* on an address
        2. Create a SUB that *connects* to that address
        This avoids the ZMQ "slow joiner" problem with PUB-connect/SUB-bind.
        """
        import uuid

        pub_addr = f"inproc://test-pub-{uuid.uuid4().hex[:8]}"

        # Start daemon (PUB initially connects to xsub_socket, we'll replace it)
        d = RecordingDaemon(identity="pub-test-daemon")
        await d.start(zmq_ctx, inproc_addrs["router"], inproc_addrs["events"])

        try:
            # Override the daemon's PUB socket: close old one, create a binding one
            if d._pub is not None:
                d._pub.close(linger=0)
            d._pub = zmq_ctx.socket(zmq.PUB)
            d._pub.setsockopt(zmq.LINGER, 0)
            d._pub.bind(pub_addr)

            # SUB connects to the PUB
            sub_sock = zmq_ctx.socket(zmq.SUB)
            sub_sock.setsockopt(zmq.LINGER, 0)
            sub_sock.connect(pub_addr)
            sub_sock.subscribe(b"")

            # Allow subscription to propagate
            await asyncio.sleep(0.1)

            topic = "session:test-123"
            env = _make_envelope(
                msg_type=MessageType.STREAM_EVENT,
                payload={"type": "text_delta", "delta": "hi"},
            )

            await d.publish_event(topic, env)

            frames = await asyncio.wait_for(sub_sock.recv_multipart(), timeout=3.0)
            assert len(frames) == 2

            received_topic = frames[0].decode("utf-8")
            assert received_topic == topic

            received_env = Envelope.deserialize(frames[1])
            assert received_env.msg_type == MessageType.STREAM_EVENT
            assert received_env.payload["delta"] == "hi"

            sub_sock.close(linger=0)
        finally:
            await d.stop()

    @pytest.mark.asyncio
    async def test_daemon_send_to_router(self, daemon, router_socket):
        """send_to_router sends via DEALER; ROUTER receives it."""
        env = _make_envelope(
            msg_type=MessageType.AGENT_REQUEST,
            source="test-daemon",
            target="another-daemon",
            payload={"data": "ping"},
        )

        await daemon.send_to_router(env)

        # ROUTER receives: [identity, b"", envelope_bytes]
        frames = await asyncio.wait_for(router_socket.recv_multipart(), timeout=3.0)
        assert len(frames) == 3

        identity = frames[0].decode("utf-8")
        assert identity == "test-daemon"

        assert frames[1] == b""

        received_env = Envelope.deserialize(frames[2])
        assert received_env.msg_type == MessageType.AGENT_REQUEST
        assert received_env.payload["data"] == "ping"

    @pytest.mark.asyncio
    async def test_daemon_last_active_at_updates(self, daemon, router_socket):
        """Processing a message should update last_active_at."""
        initial_active = daemon.last_active_at

        # Small sleep to ensure time difference
        await asyncio.sleep(0.05)

        env = _make_envelope()
        await _send_via_router(router_socket, "test-daemon", env)

        # Wait for the message to be processed
        deadline = time.monotonic() + 5.0
        while len(daemon.handled) < 1 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

        assert len(daemon.handled) == 1
        assert daemon.last_active_at > initial_active

    @pytest.mark.asyncio
    async def test_daemon_handle_message_not_implemented(self, zmq_ctx, inproc_addrs, router_socket, xsub_socket):
        """Base AgentDaemon.handle_message should raise NotImplementedError."""
        base = AgentDaemon(identity="base-daemon")
        await base.start(zmq_ctx, inproc_addrs["router"], inproc_addrs["events"])

        try:
            with pytest.raises(NotImplementedError):
                env = _make_envelope()
                await base.handle_message(env)
        finally:
            await base.stop()

    @pytest.mark.asyncio
    async def test_daemon_stop_idempotent(self, daemon):
        """Calling stop twice should not raise."""
        await daemon.stop()
        await daemon.stop()  # second call should be safe

        assert daemon.is_running is False
