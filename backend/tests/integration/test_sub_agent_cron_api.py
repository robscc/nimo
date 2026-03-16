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


# ── Coder SubAgent 专项集成测试 ──────────────────────────


class TestCoderSubAgentIntegration:
    """验证 coder SubAgent 的配置、任务类型路由和 API 安全性。"""

    @pytest.mark.asyncio
    async def test_default_coder_accepted_task_types(self, client: AsyncClient):
        """默认 coder 应支持 code / debug / script / implement / test 任务类型。"""
        # list 触发默认创建
        resp = await client.get("/api/v1/sub-agents")
        assert resp.status_code == 200

        agents = resp.json()
        coder = next(a for a in agents if a["name"] == "coder")

        expected_types = {"code", "debug", "script", "implement", "test"}
        actual_types = set(coder["accepted_task_types"])
        assert expected_types == actual_types

    @pytest.mark.asyncio
    async def test_coder_display_name(self, client: AsyncClient):
        """默认 coder 的 display_name 应为 '编码员'。"""
        resp = await client.get("/api/v1/sub-agents")
        agents = resp.json()
        coder = next(a for a in agents if a["name"] == "coder")

        assert coder["display_name"] == "编码员"

    @pytest.mark.asyncio
    async def test_create_coder_with_custom_model(self, client: AsyncClient):
        """为 coder 配置专用编码模型，has_custom_model 应为 True。"""
        resp = await client.post("/api/v1/sub-agents", json={
            "name": "coder-pro",
            "display_name": "高级编码员",
            "role_prompt": "你是一个专注于 Python 后端开发的专家编码员。",
            "accepted_task_types": ["code", "debug", "refactor", "test"],
            "model_name": "qwen-coder-plus",
            "model_provider": "compatible",
            "model_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "max_tool_rounds": 12,
            "timeout_seconds": 600,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "coder-pro"
        assert data["has_custom_model"] is True
        assert data["model_name"] == "qwen-coder-plus"
        assert data["max_tool_rounds"] == 12
        assert data["timeout_seconds"] == 600

    @pytest.mark.asyncio
    async def test_api_does_not_expose_model_api_key(self, client: AsyncClient):
        """API 响应中不应暴露 model_api_key 字段。"""
        await client.post("/api/v1/sub-agents", json={
            "name": "secure-coder",
            "model_name": "qwen-coder",
            "model_api_key": "sk-secret-key",
            "accepted_task_types": ["code"],
        })

        resp = await client.get("/api/v1/sub-agents/secure-coder")
        assert resp.status_code == 200
        data = resp.json()
        # 敏感字段不应出现在响应中
        assert "model_api_key" not in data
        # 有 model_name 时 has_custom_model 应为 True
        assert data["has_custom_model"] is True

    @pytest.mark.asyncio
    async def test_coder_without_custom_model(self, client: AsyncClient):
        """未配置自定义模型的 coder，has_custom_model 应为 False。"""
        await client.post("/api/v1/sub-agents", json={
            "name": "basic-coder",
            "accepted_task_types": ["code"],
        })

        resp = await client.get("/api/v1/sub-agents/basic-coder")
        assert resp.status_code == 200
        assert resp.json()["has_custom_model"] is False

    @pytest.mark.asyncio
    async def test_update_coder_model_config(self, client: AsyncClient):
        """更新 coder 的模型配置后，has_custom_model 应变为 True。"""
        await client.post("/api/v1/sub-agents", json={
            "name": "upgradable-coder",
            "accepted_task_types": ["code"],
        })

        # 初始无自定义模型
        resp = await client.get("/api/v1/sub-agents/upgradable-coder")
        assert resp.json()["has_custom_model"] is False

        # 更新添加自定义模型
        patch_resp = await client.patch("/api/v1/sub-agents/upgradable-coder", json={
            "model_name": "deepseek-coder",
        })
        assert patch_resp.status_code == 200
        assert patch_resp.json()["has_custom_model"] is True
        assert patch_resp.json()["model_name"] == "deepseek-coder"

    @pytest.mark.asyncio
    async def test_coder_max_tool_rounds_configurable(self, client: AsyncClient):
        """coder 的 max_tool_rounds 应可配置，用于控制工具调用深度。"""
        resp = await client.post("/api/v1/sub-agents", json={
            "name": "deep-coder",
            "accepted_task_types": ["code", "debug"],
            "max_tool_rounds": 20,  # 更多轮次用于复杂调试
        })
        assert resp.status_code == 201
        assert resp.json()["max_tool_rounds"] == 20

    @pytest.mark.asyncio
    async def test_coder_disable_and_reenable(self, client: AsyncClient):
        """禁用/重新启用 coder SubAgent。"""
        await client.post("/api/v1/sub-agents", json={
            "name": "toggle-coder",
            "accepted_task_types": ["code"],
        })

        # 禁用
        resp = await client.patch("/api/v1/sub-agents/toggle-coder", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # 重新启用
        resp = await client.patch("/api/v1/sub-agents/toggle-coder", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    @pytest.mark.asyncio
    async def test_coder_add_extra_task_types(self, client: AsyncClient):
        """可以为 coder 追加新的任务类型（如 refactor）。"""
        await client.post("/api/v1/sub-agents", json={
            "name": "versatile-coder",
            "accepted_task_types": ["code", "debug"],
        })

        resp = await client.patch("/api/v1/sub-agents/versatile-coder", json={
            "accepted_task_types": ["code", "debug", "refactor", "optimize"],
        })
        assert resp.status_code == 200
        actual = set(resp.json()["accepted_task_types"])
        assert {"code", "debug", "refactor", "optimize"} == actual

    @pytest.mark.asyncio
    async def test_cron_coding_schedule_weekdays(self, client: AsyncClient):
        """为 coder 创建工作日代码检查定时任务。"""
        resp = await client.post("/api/v1/cron", json={
            "name": "每日代码检查",
            "schedule": "0 8 * * 1-5",
            "task_prompt": (
                "检查仓库的代码质量：\n"
                "1. 运行 ruff 检查 lint 问题\n"
                "2. 运行 pytest 确认测试通过\n"
                "3. 汇报结果"
            ),
            "agent_name": "coder",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["agent_name"] == "coder"
        assert data["schedule"] == "0 8 * * 1-5"
        assert data["enabled"] is True

    @pytest.mark.asyncio
    async def test_cron_coding_schedule_with_custom_coder(self, client: AsyncClient):
        """为自定义 coder-pro 创建定时编码任务，验证 agent_name 正确关联。"""
        # 先创建 coder-pro
        create_resp = await client.post("/api/v1/sub-agents", json={
            "name": "coder-pro-v2",
            "display_name": "专业编码员",
            "accepted_task_types": ["code", "test", "refactor"],
            "model_name": "qwen-coder-plus",
        })
        assert create_resp.status_code == 201

        # 为 coder-pro 创建定时任务
        cron_resp = await client.post("/api/v1/cron", json={
            "name": "自动化测试",
            "schedule": "0 22 * * *",
            "task_prompt": "运行全量测试套件，生成测试报告",
            "agent_name": "coder-pro-v2",
        })
        assert cron_resp.status_code == 201
        assert cron_resp.json()["agent_name"] == "coder-pro-v2"
