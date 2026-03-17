"""记忆迁移脚本测试。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.database import Base
from agentpal.migrations.migrate_memory import run_migration
from agentpal.models.memory import MemoryRecord
from agentpal.models.session import SessionRecord, SessionStatus


@pytest_asyncio.fixture
async def migration_db():
    """专用的迁移测试数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


class TestMigrateMemory:
    @pytest.mark.asyncio
    async def test_migrate_empty_db(self, migration_db: AsyncSession):
        """空数据库应正常完成。"""
        stats = await run_migration(migration_db)
        assert stats["total_records"] == 0
        assert stats["updated_channel"] == 0

    @pytest.mark.asyncio
    async def test_migrate_channel_from_session_id(self, migration_db: AsyncSession):
        """应从 session_id 格式推导 channel。"""
        # 插入测试数据（channel 为 None）
        record = MemoryRecord(
            id=str(uuid.uuid4()),
            session_id="web:test-123",
            role="user",
            content="test message",
            created_at=datetime.now(timezone.utc),
            channel=None,
        )
        migration_db.add(record)
        await migration_db.flush()

        stats = await run_migration(migration_db)
        assert stats["updated_channel"] == 1

        # 验证 channel 已回填
        result = await migration_db.execute(
            text("SELECT channel FROM memory_records WHERE session_id = 'web:test-123'")
        )
        row = result.fetchone()
        assert row[0] == "web"

    @pytest.mark.asyncio
    async def test_migrate_user_id_from_session(self, migration_db: AsyncSession):
        """应从 sessions 表回填 user_id。"""
        # 创建 session
        session = SessionRecord(
            id="web:s1",
            channel="web",
            user_id="user-123",
            status=SessionStatus.ACTIVE,
        )
        migration_db.add(session)

        # 创建 memory record（user_id 为 None）
        record = MemoryRecord(
            id=str(uuid.uuid4()),
            session_id="web:s1",
            role="user",
            content="test",
            created_at=datetime.now(timezone.utc),
            user_id=None,
        )
        migration_db.add(record)
        await migration_db.flush()

        stats = await run_migration(migration_db)
        assert stats["updated_user_id"] == 1

    @pytest.mark.asyncio
    async def test_migrate_idempotent(self, migration_db: AsyncSession):
        """迁移应该是幂等的（多次运行结果一致）。"""
        record = MemoryRecord(
            id=str(uuid.uuid4()),
            session_id="web:test-456",
            role="user",
            content="test",
            created_at=datetime.now(timezone.utc),
            channel=None,
        )
        migration_db.add(record)
        await migration_db.flush()

        stats1 = await run_migration(migration_db)
        stats2 = await run_migration(migration_db)

        # 第二次运行应该不再更新
        assert stats1["updated_channel"] == 1
        assert stats2["updated_channel"] == 0

    @pytest.mark.asyncio
    async def test_migrate_preserves_existing_channel(self, migration_db: AsyncSession):
        """已有 channel 的记录不应被覆盖。"""
        record = MemoryRecord(
            id=str(uuid.uuid4()),
            session_id="web:test-789",
            role="user",
            content="test",
            created_at=datetime.now(timezone.utc),
            channel="dingtalk",  # 已有值
        )
        migration_db.add(record)
        await migration_db.flush()

        stats = await run_migration(migration_db)
        assert stats["updated_channel"] == 0

        # 确认 channel 未被改变
        result = await migration_db.execute(
            text("SELECT channel FROM memory_records WHERE session_id = 'web:test-789'")
        )
        row = result.fetchone()
        assert row[0] == "dingtalk"
