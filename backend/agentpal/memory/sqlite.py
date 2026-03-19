"""SQLiteMemory — 基于 SQLAlchemy + aiosqlite 的持久化记忆。

职责：
- 消息的持久化写入与读取
- 支持按 session_id 隔离
- 支持关键词搜索（SQLite LIKE）
- 支持跨 session 搜索（基于 MemoryScope 权限）
- 仅做存储，不含业务逻辑
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.memory.base import BaseMemory, MemoryMessage, MemoryRole, MemoryScope
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
            user_id=message.user_id,
            channel=message.channel,
            memory_type=message.memory_type or "conversation",
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

    async def cross_session_search(
        self,
        scope: MemoryScope,
        query: str,
        limit: int = 10,
    ) -> list[MemoryMessage]:
        """跨 session 关键词搜索，基于 MemoryScope 权限过滤。

        Args:
            scope:  查询权限范围（session_id / user_id / channel / global）
            query:  搜索关键词
            limit:  返回最大条数

        Returns:
            匹配的 MemoryMessage 列表（按时间升序）
        """
        scope.validate()

        # session 级别：回退到单 session search
        if scope.session_id:
            return await self.search(scope.session_id, query, limit)

        # 构建跨 session 查询
        conditions = [MemoryRecord.content.like(f"%{query}%")]

        if scope.user_id:
            conditions.append(MemoryRecord.user_id == scope.user_id)
        elif scope.channel:
            conditions.append(MemoryRecord.channel == scope.channel)
        # global_access: 不加额外过滤条件

        stmt = (
            select(MemoryRecord)
            .where(*conditions)
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

    async def mark_compressed(self, session_id: str, message_ids: list[str]) -> int:
        """批量标记消息为已压缩（在 meta JSON 中设置 compressed=true）。

        对每条消息的 meta 字段合并 {"compressed": true}。
        如果 meta 为 NULL，则设置为 {"compressed": true}。

        Returns:
            实际更新的行数
        """
        if not message_ids:
            return 0

        # 逐条更新 meta（SQLite JSON 函数兼容性最好的方式）
        count = 0
        for msg_id in message_ids:
            # 先读取当前 meta
            stmt = select(MemoryRecord).where(
                MemoryRecord.id == msg_id,
                MemoryRecord.session_id == session_id,
            )
            result = await self._db.execute(stmt)
            record = result.scalar_one_or_none()
            if record is None:
                continue
            meta = dict(record.meta) if record.meta else {}
            meta["compressed"] = True
            record.meta = meta
            count += 1

        if count > 0:
            await self._db.flush()
        return count


# ── 内部工具 ──────────────────────────────────────────────

def _record_to_msg(record: MemoryRecord) -> MemoryMessage:
    return MemoryMessage(
        id=record.id,
        session_id=record.session_id,
        role=MemoryRole(record.role) if record.role in MemoryRole._value2member_map_ else record.role,
        content=record.content,
        created_at=record.created_at,
        metadata=record.meta or {},
        user_id=record.user_id,
        channel=record.channel,
        memory_type=record.memory_type or "conversation",
    )
