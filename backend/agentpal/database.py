"""SQLAlchemy 异步引擎与 Session 工厂。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from agentpal.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.is_dev,
    connect_args={"check_same_thread": False, "timeout": 30},  # SQLite 专用
)


# 启用 WAL 模式，允许并发读写（解决 skill_cli 工具调用时 database locked 问题）
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


async def init_db() -> None:
    """建表（仅在首次启动时执行）。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def run_migrations() -> None:
    """轻量级列迁移 — 为已存在的表补充新列（SQLite 不支持 IF NOT EXISTS on ALTER COLUMN）。

    每次启动时幂等执行，列已存在时静默跳过。
    """
    migrations = [
        # cron_jobs: target_session_id (added in v0.2)
        ("cron_jobs", "target_session_id", "ALTER TABLE cron_jobs ADD COLUMN target_session_id VARCHAR(128)"),
        # sessions: tool_guard_threshold (added for Tool Guard)
        ("sessions", "tool_guard_threshold", "ALTER TABLE sessions ADD COLUMN tool_guard_threshold INTEGER"),
        # sub_agent_tasks: priority / retry_count / max_retries (added for Priority Queue)
        ("sub_agent_tasks", "priority", "ALTER TABLE sub_agent_tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 5"),
        ("sub_agent_tasks", "retry_count", "ALTER TABLE sub_agent_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"),
        ("sub_agent_tasks", "max_retries", "ALTER TABLE sub_agent_tasks ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 3"),
        # memory_records: user_id / channel / memory_type (added in v0.3 for cross-session search)
        ("memory_records", "user_id", "ALTER TABLE memory_records ADD COLUMN user_id VARCHAR(128)"),
        ("memory_records", "channel", "ALTER TABLE memory_records ADD COLUMN channel VARCHAR(64)"),
        ("memory_records", "memory_type", "ALTER TABLE memory_records ADD COLUMN memory_type VARCHAR(32) NOT NULL DEFAULT 'conversation'"),
    ]
    async with engine.begin() as conn:
        for table, column, sql in migrations:
            # 查询列是否已存在
            result = await conn.execute(
                __import__("sqlalchemy").text(f"PRAGMA table_info({table})")
            )
            existing_cols = {row[1] for row in result.fetchall()}
            if column not in existing_cols:
                await conn.execute(__import__("sqlalchemy").text(sql))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends 注入用。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def utc_isoformat(dt: "datetime | None") -> "str | None":
    """将 datetime 序列化为带 UTC 标记的 ISO 8601 字符串。

    SQLite + SQLAlchemy 返回的 datetime 通常是 naive（tzinfo=None），
    直接 .isoformat() 缺少 +00:00 后缀，导致 JavaScript 将其当作本地时间解析，
    在 UTC+8 环境下出现 8 小时偏差。

    此函数统一为 naive datetime 加上 UTC tzinfo 再序列化。
    """
    if dt is None:
        return None
    from datetime import timezone as _tz
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz.utc)
    return dt.isoformat()
