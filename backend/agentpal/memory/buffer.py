"""BufferMemory — 纯内存滑动窗口实现。

适用场景：
- 无需持久化的临时会话
- SubAgent 短任务的上下文缓冲
- 与 SQLiteMemory 组合成 HybridMemory 时作为热缓存层
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone

from agentpal.memory.base import BaseMemory, MemoryMessage


class BufferMemory(BaseMemory):
    """基于 deque 的内存滑动窗口，线程安全（单进程 asyncio 环境）。

    Args:
        max_size: 每个 session 保留的最大消息条数，超出后丢弃最早的消息。
    """

    def __init__(self, max_size: int = 30) -> None:
        self._max_size = max_size
        # session_id -> deque[MemoryMessage]
        self._store: dict[str, deque[MemoryMessage]] = defaultdict(
            lambda: deque(maxlen=self._max_size)
        )

    # ── BaseMemory 实现 ───────────────────────────────────

    async def add(self, message: MemoryMessage) -> MemoryMessage:
        if message.id is None:
            message.id = str(uuid.uuid4())
        if message.created_at is None:
            message.created_at = datetime.now(timezone.utc)
        self._store[message.session_id].append(message)
        return message

    async def get_recent(self, session_id: str, limit: int = 20) -> list[MemoryMessage]:
        msgs = list(self._store.get(session_id, deque()))
        return msgs[-limit:]

    async def clear(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    async def count(self, session_id: str) -> int:
        return len(self._store.get(session_id, deque()))

    # ── 额外工具方法 ──────────────────────────────────────

    def load_from(self, session_id: str, messages: list[MemoryMessage]) -> None:
        """批量预加载消息（供 HybridMemory 初始化时使用）。"""
        q: deque[MemoryMessage] = deque(maxlen=self._max_size)
        # 只保留最近 max_size 条
        for msg in messages[-self._max_size :]:
            q.append(msg)
        self._store[session_id] = q
