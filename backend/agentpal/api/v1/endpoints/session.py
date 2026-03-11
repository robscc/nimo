"""Session 管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.database import get_db
from agentpal.memory.factory import MemoryFactory
from agentpal.models.session import SessionRecord, SessionStatus
from agentpal.config import get_settings

router = APIRouter()


class SessionResponse(BaseModel):
    id: str
    channel: str
    user_id: str | None
    status: str


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


@router.delete("/{session_id}/memory")
async def clear_session_memory(session_id: str, db: AsyncSession = Depends(get_db)):
    """清除指定 session 的全部记忆。"""
    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)
    await memory.clear(session_id)
    return {"status": "cleared", "session_id": session_id}
