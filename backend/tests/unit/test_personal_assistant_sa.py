"""PersonalAssistant SubAgent 上下文方法单元测试。

测试 _get_effective_sub_agent_mode()、_check_mention_directive() 和 SubAgentContext 数据类。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.agents.personal_assistant import PersonalAssistant, SubAgentContext
from agentpal.database import Base
from agentpal.models.agent import SubAgentDefinition


# ── Fixtures ──────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """创建内存 SQLite 数据库，包含所有 ORM 表。"""
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


def _make_mock_session(sub_agent_mode: str | None = None) -> MagicMock:
    """创建一个 mock SessionRecord，只需 sub_agent_mode 属性。"""
    session = MagicMock()
    session.sub_agent_mode = sub_agent_mode
    return session


def _make_pa(db: AsyncSession | None = None) -> PersonalAssistant:
    """创建一个最小化的 PersonalAssistant 实例用于方法测试。

    通过 patch 跳过 __init__ 中对 workspace / config 的依赖。
    """
    with patch("agentpal.agents.personal_assistant.get_settings") as mock_settings, \
         patch("agentpal.agents.personal_assistant.WorkspaceManager"), \
         patch("agentpal.agents.personal_assistant.MemoryWriter"):
        settings = MagicMock()
        settings.workspace_dir = "/tmp/test-nimo"
        settings.sub_agent_mode = "auto"
        mock_settings.return_value = settings
        memory = MagicMock()
        pa = PersonalAssistant(
            session_id="test-session-001",
            memory=memory,
            model_config={
                "provider": "compatible",
                "model_name": "test-model",
                "api_key": "sk-test",
                "base_url": "http://localhost:1234",
            },
            db=db,
        )
    return pa


# ── SubAgentContext 数据类测试 ──────────────────────────────────


class TestSubAgentContext:
    """SubAgentContext dataclass 基本行为。"""

    def test_create_with_defaults(self):
        """能够正常创建 SubAgentContext 实例。"""
        ctx = SubAgentContext(
            roster_prompt="## Available SubAgents\n...",
            include_dispatch_tool=True,
            mention_agent=None,
        )
        assert ctx.roster_prompt == "## Available SubAgents\n..."
        assert ctx.include_dispatch_tool is True
        assert ctx.mention_agent is None

    def test_create_with_mention(self):
        """能够创建带 mention_agent 的 SubAgentContext。"""
        ctx = SubAgentContext(
            roster_prompt="roster content",
            include_dispatch_tool=True,
            mention_agent="researcher",
        )
        assert ctx.mention_agent == "researcher"

    def test_create_off_mode(self):
        """off 模式下的 SubAgentContext。"""
        ctx = SubAgentContext(
            roster_prompt="",
            include_dispatch_tool=False,
            mention_agent=None,
        )
        assert ctx.roster_prompt == ""
        assert ctx.include_dispatch_tool is False
        assert ctx.mention_agent is None

    def test_fields_are_accessible(self):
        """所有字段都可直接访问。"""
        ctx = SubAgentContext(
            roster_prompt="prompt",
            include_dispatch_tool=True,
            mention_agent="coder",
        )
        assert hasattr(ctx, "roster_prompt")
        assert hasattr(ctx, "include_dispatch_tool")
        assert hasattr(ctx, "mention_agent")


# ── _get_effective_sub_agent_mode() 测试 ───────────────────────


class TestGetEffectiveSubAgentMode:
    """测试 _get_effective_sub_agent_mode() — session 级覆盖 > 全局默认。"""

    def test_session_override_manual(self):
        """session 设置 manual 时返回 manual，忽略全局默认。"""
        pa = _make_pa()
        session = _make_mock_session(sub_agent_mode="manual")

        with patch("agentpal.agents.personal_assistant.get_settings") as mock_settings:
            settings = MagicMock()
            settings.sub_agent_mode = "auto"
            mock_settings.return_value = settings
            result = pa._get_effective_sub_agent_mode(session)

        assert result == "manual"

    def test_session_override_off(self):
        """session 设置 off 时返回 off。"""
        pa = _make_pa()
        session = _make_mock_session(sub_agent_mode="off")

        with patch("agentpal.agents.personal_assistant.get_settings") as mock_settings:
            settings = MagicMock()
            settings.sub_agent_mode = "auto"
            mock_settings.return_value = settings
            result = pa._get_effective_sub_agent_mode(session)

        assert result == "off"

    def test_session_override_auto(self):
        """session 显式设置 auto 时返回 auto。"""
        pa = _make_pa()
        session = _make_mock_session(sub_agent_mode="auto")

        with patch("agentpal.agents.personal_assistant.get_settings") as mock_settings:
            settings = MagicMock()
            settings.sub_agent_mode = "off"
            mock_settings.return_value = settings
            result = pa._get_effective_sub_agent_mode(session)

        assert result == "auto"

    def test_global_fallback_when_session_is_none(self):
        """session.sub_agent_mode 为 None 时回退到全局配置。"""
        pa = _make_pa()
        session = _make_mock_session(sub_agent_mode=None)

        with patch("agentpal.agents.personal_assistant.get_settings") as mock_settings:
            settings = MagicMock()
            settings.sub_agent_mode = "manual"
            mock_settings.return_value = settings
            result = pa._get_effective_sub_agent_mode(session)

        assert result == "manual"

    def test_global_fallback_auto(self):
        """session 未设置时回退到全局 auto。"""
        pa = _make_pa()
        session = _make_mock_session(sub_agent_mode=None)

        with patch("agentpal.agents.personal_assistant.get_settings") as mock_settings:
            settings = MagicMock()
            settings.sub_agent_mode = "auto"
            mock_settings.return_value = settings
            result = pa._get_effective_sub_agent_mode(session)

        assert result == "auto"

    def test_global_fallback_off(self):
        """session 未设置时回退到全局 off。"""
        pa = _make_pa()
        session = _make_mock_session(sub_agent_mode=None)

        with patch("agentpal.agents.personal_assistant.get_settings") as mock_settings:
            settings = MagicMock()
            settings.sub_agent_mode = "off"
            mock_settings.return_value = settings
            result = pa._get_effective_sub_agent_mode(session)

        assert result == "off"


# ── _check_mention_directive() 测试 ────────────────────────────


class TestCheckMentionDirective:
    """测试 _check_mention_directive() — 从用户消息中提取 @mention。"""

    @pytest.mark.asyncio
    async def test_no_mention_returns_none(self, db: AsyncSession):
        """消息中没有 @mention 时返回 None。"""
        pa = _make_pa(db=db)
        result = await pa._check_mention_directive("请帮我写一段代码")
        assert result is None

    @pytest.mark.asyncio
    async def test_mention_matches_agent_name(self, db: AsyncSession):
        """@agent_name 匹配已启用 agent 的 name 字段。"""
        db.add(_make_agent(
            name="researcher",
            display_name="调研员",
            role_prompt="你是调研员。",
            accepted_task_types=["research"],
            enabled=True,
        ))
        await db.flush()

        pa = _make_pa(db=db)
        result = await pa._check_mention_directive("@researcher 帮我调研一下 AI 趋势")
        assert result == "researcher"

    @pytest.mark.asyncio
    async def test_mention_matches_display_name(self, db: AsyncSession):
        """@display_name 匹配已启用 agent 的 display_name 字段。"""
        db.add(_make_agent(
            name="researcher",
            display_name="调研员",
            role_prompt="你是调研员。",
            accepted_task_types=["research"],
            enabled=True,
        ))
        await db.flush()

        pa = _make_pa(db=db)
        result = await pa._check_mention_directive("@调研员 帮我查一下数据")
        assert result == "researcher"

    @pytest.mark.asyncio
    async def test_mention_case_insensitive(self, db: AsyncSession):
        """@mention 匹配不区分大小写。"""
        db.add(_make_agent(
            name="Researcher",
            display_name="ResearchBot",
            role_prompt="You are a researcher.",
            accepted_task_types=["research"],
            enabled=True,
        ))
        await db.flush()

        pa = _make_pa(db=db)
        result = await pa._check_mention_directive("@researcher 查一下")
        assert result == "Researcher"

    @pytest.mark.asyncio
    async def test_mention_disabled_agent_returns_none(self, db: AsyncSession):
        """@mention 匹配到的 agent 如果是禁用状态则返回 None。"""
        db.add(_make_agent(
            name="coder",
            display_name="编码员",
            role_prompt="你是编码员。",
            accepted_task_types=["code"],
            enabled=False,
        ))
        await db.flush()

        pa = _make_pa(db=db)
        result = await pa._check_mention_directive("@coder 帮我写代码")
        assert result is None

    @pytest.mark.asyncio
    async def test_mention_nonexistent_agent_returns_none(self, db: AsyncSession):
        """@mention 不匹配任何 agent 时返回 None。"""
        db.add(_make_agent(
            name="researcher",
            display_name="调研员",
            role_prompt="你是调研员。",
            accepted_task_types=["research"],
            enabled=True,
        ))
        await db.flush()

        pa = _make_pa(db=db)
        result = await pa._check_mention_directive("@writer 帮我写文章")
        assert result is None

    @pytest.mark.asyncio
    async def test_mention_in_middle_of_message(self, db: AsyncSession):
        """@mention 在消息中间也能被识别。"""
        db.add(_make_agent(
            name="coder",
            display_name="编码员",
            role_prompt="你是编码员。",
            accepted_task_types=["code"],
            enabled=True,
        ))
        await db.flush()

        pa = _make_pa(db=db)
        result = await pa._check_mention_directive("请 @coder 帮我写一个 Python 脚本")
        assert result == "coder"

    @pytest.mark.asyncio
    async def test_mention_with_no_db_returns_none(self):
        """db 为 None 时 _check_mention_directive 返回 None。"""
        pa = _make_pa(db=None)
        result = await pa._check_mention_directive("@researcher 查一下")
        assert result is None

    @pytest.mark.asyncio
    async def test_first_mention_is_used(self, db: AsyncSession):
        """消息中有多个 @mention 时使用第一个匹配的。"""
        db.add(_make_agent(
            name="researcher",
            display_name="调研员",
            role_prompt="你是调研员。",
            accepted_task_types=["research"],
            enabled=True,
        ))
        db.add(_make_agent(
            name="coder",
            display_name="编码员",
            role_prompt="你是编码员。",
            accepted_task_types=["code"],
            enabled=True,
        ))
        await db.flush()

        pa = _make_pa(db=db)
        result = await pa._check_mention_directive("@researcher 调研后 @coder 写代码")
        assert result == "researcher"

    @pytest.mark.asyncio
    async def test_email_like_pattern_not_matched(self, db: AsyncSession):
        """邮箱格式不应误匹配（@ 后面的是域名而非 agent）。"""
        db.add(_make_agent(
            name="researcher",
            display_name="调研员",
            role_prompt="你是调研员。",
            accepted_task_types=["research"],
            enabled=True,
        ))
        await db.flush()

        pa = _make_pa(db=db)
        # 邮箱中 @ 后面是 gmail.com，不匹配任何 agent
        result = await pa._check_mention_directive("发邮件到 user@gmail.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_mention_with_empty_message(self, db: AsyncSession):
        """空消息返回 None。"""
        pa = _make_pa(db=db)
        result = await pa._check_mention_directive("")
        assert result is None
