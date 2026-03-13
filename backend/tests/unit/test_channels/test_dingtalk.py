"""DingTalk 渠道单元测试（Webhook 模式）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentpal.channels.dingtalk import DingTalkChannel
from agentpal.channels.base import OutgoingMessage


# ── Fixtures ──────────────────────────────────────────────

@pytest.fixture
def channel():
    return DingTalkChannel()


def _make_valid_signature(secret: str) -> tuple[str, str]:
    """生成合法的 timestamp + sign 对，用于测试。"""
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.HMAC(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


# ── parse_incoming ────────────────────────────────────────

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

    @pytest.mark.asyncio
    async def test_parse_trims_whitespace(self, channel: DingTalkChannel):
        payload = {
            "msgtype": "text",
            "text": {"content": "  hello world  "},
            "senderStaffId": "user1",
            "conversationId": "conv1",
        }
        msg = await channel.parse_incoming(payload)
        assert msg is not None
        assert msg.text == "hello world"


# ── send ─────────────────────────────────────────────────

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


# ── verify_signature ──────────────────────────────────────

class TestDingTalkVerifySignature:
    @pytest.mark.asyncio
    async def test_no_secret_always_passes(self, channel: DingTalkChannel):
        """未配置 app_secret 时，任何请求直接通过。"""
        with patch.object(channel._settings, "dingtalk_app_secret", ""):
            result = await channel.verify_signature(
                {"timestamp": "1234567890", "sign": "fakesign"}, b""
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_no_headers_passes(self, channel: DingTalkChannel):
        """有 secret 但请求未携带签名头，视为无需验证。"""
        with patch.object(channel._settings, "dingtalk_app_secret", "my_secret"):
            result = await channel.verify_signature({}, b"")
        assert result is True

    @pytest.mark.asyncio
    async def test_valid_signature_passes(self, channel: DingTalkChannel):
        secret = "test_secret_key_123"
        timestamp, sign = _make_valid_signature(secret)
        with patch.object(channel._settings, "dingtalk_app_secret", secret):
            result = await channel.verify_signature(
                {"timestamp": timestamp, "sign": sign}, b""
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_invalid_signature_fails(self, channel: DingTalkChannel):
        secret = "test_secret_key_123"
        timestamp, _ = _make_valid_signature(secret)
        with patch.object(channel._settings, "dingtalk_app_secret", secret):
            result = await channel.verify_signature(
                {"timestamp": timestamp, "sign": "INVALID_SIGN"}, b""
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_wrong_secret_fails(self, channel: DingTalkChannel):
        """用 A secret 生成签名，但验证时用 B secret，应该失败。"""
        sign_secret = "correct_secret"
        verify_secret = "wrong_secret"
        timestamp, sign = _make_valid_signature(sign_secret)
        with patch.object(channel._settings, "dingtalk_app_secret", verify_secret):
            result = await channel.verify_signature(
                {"timestamp": timestamp, "sign": sign}, b""
            )
        assert result is False


# ── _build_webhook_url ────────────────────────────────────

class TestBuildWebhookUrl:
    def test_url_contains_access_token(self, channel: DingTalkChannel):
        with patch.object(channel._settings, "dingtalk_robot_code", "ROBOT_TOKEN_123"), \
             patch.object(channel._settings, "dingtalk_app_secret", "some_secret"):
            url = channel._build_webhook_url()
        assert "access_token=ROBOT_TOKEN_123" in url

    def test_url_contains_timestamp_and_sign(self, channel: DingTalkChannel):
        with patch.object(channel._settings, "dingtalk_robot_code", "TOKEN"), \
             patch.object(channel._settings, "dingtalk_app_secret", "secret"):
            url = channel._build_webhook_url()
        assert "timestamp=" in url
        assert "sign=" in url
