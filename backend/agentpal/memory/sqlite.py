"""SQLiteMemory — 基于 SQLAlchemy + aiosqlite 的持久化记忆。

职责：
- 消息的持久化写入与读取
- 支持按 session_id 隔离
- 支持关键词搜索（SQLite LIKE）
- 仅做存储，不含业务逻辑
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.memory.base import BaseMemory, MemoryMessage, MemoryRole
from agentpal.models.memory import MemoryRecord


class SQLiteMemory(BaseMemory):
    """SQLite 持久化记忆后端。

    Args:
        db:    由 FastAPI Depends 注入的 AsyncSession
        limit: 单次 get_recent 查询的最大条数上限（防止一次性加载过多）
    """

    def __init__(self, db: AsyncSession, limit: int = 200) -> None:
        self._db = db
        self._limit = limit

    # ── BaseMemory 实现 ───────────────────────────────────

    async def add(self, message: MemoryMessage) -> MemoryMessage:
        record = MemoryRecord(
            id=message.id or str(uuid.uuid4()),
            session_id=message.session_id,
            role=str(message.role),
            content=message.content,
            created_at=message.created_at or datetime.now(timezone.utc),
            meta=message.metadata,
        )
        self._db.add(record)
        await self._db.flush()       # 获取数据库分配的字段（如有）
        message.id = record.id
        return message

    async def get_recent(self, session_id: str, limit: int = 20) -> list[MemoryMessage]:
        effective_limit = min(limit, self._limit)
        stmt = (
            select(MemoryRecord)
            .where(MemoryRecord.session_id == session_id)
            .order_by(MemoryRecord.created_at.desc())
            .limit(effective_limit)
        )
        result = await self._db.execute(stmt)
        records = result.scalars().all()
        # 倒序后正序返回（最新在末尾）
        return [_record_to_msg(r) for r in reversed(records)]

    async def clear(self, session_id: str) -> None:
        stmt = delete(MemoryRecord).where(MemoryRecord.session_id == session_id)
        await self._db.execute(stmt)

    async def search(
        self,
        session_id: str,
        query: str,
        limit: int = 5,
    ) -> list[MemoryMessage]:
        """SQLite LIKE 关键词搜索（未来可替换为 FTS5 或向量检索）。"""
        stmt = (
            select(MemoryRecord)
            .where(
                MemoryRecord.session_id == session_id,
                MemoryRecord.content.like(f"%{query}%"),
            )
            .order_by(MemoryRecord.created_at.desc())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        records = result.scalars().all()
        return [_record_to_msg(r) for r in reversed(records)]

    async def count(self, session_id: str) -> int:
        stmt = select(func.count()).select_from(MemoryRecord).where(
            MemoryRecord.session_id == session_id
        )
        result = await self._db.execute(stmt)
        return result.scalar_one()


# ── 内部工具 ──────────────────────────────────────────────

def _record_to_msg(record: MemoryRecord) -> MemoryMessage:
    return MemoryMessage(
        id=record.id,
        session_id=record.session_id,
        role=MemoryRole(record.role) if record.role in MemoryRole._value2member_map_ else record.role,
        content=record.content,
        created_at=record.created_at,
        metadata=record.meta or {},
    )
