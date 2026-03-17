"""Session + Config API 集成测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.config import get_settings
from agentpal.database import Base, get_db, get_db_standalone
from agentpal.main import create_app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_app(tmp_path):
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    # 清除 Settings lru_cache，让 workspace_dir 可以被覆盖
    get_settings.cache_clear()
    original_settings = get_settings()
    test_workspace = str(tmp_path / ".nimo")

    # 用 object.__setattr__ 强制覆盖 pydantic frozen 字段，
    # 避免测试写入真实的 ~/.nimo/config.yaml
    object.__setattr__(original_settings, "workspace_dir", test_workspace)

    app = create_app()

    async def override_db():
        async with session_factory() as session:
            yield session
            await session.rollback()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_db_standalone] = override_db
    yield app

    # 恢复：清除被污染的 Settings 缓存
    get_settings.cache_clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


# ── Session Meta API ─────────────────────────────────────


class TestSessionMetaAPI:
    @pytest.mark.asyncio
    async def test_create_session_with_model_name(self, client: AsyncClient):
        """创建的 session 应包含默认 model_name。"""
        resp = await client.post("/api/v1/sessions", params={"channel": "web"})
        assert resp.status_code == 201
        session_id = resp.json()["id"]

        # 获取 meta
        resp = await client.get(f"/api/v1/sessions/{session_id}/meta")
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["id"] == session_id
        assert meta["message_count"] == 0
        # model_name 应来自 Settings 默认值
        assert meta["model_name"] is not None

    @pytest.mark.asyncio
    async def test_session_meta_not_found(self, client: AsyncClient):
        resp = await client.get("/api/v1/sessions/nonexistent/meta")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_session_config(self, client: AsyncClient):
        """更新 session 级工具/技能配置。"""
        # 创建 session
        resp = await client.post("/api/v1/sessions", params={"channel": "web"})
        session_id = resp.json()["id"]

        # 配置 session 级工具
        resp = await client.patch(
            f"/api/v1/sessions/{session_id}/config",
            json={
                "enabled_tools": ["read_file", "get_current_time"],
                "enabled_skills": ["find-skills"],
            },
        )
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["enabled_tools"] == ["read_file", "get_current_time"]
        assert meta["enabled_skills"] == ["find-skills"]

    @pytest.mark.asyncio
    async def test_session_config_null_follows_global(self, client: AsyncClient):
        """enabled_tools 为 null 表示跟随全局。"""
        resp = await client.post("/api/v1/sessions", params={"channel": "web"})
        session_id = resp.json()["id"]

        resp = await client.get(f"/api/v1/sessions/{session_id}/meta")
        meta = resp.json()
        assert meta["enabled_tools"] is None
        assert meta["enabled_skills"] is None

    @pytest.mark.asyncio
    async def test_update_session_config_not_found(self, client: AsyncClient):
        resp = await client.patch(
            "/api/v1/sessions/nonexistent/config",
            json={"enabled_tools": ["read_file"]},
        )
        assert resp.status_code == 404


# ── Config API ───────────────────────────────────────────


class TestConfigAPI:
    @pytest.mark.asyncio
    async def test_get_config(self, client: AsyncClient):
        """获取配置应返回默认值。"""
        resp = await client.get("/api/v1/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "config" in data
        assert "path" in data
        assert "app" in data["config"]

    @pytest.mark.asyncio
    async def test_update_config(self, client: AsyncClient):
        """更新配置应合并而非覆盖。"""
        resp = await client.put(
            "/api/v1/config",
            json={"config": {"llm": {"model": "gpt-4o"}}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["llm"]["model"] == "gpt-4o"
        # 其他字段应保留（不能是空）
        assert data["config"]["llm"]["provider"] != ""

    @pytest.mark.asyncio
    async def test_init_config(self, client: AsyncClient):
        """初始化配置应幂等。"""
        resp = await client.post("/api/v1/config/init")
        assert resp.status_code == 200
        data = resp.json()
        assert "config" in data


# ── Session List and Delete ──────────────────────────────


class TestSessionCRUD:
    @pytest.mark.asyncio
    async def test_list_empty_sessions(self, client: AsyncClient):
        resp = await client.get("/api/v1/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_create_and_list_sessions(self, client: AsyncClient):
        # 创建
        resp = await client.post("/api/v1/sessions", params={"channel": "web"})
        assert resp.status_code == 201
        session_id = resp.json()["id"]

        # 列表
        resp = await client.get("/api/v1/sessions")
        assert resp.status_code == 200
        sessions = resp.json()
        assert len(sessions) >= 1
        assert any(s["id"] == session_id for s in sessions)

    @pytest.mark.asyncio
    async def test_delete_session(self, client: AsyncClient):
        resp = await client.post("/api/v1/sessions", params={"channel": "web"})
        session_id = resp.json()["id"]

        resp = await client.delete(f"/api/v1/sessions/{session_id}")
        assert resp.status_code == 204
