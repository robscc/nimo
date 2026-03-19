"""Agent Runtime 集成测试。

验证运行时架构与现有系统的集成：
- InternalSubAgentRuntime 与数据库会话的集成
- dispatch_sub_agent 使用新运行时架构
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from agentpal.runtimes.base import ExecutionResult, RuntimeConfig
from agentpal.runtimes.internal import InternalSubAgentRuntime


@pytest.mark.asyncio
class TestInternalSubAgentRuntimeIntegration:
    """InternalSubAgentRuntime 与数据库集成测试。"""

    @pytest.fixture
    async def db_session(self):
        """创建异步数据库会话。"""
        from agentpal.database import AsyncSessionLocal, init_db

        # 确保数据库已初始化
        await init_db()

        async with AsyncSessionLocal() as db:
            yield db

    @pytest.fixture
    def runtime_config(self):
        """创建运行时配置。"""
        return RuntimeConfig(
            runtime_type="internal",
            model_config={"model": "claude-sonnet-4-5-20250929"},
            max_tool_rounds=5,
            timeout_seconds=60.0,
        )

    async def test_runtime_initialization_with_db(
        self,
        db_session,
        runtime_config,
    ):
        """运行时应该能够用数据库会话正确初始化。"""
        from agentpal.models.session import SubAgentTask, TaskStatus

        # 创建一个测试任务
        task = SubAgentTask(
            id="test-task-integration",
            parent_session_id="test-parent",
            sub_session_id="sub:test-parent:integration",
            task_prompt="Test task prompt",
            status=TaskStatus.PENDING,
            agent_name=None,
        )
        db_session.add(task)
        await db_session.commit()

        # 创建运行时
        runtime = InternalSubAgentRuntime(
            session_id=task.sub_session_id,
            config=runtime_config,
            db=db_session,
            parent_session_id=task.parent_session_id,
            task=task,
        )

        # 初始化和清理都应该正常工作
        await runtime._initialize()
        assert runtime._status.value == "idle"
        assert runtime._sub_agent is not None

        await runtime._cleanup()

    async def test_runtime_execute_with_mocked_subagent(
        self,
        db_session,
        runtime_config,
    ):
        """运行时 execute 方法应与 Mock SubAgent 协同工作。"""
        from agentpal.models.session import SubAgentTask, TaskStatus

        task = SubAgentTask(
            id="test-execute-task",
            parent_session_id="test-parent",
            sub_session_id="sub:test-parent:execute",
            task_prompt="Analyze this data",
            status=TaskStatus.PENDING,
            agent_name="researcher",
        )
        db_session.add(task)
        await db_session.commit()

        runtime = InternalSubAgentRuntime(
            session_id=task.sub_session_id,
            config=runtime_config,
            db=db_session,
            parent_session_id=task.parent_session_id,
            task=task,
        )

        await runtime._initialize()

        # Mock _execute_core 方法（因为真实的执行需要 API key）
        with patch.object(runtime, '_execute_core', new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = ExecutionResult(
                success=True,
                output="Analysis complete",
                metadata={"rounds": 3},
            )

            result = await runtime.execute("Analyze this data")

            assert result.success is True
            assert result.output == "Analysis complete"
            assert result.metadata["rounds"] == 3

        await runtime._cleanup()


class TestDispatchSubAgentWithRuntime:
    """dispatch_sub_agent 与新运行时架构集成测试。"""

    @pytest.mark.asyncio
    async def test_dispatch_uses_runtime_registry(self):
        """dispatch_sub_agent 应通过 runtime_registry 获取运行时。"""
        from agentpal.tools.builtin import dispatch_sub_agent
        from agentpal.runtimes.registry import runtime_registry

        # 验证 internal 运行时已注册
        assert runtime_registry.exists("internal") is True

        # 验证可以通过 get_runtime 获取 internal 运行时
        from agentpal.runtimes.internal import InternalSubAgentRuntime
        from agentpal.runtimes.base import RuntimeConfig

        with patch("agentpal.database.AsyncSessionLocal", autospec=True):
            config = RuntimeConfig(runtime_type="internal")
            runtime = runtime_registry.create(
                name="internal",
                session_id="test-session",
                config=config,
                db=MagicMock(),
                parent_session_id="parent",
                task=MagicMock(),
            )
            assert isinstance(runtime, InternalSubAgentRuntime)

    def test_dispatch_signature_accepts_runtime_params(self):
        """dispatch_sub_agent 函数签名应接受运行时相关参数。"""
        import inspect
        from agentpal.tools.builtin import dispatch_sub_agent

        sig = inspect.signature(dispatch_sub_agent)
        params = list(sig.parameters.keys())

        assert "runtime_type" in params
        assert "runtime_config" in params

        # 验证默认值
        assert sig.parameters["runtime_type"].default == "internal"
        assert sig.parameters["runtime_config"].default is None
