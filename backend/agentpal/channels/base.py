"""渠道基础接口 — 所有渠道均实现此抽象。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class IncomingMessage:
    """渠道收到的标准化消息。"""

    channel: str          # "dingtalk" | "feishu" | "imessage" | "web"
    session_id: str       # 渠道级别的会话 ID（如 dingtalk:openConversationId）
    user_id: str          # 发送者 ID
    text: str             # 消息文本
    raw: dict[str, Any]   # 渠道原始 payload（保留以备用）


@dataclass
class OutgoingMessage:
    """待发送的标准化消息。"""

    session_id: str
    text: str
    metadata: dict[str, Any] | None = None


class BaseChannel(ABC):
    """消息渠道抽象接口。

    每个渠道负责：
    1. 验证 Webhook 签名
    2. 将渠道特定格式解析为 IncomingMessage
    3. 将回复文本发送回渠道
    """

    name: str = ""

    @abstractmethod
    async def parse_incoming(self, payload: dict[str, Any]) -> IncomingMessage | None:
        """将 Webhook payload 解析为 IncomingMessage（无法识别则返回 None）。"""

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> bool:
        """发送消息到渠道，成功返回 True。"""

    async def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        """验证 Webhook 签名（默认通过，子类按需覆盖）。"""
        return True
