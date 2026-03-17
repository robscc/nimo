"""Memory 模块公共接口。

使用方式：
    from agentpal.memory import MemoryFactory, BaseMemory, MemoryMessage

    memory = MemoryFactory.create("hybrid", db=db_session)
    await memory.add(MemoryMessage(role="user", content="你好", session_id="s1"))
    msgs = await memory.get_recent("s1", limit=10)

跨 session 搜索：
    from agentpal.memory import MemoryScope

    scope = MemoryScope(user_id="user-123")
    results = await memory.cross_session_search(scope, "天气", limit=5)
"""

from agentpal.memory.base import (
    BaseMemory,
    MemoryAccessLevel,
    MemoryMessage,
    MemoryRole,
    MemoryScope,
)
from agentpal.memory.buffer import BufferMemory
from agentpal.memory.factory import MemoryFactory
from agentpal.memory.hybrid import HybridMemory
from agentpal.memory.sqlite import SQLiteMemory

__all__ = [
    "BaseMemory",
    "MemoryAccessLevel",
    "MemoryMessage",
    "MemoryRole",
    "MemoryScope",
    "BufferMemory",
    "SQLiteMemory",
    "HybridMemory",
    "MemoryFactory",
]
