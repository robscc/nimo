"""SQLAlchemy ORM 模型：MemoryRecord（消息持久化表）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from agentpal.database import Base


class MemoryRecord(Base):
    """消息记忆持久化记录。

    字段设计说明：
    - id:          UUID 字符串，由应用层生成，避免自增 ID 暴露信息
    - session_id:  会话隔离键（可以是用户 ID、SubAgent ID 等）
    - role:        消息角色（system/user/assistant/tool）
    - content:     消息文本（Text 类型，无长度限制）
    - created_at:  写入时间戳（UTC），用于排序和 TTL 清理
    - meta:        JSON 扩展字段（渠道来源、工具调用元数据等）
    - user_id:     所属用户 ID（跨 session 查询需要）
    - channel:     所属渠道（web/dingtalk/feishu/imessage）
    - memory_type: 记忆分类（conversation/personal/task/tool）
    """

    __tablename__ = "memory_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # ── 跨 session 查询字段（v0.3 新增）───────────────────
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    memory_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="conversation", server_default="conversation"
    )

    # 复合索引：按 session + 时间排序的查询最常见
    __table_args__ = (
        Index("ix_memory_session_time", "session_id", "created_at"),
        Index("ix_memory_user_time", "user_id", "created_at"),
        Index("ix_memory_channel_time", "channel", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<MemoryRecord id={self.id!r} session={self.session_id!r} role={self.role!r}>"
