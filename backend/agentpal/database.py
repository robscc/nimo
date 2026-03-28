"""SQLAlchemy 异步引擎与 Session 工厂。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from agentpal.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.is_dev,
    connect_args={"check_same_thread": False, "timeout": 60},  # SQLite 专用
)

# 同步引擎（供 produce_artifact 等同步工具使用）
_sync_engine = create_engine(
    settings.database_url.replace("+aiosqlite", ""),
    echo=settings.is_dev,
    connect_args={"check_same_thread": False, "timeout": 60},
)


# 启用 WAL 模式，允许并发读写（解决 skill_cli 工具调用时 database locked 问题）
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()


# 同步引擎也启用 WAL
@event.listens_for(_sync_engine, "connect")
def _set_sqlite_pragma_sync(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# 同步 Session 工厂（供 produce_artifact 等同步工具使用）
SyncSessionLocal = sessionmaker(
    bind=_sync_engine,
    class_=Session,
    expire_on_commit=False,
)


@contextmanager
def get_sync_db():
    """同步数据库 session 上下文管理器。

    供 produce_artifact 等同步工具使用。
    自动 commit，异常时 rollback。
    """
    session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


async def init_db() -> None:
    """建表（仅在首次启动时执行），并验证 WAL 模式生效。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 验证 WAL 模式确实生效
    # event listener 在每个连接上执行 PRAGMA journal_mode=WAL，
    # 但 journal_mode 是持久化到 DB 文件的属性，如果 DB 文件是在非 WAL 模式下
    # 创建的，或者有其他进程占用，切换可能静默失败。这里做一次显式校验。
    async with engine.connect() as conn:
        result = await conn.execute(__import__("sqlalchemy").text("PRAGMA journal_mode"))
        mode = result.scalar()
        if mode != "wal":
            # 强制切换（需要独占连接，create_all 后所有写事务已结束）
            await conn.execute(__import__("sqlalchemy").text("PRAGMA journal_mode=WAL"))
            result = await conn.execute(__import__("sqlalchemy").text("PRAGMA journal_mode"))
            mode = result.scalar()
        if mode != "wal":
            import warnings
            warnings.warn(
                f"SQLite WAL 模式启用失败（当前: {mode}），并发读写可能导致 database locked。"
                " 请确保没有其他进程占用数据库文件。",
                RuntimeWarning,
                stacklevel=1,
            )
        else:
            from loguru import logger as _db_logger
            _db_logger.info(f"SQLite journal_mode=WAL 已确认生效 ✅")


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
        # sub_agent_tasks: agent_name / task_type / execution_log (added for SubAgent routing)
        ("sub_agent_tasks", "agent_name", "ALTER TABLE sub_agent_tasks ADD COLUMN agent_name VARCHAR(64)"),
        ("sub_agent_tasks", "task_type", "ALTER TABLE sub_agent_tasks ADD COLUMN task_type VARCHAR(64)"),
        ("sub_agent_tasks", "execution_log", "ALTER TABLE sub_agent_tasks ADD COLUMN execution_log JSON NOT NULL DEFAULT '[]'"),
        # sub_agent_tasks: input_prompt / input_response / progress_pct / progress_message / started_at / completed_at (added for bidirectional comm)
        ("sub_agent_tasks", "input_prompt", "ALTER TABLE sub_agent_tasks ADD COLUMN input_prompt TEXT"),
        ("sub_agent_tasks", "input_response", "ALTER TABLE sub_agent_tasks ADD COLUMN input_response TEXT"),
        ("sub_agent_tasks", "progress_pct", "ALTER TABLE sub_agent_tasks ADD COLUMN progress_pct INTEGER DEFAULT 0"),
        ("sub_agent_tasks", "progress_message", "ALTER TABLE sub_agent_tasks ADD COLUMN progress_message TEXT"),
        ("sub_agent_tasks", "started_at", "ALTER TABLE sub_agent_tasks ADD COLUMN started_at DATETIME"),
        ("sub_agent_tasks", "completed_at", "ALTER TABLE sub_agent_tasks ADD COLUMN completed_at DATETIME"),
        # Phase 6: Rename metadata to extra in task_artifacts (metadata is reserved)
        ("task_artifacts", "extra", "ALTER TABLE task_artifacts ADD COLUMN extra JSON"),
        # Plan Mode: agent_mode on sessions
        ("sessions", "agent_mode", "ALTER TABLE sessions ADD COLUMN agent_mode VARCHAR(32) DEFAULT 'normal'"),
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

        # 创建新表（task_artifacts, task_events）
        from agentpal.models.session import TaskArtifact, TaskEvent
        await conn.run_sync(TaskArtifact.metadata.create_all)
        await conn.run_sync(TaskEvent.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends 注入用。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_standalone() -> AsyncGenerator[AsyncSession, None]:
    """独立短事务 session — 用于需要写操作但可能与 SSE 流并发的 endpoint。

    与 get_db 不同：调用者需要自己 commit，yield 后不会自动 commit。
    这样可以尽快释放 SQLite 写锁，避免与流式 chat 长事务冲突。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
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
