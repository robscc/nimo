"""iMessage 渠道单元测试。"""

from __future__ import annotations

import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentpal.channels.imessage import IMessageChannel
from agentpal.channels.base import OutgoingMessage


@pytest.fixture
def channel():
    return IMessageChannel()


class TestIMessageParseIncoming:
    @pytest.mark.asyncio
    async def test_parse_incoming_message(self, channel: IMessageChannel):
        payload = {
            "handle": "+8612345678",
            "text": "iMessage 测试",
            "is_from_me": False,
            "rowid": 1,
        }
        msg = await channel.parse_incoming(payload)
        assert msg is not None
        assert msg.text == "iMessage 测试"
        assert msg.user_id == "+8612345678"
        assert msg.session_id == "imessage:+8612345678"

    @pytest.mark.asyncio
    async def test_skip_self_sent(self, channel: IMessageChannel):
        payload = {
            "handle": "+8612345678",
            "text": "我发的",
            "is_from_me": True,
            "rowid": 2,
        }
        msg = await channel.parse_incoming(payload)
        assert msg is None

    @pytest.mark.asyncio
    async def test_skip_empty_text(self, channel: IMessageChannel):
        payload = {
            "handle": "+8612345678",
            "text": "",
            "is_from_me": False,
            "rowid": 3,
        }
        msg = await channel.parse_incoming(payload)
        assert msg is None

    @pytest.mark.asyncio
    async def test_malformed_payload(self, channel: IMessageChannel):
        msg = await channel.parse_incoming({})
        assert msg is None


class TestIMessageSend:
    @pytest.mark.asyncio
    async def test_send_non_macos_returns_false(self, channel: IMessageChannel):
        with patch.object(sys, "platform", "linux"):
            result = await channel.send(
                OutgoingMessage(session_id="imessage:+8612345678", text="hi")
            )
            assert result is False

    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform != "darwin", reason="仅 macOS 可运行")
    async def test_send_macos_calls_osascript(self, channel: IMessageChannel):
        import subprocess
        mock_result = MagicMock(returncode=0)
        with patch("asyncio.to_thread", return_value=mock_result) as mock_thread:
            result = await channel.send(
                OutgoingMessage(session_id="imessage:+8612345678", text="test message")
            )
            assert result is True
            mock_thread.assert_called_once()
            args = mock_thread.call_args[0]
            assert args[0] == subprocess.run
            cmd = args[1]
            assert cmd[0] == "osascript"


class TestIMessageVerifySignature:
    @pytest.mark.asyncio
    async def test_always_returns_true(self, channel: IMessageChannel):
        result = await channel.verify_signature({}, b"body")
        assert result is True
