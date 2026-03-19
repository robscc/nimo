"""ZMQ protocol (Envelope / MessageType) unit tests."""

from __future__ import annotations

import time
import uuid

import pytest

from agentpal.zmq_bus.protocol import Envelope, MessageType


class TestMessageType:
    """MessageType enum tests."""

    def test_all_message_types_are_strings(self):
        """Every MessageType member should be a string."""
        for mt in MessageType:
            assert isinstance(mt, str)
            assert isinstance(mt.value, str)
            assert len(mt.value) > 0


class TestEnvelope:
    """Envelope creation, serialization, and helper methods."""

    def _make_envelope(self, **overrides) -> Envelope:
        defaults = dict(
            msg_id="test-msg-001",
            msg_type=MessageType.CHAT_REQUEST,
            source="pa:session-aaa",
            target="sub:coder:task-bbb",
            reply_to="orig-msg-000",
            session_id="session-aaa",
            payload={"prompt": "hello"},
            timestamp=1700000000.0,
        )
        defaults.update(overrides)
        return Envelope(**defaults)

    # ── Creation ──────────────────────────────────────────

    def test_envelope_create(self):
        """Create Envelope with all fields and verify attributes."""
        env = self._make_envelope()

        assert env.msg_id == "test-msg-001"
        assert env.msg_type == MessageType.CHAT_REQUEST
        assert env.source == "pa:session-aaa"
        assert env.target == "sub:coder:task-bbb"
        assert env.reply_to == "orig-msg-000"
        assert env.session_id == "session-aaa"
        assert env.payload == {"prompt": "hello"}
        assert env.timestamp == 1700000000.0

    def test_envelope_default_msg_id(self):
        """Auto-generated msg_id should be a valid UUID4."""
        env = Envelope(
            msg_type=MessageType.CHAT_REQUEST,
            source="a",
            target="b",
        )
        # Should be a valid UUID string
        parsed = uuid.UUID(env.msg_id, version=4)
        assert str(parsed) == env.msg_id

    def test_envelope_default_timestamp(self):
        """Auto-generated timestamp should be close to now."""
        before = time.time()
        env = Envelope(
            msg_type=MessageType.CHAT_REQUEST,
            source="a",
            target="b",
        )
        after = time.time()

        assert before <= env.timestamp <= after

    def test_envelope_with_optional_fields(self):
        """reply_to=None and session_id=None should be accepted."""
        env = Envelope(
            msg_type=MessageType.AGENT_NOTIFY,
            source="src",
            target="tgt",
            reply_to=None,
            session_id=None,
        )
        assert env.reply_to is None
        assert env.session_id is None

    # ── Serialization ─────────────────────────────────────

    def test_envelope_serialize_deserialize_roundtrip(self):
        """Serialize then deserialize; all fields should match."""
        original = self._make_envelope()
        raw = original.serialize()

        assert isinstance(raw, bytes)
        assert len(raw) > 0

        restored = Envelope.deserialize(raw)

        assert restored.msg_id == original.msg_id
        assert restored.msg_type == original.msg_type
        assert restored.source == original.source
        assert restored.target == original.target
        assert restored.reply_to == original.reply_to
        assert restored.session_id == original.session_id
        assert restored.payload == original.payload
        assert restored.timestamp == original.timestamp

    def test_envelope_with_large_payload(self):
        """Roundtrip with a 10 KB+ payload should work correctly."""
        large_data = "x" * 11_000  # > 10 KB
        env = self._make_envelope(
            payload={"data": large_data, "nested": {"key": list(range(200))}}
        )
        raw = env.serialize()
        restored = Envelope.deserialize(raw)

        assert restored.payload["data"] == large_data
        assert restored.payload["nested"]["key"] == list(range(200))

    def test_envelope_roundtrip_preserves_all_message_types(self):
        """Every MessageType should survive a serialize/deserialize cycle."""
        for mt in MessageType:
            env = Envelope(msg_type=mt, source="s", target="t")
            restored = Envelope.deserialize(env.serialize())
            assert restored.msg_type == mt

    # ── make_reply ────────────────────────────────────────

    def test_envelope_make_reply(self):
        """make_reply should swap source/target and set reply_to."""
        original = self._make_envelope(
            msg_id="req-111",
            source="pa:session-aaa",
            target="sub:coder:task-bbb",
            session_id="session-aaa",
        )

        reply = original.make_reply(
            msg_type=MessageType.AGENT_RESPONSE,
            payload={"result": "done"},
        )

        # target becomes source, source becomes target
        assert reply.source == "sub:coder:task-bbb"   # was target
        assert reply.target == "pa:session-aaa"        # was source
        assert reply.reply_to == "req-111"             # original msg_id
        assert reply.session_id == "session-aaa"       # preserved
        assert reply.msg_type == MessageType.AGENT_RESPONSE
        assert reply.payload == {"result": "done"}

        # reply should have its own new msg_id
        assert reply.msg_id != original.msg_id

    def test_envelope_make_reply_custom_source(self):
        """make_reply with explicit source overrides default."""
        original = self._make_envelope()
        reply = original.make_reply(
            msg_type=MessageType.AGENT_RESPONSE,
            source="custom:identity",
        )
        assert reply.source == "custom:identity"

    def test_envelope_make_reply_empty_payload(self):
        """make_reply without payload defaults to empty dict."""
        original = self._make_envelope()
        reply = original.make_reply(msg_type=MessageType.AGENT_RESPONSE)
        assert reply.payload == {}
