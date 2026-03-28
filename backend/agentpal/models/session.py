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


class AgentMode(StrEnum):
    """PA Agent 运行模式。"""
    NORMAL = "normal"
    PLANNING = "planning"
    CONFIRMING = "confirming"
    EXECUTING = "executing"
    STEP_CONFIRM = "step_confirm"  # Phase 2


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INPUT_REQUIRED = "input-required"  # 需要用户提供输入
    PAUSED = "paused"  # 暂停状态


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

    # ── Plan Mode ─────────────────────────────────────
    agent_mode: Mapped[str] = mapped_column(
        String(32), default="normal", server_default="normal"
    )

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
    - input_prompt:   请求用户输入时的提示语（INPUT_REQUIRED 状态时使用）
    - input_response: 用户提供的输入内容
    - progress_pct:   进度百分比（0-100）
    - progress_message: 进度描述信息
    - started_at:     实际开始执行时间
    - completed_at:   执行完成时间（无论成功失败）
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

    # ── Input-Required 协议 ───────────────────────────────
    input_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── 进度跟踪 ──────────────────────────────────────────
    progress_pct: Mapped[int | None] = mapped_column(Integer, nullable=True, server_default="0")
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── 时间戳 ────────────────────────────────────────────
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_task_parent_status", "parent_session_id", "status"),
        Index("ix_task_priority", "status", "priority"),
    )


class TaskArtifact(Base):
    """SubAgent 任务产出物。

    SubAgent 在执行过程中可以产生多个中间产物或最终产物，
    例如：生成的代码文件、分析报告、图表等。

    字段说明：
    - task_id:        关联的任务 ID
    - name:           产出物名称（例如："analysis_report.md"）
    - artifact_type:  产出物类型：file/text/image/data
    - content:        文本内容（text 类型时使用）
    - file_path:      文件路径（file 类型时使用）
    - mime_type:      MIME 类型（image/png, text/markdown 等）
    - size_bytes:     文件大小（字节）
    - extra:          额外元数据
    """

    __tablename__ = "task_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)  # file/text/image/data
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_artifact_task", "task_id", "created_at"),
    )


class TaskEvent(Base):
    """SubAgent 任务事件日志。

    记录 SubAgent 执行过程中的关键事件，用于：
    - 实时进度推送（SSE）
    - 调试和审计
    - 前端时间线展示

    事件类型：
    - task.started: 任务开始执行
    - task.progress: 进度更新
    - task.input_required: 请求用户输入
    - task.artifact_created: 产出物生成
    - task.completed: 任务完成
    - task.failed: 任务失败
    - task.cancelled: 任务取消
    - tool.start: 工具调用开始
    - tool.complete: 工具调用完成
    - llm.message: LLM 消息
    """

    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    __table_args__ = (
        Index("ix_event_task_created", "task_id", "created_at"),
    )
