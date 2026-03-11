"""MemoryFactory — 根据配置创建对应的记忆后端实例。

示例：
    # 在 FastAPI 依赖注入中使用
    async def get_memory(
        db: AsyncSession = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> BaseMemory:
        return MemoryFactory.create(settings.memory_backend, db=db)

扩展新后端：
    1. 实现 BaseMemory 子类（如 VectorMemory）
    2. 在 MemoryFactory._REGISTRY 中注册
    3. 在 Settings.memory_backend 的 Literal 中添加新名称
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.config import get_settings
from agentpal.memory.base import BaseMemory
from agentpal.memory.buffer import BufferMemory
from agentpal.memory.hybrid import HybridMemory
from agentpal.memory.sqlite import SQLiteMemory


class MemoryFactory:
    """工厂类，统一创建记忆后端。"""

    @staticmethod
    def create(backend: str | None = None, **kwargs: Any) -> BaseMemory:
        """创建记忆后端实例。

        Args:
            backend: "buffer" | "sqlite" | "hybrid"（None 时读取全局配置）
            **kwargs:
                db (AsyncSession): SQLite 后端必传
                buffer_size (int): BufferMemory 窗口大小，可选

        Returns:
            BaseMemory 实例
        """
        settings = get_settings()
        backend = backend or settings.memory_backend
        buffer_size: int = kwargs.get("buffer_size", settings.memory_buffer_size)
        sqlite_limit: int = kwargs.get("sqlite_limit", settings.memory_sqlite_limit)

        if backend == "buffer":
            return BufferMemory(max_size=buffer_size)

        db: AsyncSession | None = kwargs.get("db")

        if backend == "sqlite":
            if db is None:
                raise ValueError("SQLiteMemory 需要传入 db (AsyncSession)")
            return SQLiteMemory(db=db, limit=sqlite_limit)

        if backend == "hybrid":
            if db is None:
                raise ValueError("HybridMemory 需要传入 db (AsyncSession)")
            buffer = BufferMemory(max_size=buffer_size)
            persistent = SQLiteMemory(db=db, limit=sqlite_limit)
            return HybridMemory(buffer=buffer, persistent=persistent)

        raise ValueError(
            f"未知的 memory_backend: '{backend}'。"
            f"支持的后端：buffer, sqlite, hybrid"
        )
