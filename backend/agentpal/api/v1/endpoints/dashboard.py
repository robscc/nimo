"""Dashboard 统计 API — 聚合展示系统运行指标。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.database import get_db
from agentpal.models.cron import CronJob, CronJobExecution
from agentpal.models.memory import MemoryRecord
from agentpal.models.session import SessionRecord, SubAgentTask
from agentpal.models.skill import SkillRecord
from agentpal.models.tool import ToolCallLog

router = APIRouter()


class DashboardStats(BaseModel):
    total_sessions: int = 0
    sessions_by_channel: dict[str, int] = {}
    total_messages: int = 0
    total_tokens: int = 0
    models_in_use: dict[str, int] = {}
    total_tool_calls: int = 0
    tool_calls_by_name: dict[str, int] = {}
    tool_errors: int = 0
    avg_tool_duration_ms: float = 0.0
    total_skills: int = 0
    enabled_skills: int = 0
    total_cron_jobs: int = 0
    enabled_cron_jobs: int = 0
    cron_executions: int = 0
    cron_failures: int = 0
    total_errors: int = 0
    sub_agent_tasks: int = 0
    sub_agent_failures: int = 0


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)) -> DashboardStats:
    """聚合统计系统核心指标。"""

    # ── Sessions ────────────────────────────────────────────
    session_total = await db.scalar(
        select(func.count()).select_from(SessionRecord)
    ) or 0

    # 按 channel 分组
    channel_rows = (
        await db.execute(
            select(SessionRecord.channel, func.count())
            .group_by(SessionRecord.channel)
        )
    ).all()
    sessions_by_channel = {row[0]: row[1] for row in channel_rows}

    # 按 model_name 分组
    model_rows = (
        await db.execute(
            select(SessionRecord.model_name, func.count())
            .where(SessionRecord.model_name.isnot(None))
            .group_by(SessionRecord.model_name)
        )
    ).all()
    models_in_use = {row[0]: row[1] for row in model_rows}

    # context_tokens 总和
    total_tokens = await db.scalar(
        select(func.coalesce(func.sum(SessionRecord.context_tokens), 0))
    ) or 0

    # ── Messages ────────────────────────────────────────────
    total_messages = await db.scalar(
        select(func.count()).select_from(MemoryRecord)
    ) or 0

    # ── Tool calls ──────────────────────────────────────────
    total_tool_calls = await db.scalar(
        select(func.count()).select_from(ToolCallLog)
    ) or 0

    # 按 tool_name 分组 Top 10
    tool_name_rows = (
        await db.execute(
            select(ToolCallLog.tool_name, func.count().label("cnt"))
            .group_by(ToolCallLog.tool_name)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()
    tool_calls_by_name = {row[0]: row[1] for row in tool_name_rows}

    # 错误数
    tool_errors = await db.scalar(
        select(func.count())
        .select_from(ToolCallLog)
        .where(ToolCallLog.error.isnot(None))
    ) or 0

    # 平均耗时
    avg_tool_duration = await db.scalar(
        select(func.coalesce(func.avg(ToolCallLog.duration_ms), 0.0))
    ) or 0.0

    # ── Skills ──────────────────────────────────────────────
    total_skills = await db.scalar(
        select(func.count()).select_from(SkillRecord)
    ) or 0

    enabled_skills = await db.scalar(
        select(func.count())
        .select_from(SkillRecord)
        .where(SkillRecord.enabled.is_(True))
    ) or 0

    # ── Cron ────────────────────────────────────────────────
    total_cron_jobs = await db.scalar(
        select(func.count()).select_from(CronJob)
    ) or 0

    enabled_cron_jobs = await db.scalar(
        select(func.count())
        .select_from(CronJob)
        .where(CronJob.enabled.is_(True))
    ) or 0

    cron_executions = await db.scalar(
        select(func.count()).select_from(CronJobExecution)
    ) or 0

    cron_failures = await db.scalar(
        select(func.count())
        .select_from(CronJobExecution)
        .where(CronJobExecution.status == "failed")
    ) or 0

    # ── SubAgent tasks ──────────────────────────────────────
    sub_agent_tasks = await db.scalar(
        select(func.count()).select_from(SubAgentTask)
    ) or 0

    sub_agent_failures = await db.scalar(
        select(func.count())
        .select_from(SubAgentTask)
        .where(SubAgentTask.status == "failed")
    ) or 0

    # ── Total errors ────────────────────────────────────────
    total_errors = tool_errors + cron_failures + sub_agent_failures

    return DashboardStats(
        total_sessions=session_total,
        sessions_by_channel=sessions_by_channel,
        total_messages=total_messages,
        total_tokens=total_tokens,
        models_in_use=models_in_use,
        total_tool_calls=total_tool_calls,
        tool_calls_by_name=tool_calls_by_name,
        tool_errors=tool_errors,
        avg_tool_duration_ms=round(float(avg_tool_duration), 1),
        total_skills=total_skills,
        enabled_skills=enabled_skills,
        total_cron_jobs=total_cron_jobs,
        enabled_cron_jobs=enabled_cron_jobs,
        cron_executions=cron_executions,
        cron_failures=cron_failures,
        total_errors=total_errors,
        sub_agent_tasks=sub_agent_tasks,
        sub_agent_failures=sub_agent_failures,
    )
