"""iMessage 渠道实现（仅支持 macOS）。

实现方式：通过 osascript 发送消息，通过轮询 Messages.app 数据库读取消息。
注意：此渠道仅在 macOS 上可用，且需要完全磁盘访问权限。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Any

from agentpal.channels.base import BaseChannel, IncomingMessage, OutgoingMessage
from agentpal.config import get_settings


class IMessageChannel(BaseChannel):
    """iMessage 渠道（macOS 专属）。

    实现说明：
    - 发送：通过 osascript 调用 Messages.app 发送消息
    - 接收：通过轮询 ~/Library/Messages/chat.db 读取新消息
    - session_id 格式：imessage:<phone_or_email>
    """

    name = "imessage"

    def __init__(self) -> None:
        self._settings = get_settings()

    # ── BaseChannel 实现 ──────────────────────────────────

    async def parse_incoming(self, payload: dict[str, Any]) -> IncomingMessage | None:
        """将轮询到的 iMessage 记录解析为 IncomingMessage。

        payload 由 IMessagePoller 传入，格式：
        {
            "handle": "+8612345678",
            "text": "消息内容",
            "is_from_me": false,
            "rowid": 12345,
        }
        """
        try:
            if payload.get("is_from_me"):
                return None  # 跳过自己发送的消息

            handle = payload["handle"]
            text = payload.get("text", "").strip()
            if not text:
                return None

            return IncomingMessage(
                channel=self.name,
                session_id=f"imessage:{handle}",
                user_id=handle,
                text=text,
                raw=payload,
            )
        except (KeyError, TypeError):
            return None

    async def send(self, message: OutgoingMessage) -> bool:
        """通过 osascript 发送 iMessage。"""
        if sys.platform != "darwin":
            return False

        recipient = message.session_id.removeprefix("imessage:")
        # 转义引号防止注入
        safe_text = message.text.replace('"', '\\"')
        script = (
            f'tell application "Messages"\n'
            f'  set targetService to first service whose service type is iMessage\n'
            f'  set targetBuddy to buddy "{recipient}" of targetService\n'
            f'  send "{safe_text}" to targetBuddy\n'
            f'end tell'
        )
        result = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    async def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        """iMessage 为本地轮询，无需签名验证。"""
        return True
