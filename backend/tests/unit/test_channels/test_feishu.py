"""Feishu 渠道单元测试。"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentpal.channels.feishu import FeishuChannel
from agentpal.channels.base import OutgoingMessage


@pytest.fixture
def channel():
    return FeishuChannel()


def _make_payload(text: str, sender_id: str = "ou_abc", chat_id: str = "oc_xyz") -> dict:
    return {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": sender_id}},
            "message": {
                "message_type": "text",
                "content": json.dumps({"text": text}),
                "chat_id": chat_id,
            },
        },
    }


class TestFeishuParseIncoming:
    @pytest.mark.asyncio
    async def test_parse_text_message(self, channel: FeishuChannel):
        payload = _make_payload("飞书你好")
        msg = await channel.parse_incoming(payload)
        assert msg is not None
        assert msg.text == "飞书你好"
        assert msg.user_id == "ou_abc"
        assert msg.session_id == "feishu:oc_xyz"
        assert msg.channel == "feishu"

    @pytest.mark.asyncio
    async def test_challenge_returns_none(self, channel: FeishuChannel):
        msg = await channel.parse_incoming({"challenge": "abc123"})
        assert msg is None

    @pytest.mark.asyncio
    async def test_non_text_returns_none(self, channel: FeishuChannel):
        payload = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_abc"}},
                "message": {
                    "message_type": "image",
                    "content": "{}",
                    "chat_id": "oc_xyz",
                },
            },
        }
        msg = await channel.parse_incoming(payload)
        assert msg is None

    @pytest.mark.asyncio
    async def test_wrong_event_type_returns_none(self, channel: FeishuChannel):
        payload = {"header": {"event_type": "contact.user.created_v3"}}
        msg = await channel.parse_incoming(payload)
        assert msg is None

    @pytest.mark.asyncio
    async def test_malformed_payload_returns_none(self, channel: FeishuChannel):
        msg = await channel.parse_incoming({"broken": True})
        assert msg is None


class TestFeishuSend:
    @pytest.mark.asyncio
    async def test_send_success(self, channel: FeishuChannel):
        with patch.object(channel, "_get_tenant_token", return_value="mock-token"), \
             patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"code": 0, "msg": "success"}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)

            result = await channel.send(OutgoingMessage(session_id="feishu:oc_xyz", text="hello"))
            assert result is True

    @pytest.mark.asyncio
    async def test_send_failure(self, channel: FeishuChannel):
        with patch.object(channel, "_get_tenant_token", return_value="mock-token"), \
             patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"code": 99999, "msg": "error"}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)

            result = await channel.send(OutgoingMessage(session_id="feishu:oc_xyz", text="hi"))
            assert result is False
