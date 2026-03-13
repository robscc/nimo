"""Session 管理 API。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.config import get_settings
from agentpal.database import get_db
from agentpal.memory.factory import MemoryFactory
from agentpal.models.llm_usage import LLMCallLog
from agentpal.models.memory import MemoryRecord
from agentpal.models.session import SessionRecord, SessionStatus

router = APIRouter()


# ── Response Models ────────────────────────────────────────


class SessionResponse(BaseModel):
    id: str
    channel: str
    user_id: str | None
    status: str


class SessionSummary(BaseModel):
    id: str
    title: str
    channel: str
    model_name: str | None
    message_count: int
    created_at: str
    updated_at: str


class SessionMeta(BaseModel):
    """Session 元信息，包含模型、上下文大小、工具/技能列表。"""
    id: str
    channel: str
    model_name: str | None
    context_tokens: int | None
    enabled_tools: list[str] | None  # null = 跟随全局
    enabled_skills: list[str] | None  # null = 跟随全局
    message_count: int
    created_at: str
    updated_at: str


class SessionConfigUpdate(BaseModel):
    """Session 级工具/技能配置更新请求。"""
    enabled_tools: list[str] | None = None  # null = 跟随全局
    enabled_skills: list[str] | None = None  # null = 跟随全局
    model_name: str | None = None


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: str
    meta: dict | None = None


class CreateSessionResponse(BaseModel):
    id: str


# ── Endpoints ─────────────────────────────────────────────


@router.get("", response_model=list[SessionSummary])
async def list_sessions(
    channel: str = "web",
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """列出指定 channel 的 active session 列表，按 updated_at 倒序。"""
    # 1. 查询 sessions
    result = await db.execute(
        select(SessionRecord)
        .where(SessionRecord.channel == channel, SessionRecord.status == SessionStatus.ACTIVE)
        .order_by(SessionRecord.updated_at.desc())
        .limit(limit)
    )
    sessions = result.scalars().all()

    if not sessions:
        return []

    session_ids = [s.id for s in sessions]

    # 2. 批量查 message_count（避免 N+1）
    count_result = await db.execute(
        select(MemoryRecord.session_id, func.count().label("cnt"))
        .where(MemoryRecord.session_id.in_(session_ids))
        .group_by(MemoryRecord.session_id)
    )
    count_map: dict[str, int] = {row.session_id: row.cnt for row in count_result}

    # 3. 批量查每个 session 的第一条 user 消息（用于生成 title）
    first_msg_result = await db.execute(
        select(MemoryRecord)
        .where(MemoryRecord.session_id.in_(session_ids), MemoryRecord.role == "user")
        .order_by(MemoryRecord.session_id, MemoryRecord.created_at.asc())
    )
    # Python 侧取每个 session 第一条
    first_msg_map: dict[str, str] = {}
    for rec in first_msg_result.scalars().all():
        if rec.session_id not in first_msg_map:
            first_msg_map[rec.session_id] = rec.content

    summaries = []
    for s in sessions:
        raw_title = first_msg_map.get(s.id, "")
        title = raw_title[:30] + ("…" if len(raw_title) > 30 else "") if raw_title else "新对话"
        summaries.append(
            SessionSummary(
                id=s.id,
                title=title,
                channel=s.channel,
                model_name=s.model_name,
                message_count=count_map.get(s.id, 0),
                created_at=s.created_at.isoformat(),
                updated_at=s.updated_at.isoformat(),
            )
        )
    return summaries


@router.post("", response_model=CreateSessionResponse, status_code=201)
async def create_session(
    channel: str = "web",
    db: AsyncSession = Depends(get_db),
):
    """创建新 session，返回 id。"""
    settings = get_settings()
    session_id = f"{channel}:{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    session = SessionRecord(
        id=session_id,
        channel=channel,
        status=SessionStatus.ACTIVE,
        model_name=settings.llm_model,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    await db.commit()
    return CreateSessionResponse(id=session_id)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SessionRecord).where(SessionRecord.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse(
        id=session.id,
        channel=session.channel,
        user_id=session.user_id,
        status=session.status,
    )


@router.get("/{session_id}/meta", response_model=SessionMeta)
async def get_session_meta(session_id: str, db: AsyncSession = Depends(get_db)):
    """获取 session 元信息（模型、上下文大小、工具/技能列表）。"""
    result = await db.execute(select(SessionRecord).where(SessionRecord.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 统计消息数
    count_result = await db.execute(
        select(func.count()).where(MemoryRecord.session_id == session_id)
    )
    message_count = count_result.scalar() or 0

    return SessionMeta(
        id=session.id,
        channel=session.channel,
        model_name=session.model_name,
        context_tokens=session.context_tokens,
        enabled_tools=session.enabled_tools,
        enabled_skills=session.enabled_skills,
        message_count=message_count,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
    )


@router.patch("/{session_id}/config", response_model=SessionMeta)
async def update_session_config(
    session_id: str,
    req: SessionConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新 session 级工具/技能配置。

    规则：
    - enabled_tools/enabled_skills 为 null → 跟随全局配置
    - 为非空列表 → session 使用指定工具，但范围不能超过全局已启用工具
    - 如果 session 配置了全局没有启用的工具/技能，当全局启用后会自动在 session 生效
    """
    result = await db.execute(select(SessionRecord).where(SessionRecord.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if req.enabled_tools is not None:
        session.enabled_tools = req.enabled_tools
    if req.enabled_skills is not None:
        session.enabled_skills = req.enabled_skills
    if req.model_name is not None:
        session.model_name = req.model_name

    session.updated_at = datetime.now(timezone.utc)
    await db.flush()

    # 返回更新后的 meta
    count_result = await db.execute(
        select(func.count()).where(MemoryRecord.session_id == session_id)
    )
    message_count = count_result.scalar() or 0

    return SessionMeta(
        id=session.id,
        channel=session.channel,
        model_name=session.model_name,
        context_tokens=session.context_tokens,
        enabled_tools=session.enabled_tools,
        enabled_skills=session.enabled_skills,
        message_count=message_count,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
    )


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """将 session 标记为 archived（软删除），同时清除记忆记录。"""
    result = await db.execute(select(SessionRecord).where(SessionRecord.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.status = SessionStatus.ARCHIVED
    # 同时清除记忆，避免孤儿记录
    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)
    await memory.clear(session_id)
    await db.commit()


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def get_session_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """加载指定 session 的历史消息（user/assistant 角色）。"""
    result = await db.execute(
        select(MemoryRecord)
        .where(
            MemoryRecord.session_id == session_id,
            MemoryRecord.role.in_(["user", "assistant"]),
        )
        .order_by(MemoryRecord.created_at.asc())
    )
    records = result.scalars().all()
    return [
        MessageOut(role=r.role, content=r.content, created_at=r.created_at.isoformat(), meta=r.meta or None)
        for r in records
    ]


@router.delete("/{session_id}/memory")
async def clear_session_memory(session_id: str, db: AsyncSession = Depends(get_db)):
    """清除指定 session 的全部记忆。"""
    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)
    await memory.clear(session_id)
    return {"status": "cleared", "session_id": session_id}


class LLMCallItem(BaseModel):
    id: int
    model_name: str
    provider: str
    call_round: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    created_at: str


class SessionUsageResponse(BaseModel):
    session_id: str
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    llm_calls: int
    calls: list[LLMCallItem]


@router.get("/{session_id}/usage", response_model=SessionUsageResponse)
async def get_session_usage(session_id: str, db: AsyncSession = Depends(get_db)):
    """获取指定 session 的 LLM token 用量明细。"""
    result = await db.execute(
        select(LLMCallLog)
        .where(LLMCallLog.session_id == session_id)
        .order_by(LLMCallLog.created_at.asc())
    )
    logs = result.scalars().all()

    total_input = sum(r.input_tokens for r in logs)
    total_output = sum(r.output_tokens for r in logs)

    return SessionUsageResponse(
        session_id=session_id,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_tokens=total_input + total_output,
        llm_calls=len(logs),
        calls=[
            LLMCallItem(
                id=r.id,
                model_name=r.model_name,
                provider=r.provider,
                call_round=r.call_round,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                total_tokens=r.total_tokens,
                created_at=r.created_at.isoformat(),
            )
            for r in logs
        ],
    )
