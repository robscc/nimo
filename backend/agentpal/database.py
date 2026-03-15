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
