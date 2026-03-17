"""Memory API 集成测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.database import Base, get_db
from agentpal.main import create_app
from agentpal.memory.base import MemoryMessage, MemoryRole
from agentpal.models.memory import MemoryRecord

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_app():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    app = create_app()

    async def override_db():
        async with session_factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = override_db
    yield app, session_factory
    await engine.dispose()


@pytest_asyncio.fixture
async def client(test_app):
    app, _ = test_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def seeded_app(test_app):
    """预先填充一些记忆数据的 app。"""
    app, session_factory = test_app

    async with session_factory() as db:
        import uuid
        from datetime import datetime, timezone

        records = [
            MemoryRecord(
                id=str(uuid.uuid4()), session_id="web:s1", role="user",
                content="今天天气很好", user_id="user1", channel="web",
                memory_type="conversation",
                created_at=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            ),
            MemoryRecord(
                id=str(uuid.uuid4()), session_id="web:s2", role="user",
                content="明天的天气如何", user_id="user1", channel="web",
                memory_type="conversation",
                created_at=datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
            ),
            MemoryRecord(
                id=str(uuid.uuid4()), session_id="dt:s3", role="user",
                content="钉钉天气查询", user_id="user2", channel="dingtalk",
                memory_type="conversation",
                created_at=datetime(2024, 1, 3, 0, 0, tzinfo=timezone.utc),
            ),
            MemoryRecord(
                id=str(uuid.uuid4()), session_id="web:s4", role="assistant",
                content="不相关的消息", user_id="user1", channel="web",
                memory_type="conversation",
                created_at=datetime(2024, 1, 4, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        for r in records:
            db.add(r)
        await db.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


class TestMemorySearchAPI:
    @pytest.mark.asyncio
    async def test_search_by_user_id(self, seeded_app: AsyncClient):
        """按 user_id 搜索。"""
        resp = await seeded_app.post(
            "/api/v1/memory/search",
            json={"query": "天气", "user_id": "user1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "user"
        assert data["total"] == 2
        assert all(r["user_id"] == "user1" for r in data["results"])

    @pytest.mark.asyncio
    async def test_search_by_channel(self, seeded_app: AsyncClient):
        """按 channel 搜索。"""
        resp = await seeded_app.post(
            "/api/v1/memory/search",
            json={"query": "天气", "channel": "dingtalk"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "channel"
        assert data["total"] == 1
        assert data["results"][0]["channel"] == "dingtalk"

    @pytest.mark.asyncio
    async def test_search_global(self, seeded_app: AsyncClient):
        """全局搜索。"""
        resp = await seeded_app.post(
            "/api/v1/memory/search",
            json={"query": "天气", "global_access": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "global"
        assert data["total"] == 3

    @pytest.mark.asyncio
    async def test_search_by_session(self, seeded_app: AsyncClient):
        """按 session_id 搜索。"""
        resp = await seeded_app.post(
            "/api/v1/memory/search",
            json={"query": "天气", "session_id": "web:s1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "session"
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_search_empty_scope_returns_400(self, seeded_app: AsyncClient):
        """空 scope 应返回 400。"""
        resp = await seeded_app.post(
            "/api/v1/memory/search",
            json={"query": "test"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_search_with_limit(self, seeded_app: AsyncClient):
        """limit 参数。"""
        resp = await seeded_app.post(
            "/api/v1/memory/search",
            json={"query": "天气", "global_access": True, "limit": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_search_no_results(self, seeded_app: AsyncClient):
        """无匹配结果。"""
        resp = await seeded_app.post(
            "/api/v1/memory/search",
            json={"query": "不存在的XYZ", "global_access": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["results"] == []


class TestSessionMemorySearchAPI:
    @pytest.mark.asyncio
    async def test_session_search(self, seeded_app: AsyncClient):
        """单 session 搜索 GET 接口。"""
        resp = await seeded_app.get(
            "/api/v1/memory/sessions/web:s1/search",
            params={"query": "天气"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "session"
        assert data["total"] == 1
