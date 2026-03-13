"""AgentMessage 模型 — SubAgent 间通信消息。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from agentpal.database import Base


class MessageStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    PROCESSED = "processed"


class MessageType(StrEnum):
    REQUEST = "request"        # 请求协作
    RESPONSE = "response"      # 协作回复
    NOTIFY = "notify"          # 单向通知（如 cron 结果通知主 Agent）
    BROADCAST = "broadcast"    # 广播给所有 Agent


class AgentMessage(Base):
    """Agent 间通信消息。

    用于 SubAgent 之间以及 SubAgent 与主 Agent 之间的异步通信。

    Attributes:
        id:              UUID 主键
        from_agent:      发送方 Agent 名称（"main" 表示主 Agent）
        to_agent:        接收方 Agent 名称（"main" 表示主 Agent）
        parent_session_id: 所属的主会话 ID
        message_type:    消息类型（request/response/notify/broadcast）
        content:         消息正文
        extra:           附加元数据（如关联的 task_id）
        status:          消息状态
        in_reply_to:     回复的消息 ID（可选）
    """

    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    from_agent: Mapped[str] = mapped_column(String(64), nullable=False)
    to_agent: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False, default=MessageType.NOTIFY)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=MessageStatus.PENDING)
    in_reply_to: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_agent_msg_to_status", "to_agent", "status"),
        Index("ix_agent_msg_session", "parent_session_id", "created_at"),
    )
