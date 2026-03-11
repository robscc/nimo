"""API 集成测试（使用 TestClient + 内存 SQLite）。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.database import Base, get_db
from agentpal.main import create_app


# ── 测试数据库覆盖 ─────────────────────────────────────────

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
            await session.rollback()

    app.dependency_overrides[get_db] = override_db
    yield app
    await engine.dispose()


@pytest_asyncio.fixture
async def client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


# ── 健康检查 ──────────────────────────────────────────────

class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ── Session API ───────────────────────────────────────────

class TestSessionAPI:
    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, client: AsyncClient):
        resp = await client.get("/api/v1/sessions/non-existent-id")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_clear_memory_on_empty_session(self, client: AsyncClient):
        resp = await client.delete("/api/v1/sessions/empty-session/memory")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cleared"


# ── Task API ──────────────────────────────────────────────

class TestTaskAPI:
    @pytest.mark.asyncio
    async def test_get_nonexistent_task(self, client: AsyncClient):
        resp = await client.get("/api/v1/agent/tasks/non-existent-task")
        assert resp.status_code == 404
