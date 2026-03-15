"""Cron 定时任务模型 — 任务定义 + 执行日志。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from agentpal.database import Base


class CronStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class CronJob(Base):
    """定时任务定义。

    Attributes:
        id:            UUID 主键
        name:          任务名称（用户可读）
        schedule:      cron 表达式（如 "0 9 * * *" 表示每天 9:00）
        task_prompt:   交给 SubAgent 的任务提示词
        agent_name:    指定执行的 SubAgent（null 则自动匹配或用默认）
        enabled:       是否启用
        last_run_at:   上次执行时间
        next_run_at:   下次执行时间（由调度器计算）
        notify_main:   执行完成后是否通知主 Agent
    """

    __tablename__ = "cron_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    schedule: Mapped[str] = mapped_column(String(128), nullable=False)
    task_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notify_main: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 指定接收通知的 session（null = 走 MessageBus 通知主 Agent）
    target_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class CronJobExecution(Base):
    """定时任务执行记录 — 包含完整对话和工具调用日志。

    Attributes:
        id:             UUID 主键
        cron_job_id:    所属 CronJob ID
        cron_job_name:  任务名称快照（方便查询时不 JOIN）
        status:         执行状态
        started_at:     开始时间
        finished_at:    结束时间
        result:         最终结果文本
        error:          错误信息
        execution_log:  完整执行日志（LLM 对话 + 工具调用）
        agent_name:     实际执行的 SubAgent 名称
    """

    __tablename__ = "cron_job_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cron_job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    cron_job_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), default=CronStatus.PENDING)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    agent_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_cron_exec_job_started", "cron_job_id", "started_at"),
    )
