"""DingTalk 渠道单元测试。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentpal.channels.dingtalk import DingTalkChannel
from agentpal.channels.base import OutgoingMessage


@pytest.fixture
def channel():
    return DingTalkChannel()


class TestDingTalkParseIncoming:
    @pytest.mark.asyncio
    async def test_parse_text_message(self, channel: DingTalkChannel):
        payload = {
            "msgtype": "text",
            "text": {"content": "你好"},
            "senderStaffId": "user123",
            "conversationId": "conv456",
        }
        msg = await channel.parse_incoming(payload)
        assert msg is not None
        assert msg.text == "你好"
        assert msg.user_id == "user123"
        assert msg.session_id == "dingtalk:conv456"
        assert msg.channel == "dingtalk"

    @pytest.mark.asyncio
    async def test_parse_non_text_returns_none(self, channel: DingTalkChannel):
        payload = {"msgtype": "image", "content": {}}
        msg = await channel.parse_incoming(payload)
        assert msg is None

    @pytest.mark.asyncio
    async def test_parse_malformed_payload_returns_none(self, channel: DingTalkChannel):
        msg = await channel.parse_incoming({})
        assert msg is None

    @pytest.mark.asyncio
    async def test_parse_uses_sender_id_as_fallback(self, channel: DingTalkChannel):
        payload = {
            "msgtype": "text",
            "text": {"content": "test"},
            "senderStaffId": "staff001",
            # no conversationId
        }
        msg = await channel.parse_incoming(payload)
        assert msg is not None
        assert "staff001" in msg.session_id


class TestDingTalkSend:
    @pytest.mark.asyncio
    async def test_send_success(self, channel: DingTalkChannel):
        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 0, "errmsg": "ok"}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)

            result = await channel.send(
                OutgoingMessage(session_id="dingtalk:conv123", text="回复内容")
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_send_failure(self, channel: DingTalkChannel):
        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 400023, "errmsg": "error"}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)

            result = await channel.send(
                OutgoingMessage(session_id="dingtalk:conv123", text="test")
            )
            assert result is False
