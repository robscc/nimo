"""MemoryFactory — 根据配置创建对应的记忆后端实例。

示例：
    # 在 FastAPI 依赖注入中使用
    async def get_memory(
        db: AsyncSession = Depends(get_db),
        settings: Settings = Depends(get_settings),
    ) -> BaseMemory:
        return MemoryFactory.create(settings.memory_backend, db=db)

扩展新后端：
    1. 实现 BaseMemory 子类（如 Mem0Memory）
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
            backend: "buffer" | "sqlite" | "hybrid" | "mem0" | "reme"（None 时读取全局配置）
            **kwargs:
                db (AsyncSession): SQLite/hybrid 后端必传
                buffer_size (int): BufferMemory 窗口大小，可选
                mem0_config (dict): mem0 配置，可选
                mem0_infer (bool): mem0 是否启用 LLM 事实提取，可选
                reme_server_url (str): ReMe 服务端 URL，可选
                reme_agent_name (str): ReMe Agent 名称，可选
                reme_model_config (dict): ReMe LLM 配置，可选
                reme_embedding_config (dict): ReMe Embedding 配置，可选

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

        if backend == "mem0":
            return _create_mem0(settings, **kwargs)

        if backend == "reme":
            return _create_reme(settings, **kwargs)

        raise ValueError(
            f"未知的 memory_backend: '{backend}'。"
            f"支持的后端：buffer, sqlite, hybrid, mem0, reme"
        )


def _create_mem0(settings: Any, **kwargs: Any) -> BaseMemory:
    """创建 mem0 记忆后端。"""
    from agentpal.memory.mem0_adapter import Mem0Memory

    mem0_config = kwargs.get("mem0_config") or getattr(settings, "memory_mem0_config", None)
    mem0_infer = kwargs.get("mem0_infer", getattr(settings, "memory_mem0_infer", False))
    return Mem0Memory(mem0_config=mem0_config, infer=mem0_infer)


def _create_reme(settings: Any, **kwargs: Any) -> BaseMemory:
    """创建 ReMe 记忆后端。"""
    from agentpal.memory.reme_adapter import ReMeMemory

    server_url = kwargs.get("reme_server_url") or getattr(settings, "memory_reme_server_url", None)
    agent_name = kwargs.get("reme_agent_name") or getattr(settings, "memory_reme_agent_name", "AgentPal")
    model_config = kwargs.get("reme_model_config") or getattr(settings, "memory_reme_model_config", None)
    embedding_config = kwargs.get("reme_embedding_config") or getattr(
        settings, "memory_reme_embedding_config", None
    )
    return ReMeMemory(
        server_url=server_url,
        agent_name=agent_name,
        model_config=model_config,
        embedding_config=embedding_config,
    )
