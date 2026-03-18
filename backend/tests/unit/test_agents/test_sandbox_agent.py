"""SandboxAgent 单元测试。

覆盖范围：
- SandboxAgent 继承关系和初始化
- 容器创建/复用/日志记录
- 沙箱工具集构建
- 系统提示词内容
- 沙箱命令执行流程
- 完整 run 流程（mock LLM + Docker）
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentpal.agents.sandbox_agent import SandboxAgent
from agentpal.agents.sub_agent import SubAgent
from agentpal.memory.buffer import BufferMemory
from agentpal.models.session import SubAgentTask, TaskStatus


def _make_task(
    task_id: str = "sandbox-task-001",
    agent_name: str = "sandbox",
    task_type: str = "sandbox",
) -> SubAgentTask:
    return SubAgentTask(
        id=task_id,
        parent_session_id="parent-session",
        sub_session_id=f"sub:parent-session:{task_id}",
        task_prompt="在沙箱中分析系统信息",
        status=TaskStatus.PENDING,
        agent_name=agent_name,
        task_type=task_type,
        retry_count=0,
        max_retries=3,
    )


def _make_sandbox_agent(
    mock_db: MagicMock,
    task_id: str = "sandbox-task-001",
    sandbox_config: dict | None = None,
    role_prompt: str = "在隔离 Docker 沙箱中执行任务",
) -> SandboxAgent:
    task = _make_task(task_id=task_id)
    memory = BufferMemory(max_size=10)
    return SandboxAgent(
        session_id=f"sub:parent-session:{task_id}",
        memory=memory,
        task=task,
        db=mock_db,
        model_config={"config_name": "test", "model_name": "test-model"},
        role_prompt=role_prompt,
        max_tool_rounds=12,
        parent_session_id="parent-session",
        sandbox_config=sandbox_config or {"image": "python:3.11-slim", "memory_limit": "512m"},
    )


@pytest.fixture
def mock_db() -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def sandbox_agent(mock_db: MagicMock) -> SandboxAgent:
    return _make_sandbox_agent(mock_db)


# ── 继承关系 ────────────────────────────────────────────────


class TestSandboxAgentInheritance:
    def test_inherits_from_sub_agent(self, sandbox_agent: SandboxAgent):
        """SandboxAgent 应继承 SubAgent。"""
        assert isinstance(sandbox_agent, SubAgent)

    def test_has_sandbox_config(self, sandbox_agent: SandboxAgent):
        """应持有 sandbox_config。"""
        assert sandbox_agent._sandbox_config is not None
        assert sandbox_agent._sandbox_config["image"] == "python:3.11-slim"

    def test_default_sandbox_config(self, mock_db):
        """sandbox_config=None 时应使用空 dict。"""
        agent = _make_sandbox_agent(mock_db, sandbox_config=None)
        # 空 dict + sandbox_config or {} → {}
        # 但 _make_sandbox_agent 传了默认值，所以这里显式传 None
        task = _make_task()
        memory = BufferMemory(max_size=10)
        agent2 = SandboxAgent(
            session_id="sub:p:t",
            memory=memory,
            task=task,
            db=mock_db,
            sandbox_config=None,
        )
        assert agent2._sandbox_config == {}

    def test_container_id_initially_none(self, sandbox_agent: SandboxAgent):
        """初始化时 container_id 应为 None。"""
        assert sandbox_agent._container_id is None

    def test_sandbox_manager_initially_none(self, sandbox_agent: SandboxAgent):
        """初始化时 sandbox_manager 应为 None。"""
        assert sandbox_agent._sandbox_manager is None


# ── 系统提示词 ──────────────────────────────────────────────


class TestSandboxSystemPrompt:
    def test_includes_sandbox_environment_info(self, sandbox_agent: SandboxAgent):
        """系统提示词应包含沙箱环境说明。"""
        prompt = sandbox_agent._build_sub_system_prompt()

        assert "Docker" in prompt
        assert "/workspace" in prompt
        assert "沙箱" in prompt

    def test_includes_base_prompt(self, sandbox_agent: SandboxAgent):
        """系统提示词应包含基类的角色和工作原则。"""
        prompt = sandbox_agent._build_sub_system_prompt()

        assert "你的角色" in prompt
        assert "工作原则" in prompt

    def test_sandbox_python_info(self, sandbox_agent: SandboxAgent):
        """应提到 Python 3.11 可用。"""
        prompt = sandbox_agent._build_sub_system_prompt()

        assert "Python" in prompt

    def test_sandbox_pip_info(self, sandbox_agent: SandboxAgent):
        """应提到可以使用 pip install。"""
        prompt = sandbox_agent._build_sub_system_prompt()

        assert "pip install" in prompt

    def test_sandbox_context_dir(self, sandbox_agent: SandboxAgent):
        """应提到 context 文件目录。"""
        prompt = sandbox_agent._build_sub_system_prompt()

        assert "/workspace/context/" in prompt

    def test_sandbox_safety_note(self, sandbox_agent: SandboxAgent):
        """应提到沙箱安全说明。"""
        prompt = sandbox_agent._build_sub_system_prompt()

        assert "不会影响宿主机" in prompt


# ── 容器创建 ────────────────────────────────────────────────


class TestEnsureContainer:
    @pytest.mark.asyncio
    async def test_creates_container_on_first_call(self, sandbox_agent: SandboxAgent):
        """首次调用应创建容器。"""
        mock_manager = MagicMock()
        mock_manager.create_or_get = AsyncMock(return_value="container-new-123")

        with (
            patch("agentpal.agents.sandbox_agent.SandboxManager", return_value=mock_manager),
            patch.object(sandbox_agent, "_load_workspace_files", new_callable=AsyncMock, return_value={}),
        ):
            container_id = await sandbox_agent._ensure_container()

        assert container_id == "container-new-123"
        assert sandbox_agent._container_id == "container-new-123"
        mock_manager.create_or_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_reuses_container_on_second_call(self, sandbox_agent: SandboxAgent):
        """第二次调用应复用已有容器。"""
        sandbox_agent._container_id = "already-created-456"

        container_id = await sandbox_agent._ensure_container()

        assert container_id == "already-created-456"

    @pytest.mark.asyncio
    async def test_logs_sandbox_created(self, sandbox_agent: SandboxAgent):
        """容器创建时应记录日志。"""
        mock_manager = MagicMock()
        mock_manager.create_or_get = AsyncMock(return_value="container-log-789")

        with (
            patch("agentpal.agents.sandbox_agent.SandboxManager", return_value=mock_manager),
            patch.object(sandbox_agent, "_load_workspace_files", new_callable=AsyncMock, return_value={"SOUL.md": "# Soul"}),
        ):
            await sandbox_agent._ensure_container()

        log_types = [e["type"] for e in sandbox_agent._execution_log]
        assert "sandbox_created" in log_types

        sandbox_log = next(e for e in sandbox_agent._execution_log if e["type"] == "sandbox_created")
        assert "container_id" in sandbox_log
        assert sandbox_log["image"] == "python:3.11-slim"
        assert "SOUL.md" in sandbox_log["workspace_files"]

    @pytest.mark.asyncio
    async def test_passes_sandbox_config_to_manager(self, mock_db):
        """应将 sandbox_config 的 image 和 memory_limit 传给 SandboxManager。"""
        agent = _make_sandbox_agent(
            mock_db,
            sandbox_config={"image": "ubuntu:22.04", "memory_limit": "1g"},
        )

        with (
            patch("agentpal.agents.sandbox_agent.SandboxManager") as MockManager,
            patch.object(agent, "_load_workspace_files", new_callable=AsyncMock, return_value={}),
        ):
            mock_instance = MagicMock()
            mock_instance.create_or_get = AsyncMock(return_value="c-config")
            MockManager.return_value = mock_instance

            await agent._ensure_container()

            MockManager.assert_called_once_with(
                image="ubuntu:22.04",
                memory_limit="1g",
            )


# ── 工具集构建 ──────────────────────────────────────────────


class TestSandboxToolkit:
    @pytest.mark.asyncio
    async def test_build_toolkit_creates_sandbox_tools(self, sandbox_agent: SandboxAgent):
        """_build_toolkit 应创建沙箱工具集。"""
        sandbox_agent._container_id = "c-toolkit-test"
        sandbox_agent._sandbox_manager = MagicMock()

        mock_toolkit = MagicMock()

        with (
            patch("agentpal.sandbox.tools.create_sandbox_tools") as mock_create,
            patch("agentpal.tools.registry.build_toolkit", return_value=mock_toolkit) as mock_build,
        ):
            mock_create.return_value = [{"name": "execute_shell_command", "func": lambda: None}]
            result = await sandbox_agent._build_toolkit()

        mock_create.assert_called_once_with(
            manager=sandbox_agent._sandbox_manager,
            container_id="c-toolkit-test",
        )
        assert result == mock_toolkit

    @pytest.mark.asyncio
    async def test_build_toolkit_ensures_container(self, sandbox_agent: SandboxAgent):
        """_build_toolkit 应先调用 _ensure_container。"""
        mock_manager = MagicMock()
        mock_manager.create_or_get = AsyncMock(return_value="c-ensure")

        with (
            patch("agentpal.agents.sandbox_agent.SandboxManager", return_value=mock_manager),
            patch.object(sandbox_agent, "_load_workspace_files", new_callable=AsyncMock, return_value={}),
            patch("agentpal.sandbox.tools.create_sandbox_tools", return_value=[]),
            patch("agentpal.tools.registry.build_toolkit", return_value=MagicMock()),
        ):
            await sandbox_agent._build_toolkit()

        assert sandbox_agent._container_id == "c-ensure"


# ── Workspace 文件加载 ──────────────────────────────────────


class TestLoadWorkspaceFiles:
    @pytest.mark.asyncio
    async def test_loads_existing_files(self, sandbox_agent: SandboxAgent, tmp_path):
        """应加载存在的 workspace 文件。"""
        # 创建临时 workspace 目录
        soul_md = tmp_path / "SOUL.md"
        soul_md.write_text("# Soul\nBe helpful")
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Agents\nresearcher, coder")

        with patch("agentpal.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(workspace_dir=str(tmp_path))
            files = await sandbox_agent._load_workspace_files()

        assert "SOUL.md" in files
        assert "AGENTS.md" in files
        assert "Be helpful" in files["SOUL.md"]

    @pytest.mark.asyncio
    async def test_skips_missing_files(self, sandbox_agent: SandboxAgent, tmp_path):
        """不存在的文件应跳过。"""
        with patch("agentpal.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(workspace_dir=str(tmp_path))
            files = await sandbox_agent._load_workspace_files()

        assert len(files) == 0


# ── 完整 run 流程 ──────────────────────────────────────────


class TestSandboxAgentRun:
    @pytest.mark.asyncio
    async def test_run_success(self, sandbox_agent: SandboxAgent):
        """run 成功时状态变为 DONE。"""
        with patch.object(sandbox_agent, "reply", new_callable=AsyncMock, return_value="系统分析报告完成"):
            result = await sandbox_agent.run("分析系统信息")

        assert result == "系统分析报告完成"
        assert sandbox_agent._task.status == TaskStatus.DONE

    @pytest.mark.asyncio
    async def test_run_failure(self, sandbox_agent: SandboxAgent):
        """run 失败时进入重试。"""
        with (
            patch.object(sandbox_agent, "reply", side_effect=RuntimeError("Docker error")),
            patch("agentpal.agents.sub_agent.asyncio.create_task"),
        ):
            result = await sandbox_agent.run("分析系统信息")

        assert result == ""
        assert sandbox_agent._task.status == TaskStatus.PENDING  # 进入重试

    @pytest.mark.asyncio
    async def test_max_tool_rounds_is_12(self, sandbox_agent: SandboxAgent):
        """sandbox agent 默认 max_tool_rounds=12。"""
        assert sandbox_agent._max_tool_rounds == 12
