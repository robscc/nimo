"""pytest 全局配置与 fixtures。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.database import Base
from agentpal.memory.base import MemoryMessage, MemoryRole
from agentpal.memory.buffer import BufferMemory
from agentpal.memory.sqlite import SQLiteMemory
from agentpal.memory.hybrid import HybridMemory

# ── 测试数据库（内存 SQLite）────────────────────────────────


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


# ── Memory Fixtures ────────────────────────────────────────


@pytest.fixture
def buffer_memory() -> BufferMemory:
    return BufferMemory(max_size=10)


@pytest_asyncio.fixture
async def sqlite_memory(db_session: AsyncSession) -> SQLiteMemory:
    return SQLiteMemory(db=db_session, limit=100)


@pytest_asyncio.fixture
async def hybrid_memory(db_session: AsyncSession) -> HybridMemory:
    buffer = BufferMemory(max_size=10)
    sqlite = SQLiteMemory(db=db_session, limit=100)
    return HybridMemory(buffer=buffer, persistent=sqlite)


# ── 消息工厂 ──────────────────────────────────────────────


def make_msg(content: str, role: MemoryRole = MemoryRole.USER, session_id: str = "test-session") -> MemoryMessage:
    return MemoryMessage(session_id=session_id, role=role, content=content)
