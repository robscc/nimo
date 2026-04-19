"""SQLAlchemy 异步引擎与 Session 工厂。"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from loguru import logger
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from agentpal.config import get_settings

settings = get_settings()

# Hermes-style write retry with random jitter — breaks the "convoy effect"
# where deterministic SQLite busy handlers wake all contending writers at the
# same instant, causing them to collide on the write lock again.
_WRITE_MAX_RETRIES = 15
_WRITE_RETRY_MIN_S = 0.020  # 20ms
_WRITE_RETRY_MAX_S = 0.150  # 150ms
_CHECKPOINT_EVERY = 50
_write_count = 0
_write_count_lock = asyncio.Lock()

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
    cursor.execute("PRAGMA busy_timeout=3000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA wal_autocheckpoint=1000")
    cursor.close()


# 同步引擎也启用 WAL
@event.listens_for(_sync_engine, "connect")
def _set_sqlite_pragma_sync(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=3000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA wal_autocheckpoint=1000")
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


def _is_sqlite_locked_error(exc: Exception) -> bool:
    """判断异常是否为 SQLite 锁冲突（database is locked / SQLITE_BUSY）。"""
    if not isinstance(exc, OperationalError):
        return False
    msg = str(exc).lower()
    return (
        "database is locked" in msg
        or "sqlite_busy" in msg
        or "database table is locked" in msg
    )


def _format_commit_retry_context(context: dict[str, Any] | str | None) -> str:
    """标准化 commit 重试上下文。

    统一输出固定字段集（key=value），便于 grep 聚合：
    component / phase / session_id / task_id / tool_name / status / agent_name
    """
    if context is None:
        context = {}

    if isinstance(context, str):
        # 兼容历史字符串上下文，仍保证有统一字段前缀
        return (
            "component=n/a phase=n/a session_id=n/a task_id=n/a "
            "tool_name=n/a status=n/a agent_name=n/a "
            f"raw={context.replace(' ', '_')}"
        )

    fixed_keys = (
        "component",
        "phase",
        "session_id",
        "task_id",
        "tool_name",
        "status",
        "agent_name",
    )

    parts: list[str] = []
    for key in fixed_keys:
        value = context.get(key)
        text = "n/a" if value is None else str(value).replace(" ", "_")
        parts.append(f"{key}={text}")

    # 额外字段按 key 排序附加，保留扩展能力
    for key in sorted(k for k in context.keys() if k not in fixed_keys):
        value = context.get(key)
        if value is None:
            continue
        parts.append(f"{key}={str(value).replace(' ', '_')}")

    return " ".join(parts)


async def commit_with_retry(
    session: AsyncSession,
    *,
    max_attempts: int = _WRITE_MAX_RETRIES,
    base_delay: float | None = None,
    context: dict[str, Any] | str | None = None,
) -> None:
    """为 SQLite 短暂锁竞争提供带抖动重试的 commit。

    仅针对锁冲突重试；其他异常直接抛出。
    每次失败后 rollback 清理事务，再按 Hermes 风格随机抖动等待 —
    打破确定性 busy handler 的"车队效应"。

    Args:
        session: AsyncSession 实例
        max_attempts: 最大尝试次数（含首次 commit），默认 15
        base_delay: 若提供则固定使用该延迟（秒，主要用于测试）；
            否则每次重试在 [20, 150] ms 间随机抽取。
        context: 可观测性上下文
    """
    attempt = 0
    ctx = _format_commit_retry_context(context)

    while True:
        try:
            await session.commit()
            if attempt > 0:
                logger.info(
                    "sqlite_commit_retry event=success context='{}' attempts={}",
                    ctx,
                    attempt + 1,
                )
            await _maybe_checkpoint(session)
            return
        except Exception as exc:
            attempt += 1
            is_locked = _is_sqlite_locked_error(exc)
            if not is_locked:
                logger.error(
                    "sqlite_commit_retry event=non_retryable_error context='{}' attempts={} error={}",
                    ctx,
                    attempt,
                    exc,
                )
                raise

            if attempt >= max_attempts:
                logger.error(
                    "sqlite_commit_retry event=exhausted context='{}' max_attempts={} error={}",
                    ctx,
                    max_attempts,
                    exc,
                )
                raise

            if base_delay is None:
                delay = random.uniform(_WRITE_RETRY_MIN_S, _WRITE_RETRY_MAX_S)
            else:
                delay = base_delay
            logger.warning(
                "sqlite_commit_retry event=retrying context='{}' attempt={}/{} delay_ms={} error={}",
                ctx,
                attempt,
                max_attempts,
                int(delay * 1000),
                exc,
            )
            await session.rollback()
            if delay > 0:
                await asyncio.sleep(delay)


async def _maybe_checkpoint(session: AsyncSession) -> None:
    """每 _CHECKPOINT_EVERY 次成功写入后做一次 passive WAL checkpoint。

    PASSIVE 模式不会阻塞其他读写，失败时静默忽略 —
    防止 WAL 文件在长时间运行中无限增长。
    """
    global _write_count
    async with _write_count_lock:
        _write_count += 1
        should_checkpoint = _write_count % _CHECKPOINT_EVERY == 0

    if not should_checkpoint:
        return

    try:
        await session.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
    except Exception as exc:
        logger.debug("wal_checkpoint_passive_failed error={}", exc)


@asynccontextmanager
async def write_session(
    context: dict[str, Any] | str | None = None,
) -> AsyncIterator[AsyncSession]:
    """显式写事务上下文：BEGIN IMMEDIATE + 抖动重试 commit。

    用于热点写路径（memory inserts、tool logs、agent_messages 等）。
    BEGIN IMMEDIATE 在事务起点就抢 RESERVED 写锁，冲突立刻暴露并进入
    抖动重试循环，避免做完一串 INSERT 才在 COMMIT 撞锁回滚。

    纯读路径请继续用 AsyncSessionLocal() / get_db()，这里只影响显式写，
    不会拖慢读并发。
    """
    async with AsyncSessionLocal() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        try:
            yield session
            await commit_with_retry(session, context=context)
        except Exception:
            await session.rollback()
            raise


async def shutdown_checkpoint() -> None:
    """进程关闭前做一次 TRUNCATE checkpoint，把 WAL 刷回主库。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        logger.info("sqlite_shutdown_checkpoint event=done")
    except Exception as exc:
        logger.warning("sqlite_shutdown_checkpoint event=failed error={}", exc)


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
