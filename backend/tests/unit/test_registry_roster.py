"""SubAgentRegistry.build_roster_prompt() 单元测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.database import Base
from agentpal.agents.registry import SubAgentRegistry
from agentpal.models.agent import SubAgentDefinition


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def _make_agent(
    name: str,
    display_name: str = "",
    role_prompt: str = "",
    accepted_task_types: list[str] | None = None,
    enabled: bool = True,
) -> SubAgentDefinition:
    """辅助：构造一个 SubAgentDefinition。"""
    return SubAgentDefinition(
        name=name,
        display_name=display_name or name,
        role_prompt=role_prompt,
        accepted_task_types=accepted_task_types or [],
        enabled=enabled,
    )


class TestBuildRosterPromptEmpty:
    """没有可用 SubAgent 时返回空字符串。"""

    @pytest.mark.asyncio
    async def test_no_agents_at_all(self, db: AsyncSession):
        """数据库中没有任何 SubAgent 定义。"""
        registry = SubAgentRegistry(db)
        result = await registry.build_roster_prompt()
        assert result == ""

    @pytest.mark.asyncio
    async def test_all_agents_disabled(self, db: AsyncSession):
        """所有 SubAgent 都被禁用时返回空字符串。"""
        db.add(_make_agent("researcher", enabled=False))
        db.add(_make_agent("coder", enabled=False))
        await db.flush()

        registry = SubAgentRegistry(db)
        result = await registry.build_roster_prompt()
        assert result == ""


class TestBuildRosterPromptContent:
    """启用的 SubAgent 应该出现在 roster 中。"""

    @pytest.mark.asyncio
    async def test_single_enabled_agent(self, db: AsyncSession):
        """单个启用的 SubAgent 出现在 roster 中。"""
        db.add(_make_agent(
            name="researcher",
            display_name="调研员",
            role_prompt="你是一个专注的调研员。你的职责是深度调研。\n- 信息搜集",
            accepted_task_types=["research", "summarize"],
        ))
        await db.flush()

        registry = SubAgentRegistry(db)
        result = await registry.build_roster_prompt()

        # 包含 agent 名称
        assert "researcher" in result
        assert "调研员" in result
        # 包含 task types
        assert "research" in result
        assert "summarize" in result
        # 包含 dispatch 指令
        assert "dispatch_sub_agent" in result
        assert 'agent_name="researcher"' in result

    @pytest.mark.asyncio
    async def test_multiple_enabled_agents(self, db: AsyncSession):
        """多个启用的 SubAgent 都出现在 roster 中。"""
        db.add(_make_agent(
            name="researcher",
            display_name="调研员",
            role_prompt="你是一个调研员。",
            accepted_task_types=["research"],
        ))
        db.add(_make_agent(
            name="coder",
            display_name="编码员",
            role_prompt="你是一个编码员。",
            accepted_task_types=["code", "debug"],
        ))
        await db.flush()

        registry = SubAgentRegistry(db)
        result = await registry.build_roster_prompt()

        assert "researcher" in result
        assert "coder" in result
        assert "research" in result
        assert "code" in result
        assert "debug" in result

    @pytest.mark.asyncio
    async def test_disabled_agents_excluded(self, db: AsyncSession):
        """禁用的 SubAgent 不出现在 roster 中。"""
        db.add(_make_agent(
            name="researcher",
            display_name="调研员",
            role_prompt="你是一个调研员。",
            accepted_task_types=["research"],
            enabled=True,
        ))
        db.add(_make_agent(
            name="coder",
            display_name="编码员",
            role_prompt="你是一个编码员。",
            accepted_task_types=["code"],
            enabled=False,
        ))
        await db.flush()

        registry = SubAgentRegistry(db)
        result = await registry.build_roster_prompt()

        assert "researcher" in result
        assert "coder" not in result
        assert "code" not in result

    @pytest.mark.asyncio
    async def test_role_prompt_first_line_as_summary(self, db: AsyncSession):
        """role_prompt 的首行被用作 Specialty 摘要。"""
        db.add(_make_agent(
            name="analyst",
            display_name="分析师",
            role_prompt="你是一个数据分析专家。\n- 数据清洗\n- 可视化\n- 报告撰写",
            accepted_task_types=["analyze"],
        ))
        await db.flush()

        registry = SubAgentRegistry(db)
        result = await registry.build_roster_prompt()

        # 首行出现在 Specialty 中
        assert "你是一个数据分析专家。" in result
        # 其余行不应出现（避免 context 膨胀）
        assert "数据清洗" not in result
        assert "可视化" not in result

    @pytest.mark.asyncio
    async def test_roster_contains_section_header(self, db: AsyncSession):
        """roster 包含 '## Available SubAgents' 标题和使用说明。"""
        db.add(_make_agent(
            name="researcher",
            role_prompt="调研员。",
            accepted_task_types=["research"],
        ))
        await db.flush()

        registry = SubAgentRegistry(db)
        result = await registry.build_roster_prompt()

        assert "## Available SubAgents" in result
        assert "dispatch_sub_agent" in result

    @pytest.mark.asyncio
    async def test_empty_role_prompt(self, db: AsyncSession):
        """role_prompt 为空时不崩溃，Specialty 为空。"""
        db.add(_make_agent(
            name="empty-prompt",
            display_name="空提示",
            role_prompt="",
            accepted_task_types=["misc"],
        ))
        await db.flush()

        registry = SubAgentRegistry(db)
        result = await registry.build_roster_prompt()

        assert "empty-prompt" in result
        assert "**Specialty:**" in result

    @pytest.mark.asyncio
    async def test_empty_task_types(self, db: AsyncSession):
        """accepted_task_types 为空列表时 Task types 部分为空。"""
        db.add(_make_agent(
            name="generic",
            display_name="通用",
            role_prompt="通用助手。",
            accepted_task_types=[],
        ))
        await db.flush()

        registry = SubAgentRegistry(db)
        result = await registry.build_roster_prompt()

        assert "generic" in result
        assert "**Task types:**" in result
