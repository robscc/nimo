"""SubAgent + Cron API 集成测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.database import Base, get_db
from agentpal.main import create_app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_app(tmp_path):
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


# ── SubAgent API ─────────────────────────────────────────


class TestSubAgentAPI:
    @pytest.mark.asyncio
    async def test_list_sub_agents(self, client: AsyncClient):
        """列出 SubAgent 应返回默认角色。"""
        resp = await client.get("/api/v1/sub-agents")
        assert resp.status_code == 200
        agents = resp.json()
        names = [a["name"] for a in agents]
        assert "researcher" in names
        assert "coder" in names

    @pytest.mark.asyncio
    async def test_get_sub_agent(self, client: AsyncClient):
        """获取单个 SubAgent（默认会被 list 创建）。"""
        # list 触发默认创建后 rollback 了，所以单独创建
        await client.post("/api/v1/sub-agents", json={
            "name": "researcher",
            "display_name": "调研员",
            "accepted_task_types": ["research"],
        })

        resp = await client.get("/api/v1/sub-agents/researcher")
        assert resp.status_code == 200
        agent = resp.json()
        assert agent["name"] == "researcher"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, client: AsyncClient):
        resp = await client.get("/api/v1/sub-agents/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_sub_agent(self, client: AsyncClient):
        """创建自定义 SubAgent。"""
        resp = await client.post("/api/v1/sub-agents", json={
            "name": "writer",
            "display_name": "写作员",
            "role_prompt": "你是一个写作专家",
            "accepted_task_types": ["write"],
        })
        assert resp.status_code == 201
        assert resp.json()["name"] == "writer"

    @pytest.mark.asyncio
    async def test_create_duplicate(self, client: AsyncClient):
        """创建重复 SubAgent 应返回 400。"""
        await client.post("/api/v1/sub-agents", json={"name": "dup"})
        resp = await client.post("/api/v1/sub-agents", json={"name": "dup"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_update_sub_agent(self, client: AsyncClient):
        """更新 SubAgent。"""
        await client.post("/api/v1/sub-agents", json={"name": "editable"})
        resp = await client.patch("/api/v1/sub-agents/editable", json={
            "display_name": "已编辑",
            "enabled": False,
        })
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "已编辑"
        assert resp.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_delete_sub_agent(self, client: AsyncClient):
        """删除 SubAgent。"""
        await client.post("/api/v1/sub-agents", json={"name": "deletable"})
        resp = await client.delete("/api/v1/sub-agents/deletable")
        assert resp.status_code == 204


# ── Cron API ─────────────────────────────────────────────


class TestCronAPI:
    @pytest.mark.asyncio
    async def test_create_cron_job(self, client: AsyncClient):
        """创建定时任务。"""
        resp = await client.post("/api/v1/cron", json={
            "name": "每日报告",
            "schedule": "0 9 * * *",
            "task_prompt": "生成今日报告",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "每日报告"
        assert data["schedule"] == "0 9 * * *"
        assert data["enabled"] is True

    @pytest.mark.asyncio
    async def test_create_invalid_schedule(self, client: AsyncClient):
        """无效 cron 表达式应返回 400。"""
        resp = await client.post("/api/v1/cron", json={
            "name": "bad", "schedule": "invalid", "task_prompt": "test",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_cron_jobs(self, client: AsyncClient):
        """列出定时任务。"""
        await client.post("/api/v1/cron", json={
            "name": "j1", "schedule": "0 9 * * *", "task_prompt": "t1",
        })
        await client.post("/api/v1/cron", json={
            "name": "j2", "schedule": "0 18 * * *", "task_prompt": "t2",
        })

        resp = await client.get("/api/v1/cron")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    @pytest.mark.asyncio
    async def test_update_cron_job(self, client: AsyncClient):
        """更新定时任务。"""
        create_resp = await client.post("/api/v1/cron", json={
            "name": "原名", "schedule": "0 9 * * *", "task_prompt": "test",
        })
        job_id = create_resp.json()["id"]

        resp = await client.patch(f"/api/v1/cron/{job_id}", json={
            "name": "新名", "enabled": False,
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "新名"
        assert resp.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_delete_cron_job(self, client: AsyncClient):
        """删除定时任务。"""
        create_resp = await client.post("/api/v1/cron", json={
            "name": "del", "schedule": "0 9 * * *", "task_prompt": "test",
        })
        job_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/v1/cron/{job_id}")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_toggle_cron_job(self, client: AsyncClient):
        """启用/禁用定时任务。"""
        create_resp = await client.post("/api/v1/cron", json={
            "name": "toggle", "schedule": "0 9 * * *", "task_prompt": "test",
        })
        job_id = create_resp.json()["id"]

        resp = await client.patch(f"/api/v1/cron/{job_id}/toggle", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, client: AsyncClient):
        resp = await client.get("/api/v1/cron/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cron_job_with_agent(self, client: AsyncClient):
        """创建指定 SubAgent 的定时任务。"""
        resp = await client.post("/api/v1/cron", json={
            "name": "代码巡检",
            "schedule": "0 8 * * 1-5",
            "task_prompt": "检查代码库的 lint 和测试状态",
            "agent_name": "coder",
        })
        assert resp.status_code == 201
        assert resp.json()["agent_name"] == "coder"
