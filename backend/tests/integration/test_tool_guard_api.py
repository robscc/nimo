"""Tool Guard API 集成测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agentpal.database import Base, get_db, get_db_standalone
from agentpal.main import create_app
from agentpal.tools.tool_guard import ToolGuardManager

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(autouse=True)
async def _reset_guard():
    """每个测试前重置 ToolGuardManager 单例。"""
    ToolGuardManager.reset_instance()
    yield
    ToolGuardManager.reset_instance()


@pytest_asyncio.fixture
async def test_app():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    app = create_app()

    async def override_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_db_standalone] = override_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client

    await engine.dispose()


class TestToolGuardResolveAPI:
    """POST /api/v1/agent/tool-guard/{id}/resolve 端点测试。"""

    @pytest.mark.asyncio
    async def test_resolve_nonexistent(self, test_app):
        """尝试 resolve 不存在的 request_id → 404。"""
        resp = await test_app.post(
            "/api/v1/agent/tool-guard/nonexistent-id/resolve",
            json={"approved": True},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_resolve_existing_approve(self, test_app):
        """创建 pending 请求，resolve approve → 200。"""
        guard = ToolGuardManager.get_instance()
        pending = guard.create_pending("test-req-1", "execute_shell_command", {"command": "rm file"})

        resp = await test_app.post(
            "/api/v1/agent/tool-guard/test-req-1/resolve",
            json={"approved": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["request_id"] == "test-req-1"
        assert data["approved"] is True
        assert pending.approved is True
        assert pending.event.is_set()

    @pytest.mark.asyncio
    async def test_resolve_existing_reject(self, test_app):
        """创建 pending 请求，resolve reject → 200。"""
        guard = ToolGuardManager.get_instance()
        pending = guard.create_pending("test-req-2", "write_file", {"file_path": "/etc/hosts"})

        resp = await test_app.post(
            "/api/v1/agent/tool-guard/test-req-2/resolve",
            json={"approved": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is False
        assert pending.approved is False
        assert pending.event.is_set()


class TestSessionToolGuardThreshold:
    """Session tool_guard_threshold 配置测试。"""

    @pytest.mark.asyncio
    async def test_session_meta_includes_threshold(self, test_app):
        """SessionMeta 包含 tool_guard_threshold 字段。"""
        # 创建 session
        resp = await test_app.post("/api/v1/sessions", params={"channel": "web"})
        assert resp.status_code == 201
        session_id = resp.json()["id"]

        # 获取 meta
        resp = await test_app.get(f"/api/v1/sessions/{session_id}/meta")
        assert resp.status_code == 200
        meta = resp.json()
        assert "tool_guard_threshold" in meta
        assert meta["tool_guard_threshold"] is None  # 新 session 默认 null

    @pytest.mark.asyncio
    async def test_update_session_threshold(self, test_app):
        """PATCH config 可以更新 tool_guard_threshold。"""
        resp = await test_app.post("/api/v1/sessions", params={"channel": "web"})
        session_id = resp.json()["id"]

        # 更新 threshold
        resp = await test_app.patch(
            f"/api/v1/sessions/{session_id}/config",
            json={"tool_guard_threshold": 3},
        )
        assert resp.status_code == 200
        assert resp.json()["tool_guard_threshold"] == 3

        # 验证持久化
        resp = await test_app.get(f"/api/v1/sessions/{session_id}/meta")
        assert resp.json()["tool_guard_threshold"] == 3
