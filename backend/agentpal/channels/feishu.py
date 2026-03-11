"""Feishu / Lark 渠道实现。

参考文档：https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1
"""

from __future__ import annotations

import hashlib
import hmac
import json
from base64 import b64decode, b64encode
from typing import Any

import httpx

from agentpal.channels.base import BaseChannel, IncomingMessage, OutgoingMessage
from agentpal.config import get_settings


class FeishuChannel(BaseChannel):
    """飞书消息渠道。

    支持：
    - 接收飞书事件订阅（消息事件）
    - 发送文本消息（im.v1）
    - 签名验证（encrypt_key）
    """

    name = "feishu"
    _TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    _SEND_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._tenant_token: str | None = None

    # ── BaseChannel 实现 ──────────────────────────────────

    async def parse_incoming(self, payload: dict[str, Any]) -> IncomingMessage | None:
        """解析飞书事件订阅消息。"""
        try:
            # 处理 URL 验证挑战
            if "challenge" in payload:
                return None

            event_type = payload.get("header", {}).get("event_type", "")
            if event_type != "im.message.receive_v1":
                return None

            event = payload["event"]
            msg = event["message"]
            if msg.get("message_type") != "text":
                return None

            content = json.loads(msg["content"])
            text = content.get("text", "").strip()
            sender_id = event["sender"]["sender_id"]["open_id"]
            chat_id = msg.get("chat_id", sender_id)

            return IncomingMessage(
                channel=self.name,
                session_id=f"feishu:{chat_id}",
                user_id=sender_id,
                text=text,
                raw=payload,
            )
        except (KeyError, TypeError, json.JSONDecodeError):
            return None

    async def send(self, message: OutgoingMessage) -> bool:
        """发送文本消息到飞书会话。"""
        token = await self._get_tenant_token()
        # session_id 格式: feishu:<chat_id>
        chat_id = message.session_id.removeprefix("feishu:")

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": message.text}),
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._SEND_URL}?receive_id_type=chat_id",
                headers=headers,
                json=body,
            )
            result = resp.json()
            return result.get("code", -1) == 0

    async def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        """验证飞书 Webhook 签名。"""
        encrypt_key = self._settings.feishu_encrypt_key
        if not encrypt_key:
            return True
        timestamp = headers.get("x-lark-request-timestamp", "")
        nonce = headers.get("x-lark-request-nonce", "")
        signature = headers.get("x-lark-signature", "")
        content = timestamp + nonce + encrypt_key + body.decode("utf-8")
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return signature == expected

    # ── 内部工具 ──────────────────────────────────────────

    async def _get_tenant_token(self) -> str:
        """获取飞书 tenant_access_token（简化版，不做缓存刷新）。"""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                self._TOKEN_URL,
                json={
                    "app_id": self._settings.feishu_app_id,
                    "app_secret": self._settings.feishu_app_secret,
                },
            )
            return resp.json()["tenant_access_token"]
