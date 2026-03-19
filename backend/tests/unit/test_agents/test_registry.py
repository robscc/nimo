"""SubAgent Registry 单元测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.database import Base
from agentpal.models.agent import SubAgentDefinition
from agentpal.agents.registry import SubAgentRegistry


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


class TestSubAgentRegistry:
    @pytest.mark.asyncio
    async def test_ensure_defaults_creates_agents(self, db: AsyncSession):
        """ensure_defaults 应创建默认的 researcher 和 coder。"""
        registry = SubAgentRegistry(db)
        await registry.ensure_defaults()

        agents = await registry.list_agents()
        names = [a["name"] for a in agents]
        assert "researcher" in names
        assert "coder" in names

    @pytest.mark.asyncio
    async def test_ensure_defaults_idempotent(self, db: AsyncSession):
        """多次调用 ensure_defaults 不应重复创建。"""
        registry = SubAgentRegistry(db)
        await registry.ensure_defaults()
        await registry.ensure_defaults()

        agents = await registry.list_agents()
        assert len(agents) == 2

    @pytest.mark.asyncio
    async def test_create_agent(self, db: AsyncSession):
        """创建自定义 SubAgent。"""
        registry = SubAgentRegistry(db)
        result = await registry.create_agent({
            "name": "writer",
            "display_name": "写作员",
            "role_prompt": "你是一个写作专家",
            "accepted_task_types": ["write", "edit"],
        })
        assert result["name"] == "writer"
        assert result["display_name"] == "写作员"
        assert result["accepted_task_types"] == ["write", "edit"]

    @pytest.mark.asyncio
    async def test_create_duplicate_raises(self, db: AsyncSession):
        """创建重复名称的 SubAgent 应抛出 ValueError。"""
        registry = SubAgentRegistry(db)
        await registry.create_agent({"name": "test-agent"})

        with pytest.raises(ValueError, match="已存在"):
            await registry.create_agent({"name": "test-agent"})

    @pytest.mark.asyncio
    async def test_update_agent(self, db: AsyncSession):
        """更新 SubAgent 配置。"""
        registry = SubAgentRegistry(db)
        await registry.create_agent({
            "name": "updatable",
            "display_name": "原名",
        })

        result = await registry.update_agent("updatable", {
            "display_name": "新名",
            "max_tool_rounds": 5,
        })
        assert result is not None
        assert result["display_name"] == "新名"
        assert result["max_tool_rounds"] == 5

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_none(self, db: AsyncSession):
        registry = SubAgentRegistry(db)
        result = await registry.update_agent("nonexistent", {"display_name": "x"})
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_agent(self, db: AsyncSession):
        """删除 SubAgent。"""
        registry = SubAgentRegistry(db)
        await registry.create_agent({"name": "deletable"})
        assert await registry.delete_agent("deletable") is True
        assert await registry.get_agent("deletable") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, db: AsyncSession):
        registry = SubAgentRegistry(db)
        assert await registry.delete_agent("nonexistent") is False

    @pytest.mark.asyncio
    async def test_find_agent_for_task(self, db: AsyncSession):
        """根据任务类型匹配 SubAgent。"""
        registry = SubAgentRegistry(db)
        await registry.ensure_defaults()

        # research → researcher
        agent = await registry.find_agent_for_task("research")
        assert agent is not None
        assert agent.name == "researcher"

        # code → coder
        agent = await registry.find_agent_for_task("code")
        assert agent is not None
        assert agent.name == "coder"

        # unknown → None
        agent = await registry.find_agent_for_task("cooking")
        assert agent is None

    @pytest.mark.asyncio
    async def test_get_enabled_agents(self, db: AsyncSession):
        """获取所有已启用的 SubAgent。"""
        registry = SubAgentRegistry(db)
        await registry.ensure_defaults()

        enabled = await registry.get_enabled_agents()
        assert len(enabled) == 2

        # 禁用一个
        await registry.update_agent("coder", {"enabled": False})
        enabled = await registry.get_enabled_agents()
        assert len(enabled) == 1
        assert enabled[0].name == "researcher"


class TestSubAgentDefinitionModel:
    @pytest.mark.asyncio
    async def test_get_model_config_with_custom(self, db: AsyncSession):
        """自定义模型配置应覆盖 fallback。"""
        defn = SubAgentDefinition(
            name="test",
            model_name="gpt-4o",
            model_provider="openai",
        )
        config = defn.get_model_config({
            "provider": "compatible",
            "model_name": "qwen",
            "api_key": "sk-default",
            "base_url": "http://default",
        })
        assert config["provider"] == "openai"
        assert config["model_name"] == "gpt-4o"
        assert config["api_key"] == "sk-default"  # 未自定义，继承 fallback
        assert config["base_url"] == "http://default"

    @pytest.mark.asyncio
    async def test_get_model_config_all_fallback(self, db: AsyncSession):
        """未自定义模型时应全部使用 fallback。"""
        defn = SubAgentDefinition(name="test")
        fallback = {
            "provider": "compatible",
            "model_name": "qwen",
            "api_key": "sk-fb",
            "base_url": "http://fb",
        }
        config = defn.get_model_config(fallback)
        assert config == fallback

    @pytest.mark.asyncio
    async def test_get_model_config_no_fallback_reads_config_yaml(self, db: AsyncSession):
        """未传 fallback 时应自动从 config.yaml 读取。"""
        defn = SubAgentDefinition(name="test")

        fake_config = {
            "llm": {
                "provider": "compatible",
                "model": "qwen3.5-plus",
                "api_key": "sk-from-yaml",
                "base_url": "https://example.com/v1",
            }
        }
        with patch("agentpal.services.config_file.ConfigFileManager.load", return_value=fake_config):
            config = defn.get_model_config()  # 无 fallback

        assert config["provider"] == "compatible"
        assert config["model_name"] == "qwen3.5-plus"
        assert config["api_key"] == "sk-from-yaml"
        assert config["base_url"] == "https://example.com/v1"
