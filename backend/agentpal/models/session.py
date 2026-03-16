"""SQLAlchemy ORM 模型：Session 与 SubAgentTask。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from agentpal.database import Base


class SessionStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SessionRecord(Base):
    """对话 Session 记录。

    一个 session 对应一个独立的对话上下文，
    PersonalAssistant 和每个 SubAgent 各自维护独立的 session_id。

    新增字段：
    - model_name:      该 session 使用的 LLM 模型
    - enabled_tools:   该 session 启用的工具列表（null 表示跟随全局）
    - enabled_skills:  该 session 启用的技能列表（null 表示跟随全局）
    - context_tokens:  当前上下文总 token 估算
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)  # dingtalk/feishu/imessage/web
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=SessionStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # ── Session 级配置（Todo 3）─────────────────────────
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled_tools: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    enabled_skills: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    context_tokens: Mapped[int | None] = mapped_column(nullable=True)
    tool_guard_threshold: Mapped[int | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_session_channel_user", "channel", "user_id"),
    )


class SubAgentTask(Base):
    """SubAgent 异步任务记录。

    每次 PersonalAssistant 派遣 SubAgent 时创建一条记录，
    异步任务完成后更新 status 和 result。

    新增字段：
    - agent_name:     执行此任务的 SubAgent 角色名
    - task_type:      任务类型（用于 SubAgent 角色路由）
    - execution_log:  完整执行日志（LLM 对话 + 工具调用）
    """

    __tablename__ = "sub_agent_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    parent_session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sub_session_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    task_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.PENDING)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # ── 优先级 & 重试 ──────────────────────────────────────
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False, server_default="5")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False, server_default="3")

    __table_args__ = (
        Index("ix_task_parent_status", "parent_session_id", "status"),
        Index("ix_task_priority", "status", "priority"),
    )
