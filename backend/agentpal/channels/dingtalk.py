"""DingTalk 渠道实现。

参考文档：https://open.dingtalk.com/document/orgapp/receive-message
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import httpx

from agentpal.channels.base import BaseChannel, IncomingMessage, OutgoingMessage
from agentpal.config import get_settings


class DingTalkChannel(BaseChannel):
    """钉钉消息渠道。

    支持：
    - 企业内部机器人 Webhook（outgoing robot）
    - Stream 模式监听（后续扩展）
    """

    name = "dingtalk"

    def __init__(self) -> None:
        self._settings = get_settings()

    # ── BaseChannel 实现 ──────────────────────────────────

    async def parse_incoming(self, payload: dict[str, Any]) -> IncomingMessage | None:
        """解析钉钉 Webhook 消息。"""
        try:
            msg_type = payload.get("msgtype", "")
            if msg_type != "text":
                return None  # 目前仅处理文本消息

            text = payload["text"]["content"].strip()
            sender_id = payload.get("senderStaffId", "") or payload.get("senderId", "")
            conversation_id = payload.get("conversationId", sender_id)

            return IncomingMessage(
                channel=self.name,
                session_id=f"dingtalk:{conversation_id}",
                user_id=sender_id,
                text=text,
                raw=payload,
            )
        except (KeyError, TypeError):
            return None

    async def send(self, message: OutgoingMessage) -> bool:
        """通过钉钉机器人 Webhook 发送文本消息。"""
        webhook_url = self._build_webhook_url()
        body = {
            "msgtype": "text",
            "text": {"content": message.text},
            "at": {"isAtAll": False},
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=body)
            result = resp.json()
            return result.get("errcode", -1) == 0

    async def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        """验证钉钉 Webhook 签名（timestamp + sign）。"""
        timestamp = headers.get("timestamp", "")
        sign = headers.get("sign", "")
        if not timestamp or not sign:
            return True  # 未配置签名时跳过验证

        secret = self._settings.dingtalk_app_secret
        string_to_sign = f"{timestamp}\n{secret}"
        import base64
        import urllib.parse

        hmac_code = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        expected = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return sign == expected

    # ── 内部工具 ──────────────────────────────────────────

    def _build_webhook_url(self) -> str:
        """构建带签名的 Webhook 地址。"""
        timestamp = str(round(time.time() * 1000))
        secret = self._settings.dingtalk_app_secret
        string_to_sign = f"{timestamp}\n{secret}"
        import base64
        import hmac as _hmac
        import urllib.parse

        hmac_code = _hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        base = f"https://oapi.dingtalk.com/robot/send?access_token={self._settings.dingtalk_robot_code}"
        return f"{base}&timestamp={timestamp}&sign={sign}"
