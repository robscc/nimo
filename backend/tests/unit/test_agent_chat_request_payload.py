"""Agent chat request file_ids 透传测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from agentpal.api.v1.endpoints.agent import ChatRequest, _chat_via_zmq


class _DummySubscriber:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        return {"type": "done", "_msg_id": "dummy"}


class _DummyZmqManager:
    def __init__(self):
        self.sent_envelope = None

    async def ensure_pa_daemon(self, session_id: str):
        return None

    def create_event_subscriber(self, topic: str, filter_msg_id: str):
        return _DummySubscriber()

    async def send_to_agent(self, target: str, envelope):
        self.sent_envelope = envelope


class TestChatRequestFileIds:
    def test_chat_request_accepts_file_ids(self):
        req = ChatRequest(
            session_id="s1",
            message="hello",
            file_ids=["f1", "f2"],
        )
        assert req.file_ids == ["f1", "f2"]

    @pytest.mark.asyncio
    async def test_chat_via_zmq_forwards_file_ids(self):
        req = ChatRequest(
            session_id="s2",
            message="analyze",
            file_ids=["file-a", "file-b"],
        )
        manager = _DummyZmqManager()

        await _chat_via_zmq(req, manager)

        assert manager.sent_envelope is not None
        assert manager.sent_envelope.payload["file_ids"] == ["file-a", "file-b"]
        assert manager.sent_envelope.payload["message"] == "analyze"
