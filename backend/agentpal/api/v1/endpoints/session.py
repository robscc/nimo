"""Session 管理 API。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.config import get_settings
from agentpal.database import get_db
from agentpal.memory.factory import MemoryFactory
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
    message_count: int
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: str


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
    session_id = f"{channel}:{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    session = SessionRecord(
        id=session_id,
        channel=channel,
        status=SessionStatus.ACTIVE,
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
        MessageOut(role=r.role, content=r.content, created_at=r.created_at.isoformat())
        for r in records
    ]


@router.delete("/{session_id}/memory")
async def clear_session_memory(session_id: str, db: AsyncSession = Depends(get_db)):
    """清除指定 session 的全部记忆。"""
    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)
    await memory.clear(session_id)
    return {"status": "cleared", "session_id": session_id}
