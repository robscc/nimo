"""HybridMemory — 热缓冲 + SQLite 持久化的组合记忆。

工作原理：
┌──────────────────────────────────────────────┐
│               HybridMemory                   │
│                                              │
│  写入 add()  ──► BufferMemory (热缓存)        │
│              └─► SQLiteMemory (持久化)        │
│                                              │
│  读取 get_recent()                            │
│    ├─ Buffer 有足够数据 → 直接返回              │
│    └─ Buffer 不足      → 从 SQLite 补全        │
│                                              │
│  搜索 search()  → SQLite LIKE / FTS           │
└──────────────────────────────────────────────┘

Session 首次访问时，会从 SQLite 预热 BufferMemory，
后续读写优先命中内存，大幅减少磁盘 I/O。
"""

from __future__ import annotations

from agentpal.memory.base import BaseMemory, MemoryMessage
from agentpal.memory.buffer import BufferMemory
from agentpal.memory.sqlite import SQLiteMemory


class HybridMemory(BaseMemory):
    """默认推荐的记忆后端，兼顾性能与持久化。

    Args:
        buffer:     BufferMemory 实例（热缓存层）
        persistent: SQLiteMemory 实例（持久化层）
    """

    def __init__(self, buffer: BufferMemory, persistent: SQLiteMemory) -> None:
        self._buffer = buffer
        self._persistent = persistent
        # 记录已预热的 session，避免重复查询 SQLite
        self._warmed_sessions: set[str] = set()

    # ── BaseMemory 实现 ───────────────────────────────────

    async def add(self, message: MemoryMessage) -> MemoryMessage:
        # 先写 SQLite 获取 id，再同步到 Buffer
        message = await self._persistent.add(message)
        await self._buffer.add(message)
        return message

    async def get_recent(self, session_id: str, limit: int = 20) -> list[MemoryMessage]:
        await self._maybe_warm(session_id)

        buf_msgs = await self._buffer.get_recent(session_id, limit=limit)

        # Buffer 已有足够数量，直接返回
        if len(buf_msgs) >= limit:
            return buf_msgs

        # Buffer 不足时从 SQLite 拉取更多历史
        db_msgs = await self._persistent.get_recent(session_id, limit=limit)
        # 去重：Buffer 中已有的 id
        buf_ids = {m.id for m in buf_msgs if m.id}
        extra = [m for m in db_msgs if m.id not in buf_ids]
        merged = extra + buf_msgs
        return merged[-limit:]

    async def clear(self, session_id: str) -> None:
        await self._buffer.clear(session_id)
        await self._persistent.clear(session_id)
        self._warmed_sessions.discard(session_id)

    async def search(
        self,
        session_id: str,
        query: str,
        limit: int = 5,
    ) -> list[MemoryMessage]:
        # 全文检索走 SQLite（未来可替换为 FTS5 / 向量检索）
        return await self._persistent.search(session_id, query, limit)

    async def count(self, session_id: str) -> int:
        return await self._persistent.count(session_id)

    # ── 内部工具 ──────────────────────────────────────────

    async def _maybe_warm(self, session_id: str) -> None:
        """首次访问时从 SQLite 预热 Buffer。"""
        if session_id in self._warmed_sessions:
            return
        recent = await self._persistent.get_recent(
            session_id, limit=self._buffer._max_size
        )
        self._buffer.load_from(session_id, recent)
        self._warmed_sessions.add(session_id)
