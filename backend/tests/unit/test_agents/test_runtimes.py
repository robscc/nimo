"""Agent Runtime 单元测试。

覆盖范围:
- BaseAgentRuntime 抽象基类接口
- RuntimeConfig 和 ExecutionResult 数据类
- RuntimeStatus 枚举
- RuntimeRegistry 注册表
- get_runtime 便捷函数
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentpal.runtimes.base import (
    BaseAgentRuntime,
    RuntimeConfig,
    RuntimeStatus,
    ExecutionResult,
)
from agentpal.runtimes.registry import (
    RuntimeDescriptor,
    runtime_registry,
    get_runtime,
    list_available_runtimes,
)


# ── BaseAgentRuntime 抽象基类 ─────────────────────────────────────


class TestBaseAgentRuntime:
    """BaseAgentRuntime 抽象基类接口测试。"""

    def test_abstract_methods_defined(self):
        """BaseAgentRuntime 定义了必要的抽象方法。"""
        from abc import ABC

        assert issubclass(BaseAgentRuntime, ABC)

        # 验证抽象方法存在
        assert hasattr(BaseAgentRuntime, '_initialize')
        assert hasattr(BaseAgentRuntime, '_execute_core')
        assert hasattr(BaseAgentRuntime, '_stream_core')
        assert hasattr(BaseAgentRuntime, '_cleanup')
        assert hasattr(BaseAgentRuntime, '_cancel')

    def test_cannot_instantiate_abstract_base(self):
        """不能直接实例化 BaseAgentRuntime。"""
        with pytest.raises(TypeError):
            BaseAgentRuntime(
                session_id="test",
                config=RuntimeConfig(runtime_type="test"),
            )

    def test_concrete_class_can_extend(self):
        """具体子类可以实现抽象方法。"""

        class ConcreteRuntime(BaseAgentRuntime):
            async def _initialize(self) -> None:
                self._status = RuntimeStatus.IDLE

            async def _execute_core(self, task_prompt: str, **kwargs):
                return ExecutionResult(success=True, output="done")

            async def _stream_core(self, task_prompt: str, **kwargs):
                yield {"type": "complete", "data": "done"}

            async def _cleanup(self) -> None:
                pass

            async def _cancel(self) -> None:
                self._status = RuntimeStatus.IDLE

        config = RuntimeConfig(runtime_type="concrete")
        runtime = ConcreteRuntime(session_id="test", config=config)

        assert runtime.session_id == "test"
        assert runtime.config.runtime_type == "concrete"


# ── RuntimeConfig 数据类 ──────────────────────────────────────────


class TestRuntimeConfig:
    """RuntimeConfig 数据类测试。"""

    def test_minimal_config(self):
        """最小配置。"""
        config = RuntimeConfig(runtime_type="internal")

        assert config.runtime_type == "internal"
        # model_config 和 extra 默认为空 dict，max_tool_rounds 和 timeout_seconds 有默认值
        assert config.model_config is None or config.model_config == {}
        assert isinstance(config.max_tool_rounds, int)  # 有默认值
        assert isinstance(config.timeout_seconds, float)  # 有默认值

    def test_full_config(self):
        """完整配置。"""
        config = RuntimeConfig(
            runtime_type="http",
            model_config={"model": "claude-sonnet-4-5-20250929"},
            max_tool_rounds=10,
            timeout_seconds=600.0,
            extra={"base_url": "http://localhost:8000"},
        )

        assert config.runtime_type == "http"
        assert config.model_config["model"] == "claude-sonnet-4-5-20250929"
        assert config.max_tool_rounds == 10
        assert config.timeout_seconds == 600.0
        assert config.extra["base_url"] == "http://localhost:8000"


# ── ExecutionResult 数据类 ────────────────────────────────────────


class TestExecutionResult:
    """ExecutionResult 数据类测试。"""

    def test_success_result(self):
        """成功的执行结果。"""
        result = ExecutionResult(
            success=True,
            output="任务完成",
            metadata={"elapsed": 1.5},
        )

        assert result.success is True
        assert result.output == "任务完成"
        assert result.metadata["elapsed"] == 1.5
        assert result.error is None

    def test_error_result(self):
        """失败的执行结果。"""
        result = ExecutionResult(
            success=False,
            error="Something went wrong",
        )

        assert result.success is False
        assert result.output == ""
        assert result.error == "Something went wrong"
        assert result.metadata == {}

    def test_default_values(self):
        """默认值。"""
        result = ExecutionResult(success=False)

        assert result.success is False
        assert result.output == ""
        assert result.error is None
        assert result.metadata == {}


# ── RuntimeStatus 枚举 ────────────────────────────────────────────


class TestRuntimeStatus:
    """RuntimeStatus 枚举测试。"""

    def test_status_values(self):
        """验证状态枚举值。"""
        assert RuntimeStatus.IDLE.value == "idle"
        assert RuntimeStatus.RUNNING.value == "running"
        assert RuntimeStatus.PAUSED.value == "paused"
        assert RuntimeStatus.ERROR.value == "error"


# ── RuntimeDescriptor ─────────────────────────────────────────────


class TestRuntimeDescriptor:
    """RuntimeDescriptor 数据类测试。"""

    def test_descriptor_creation(self):
        """RuntimeDescriptor 可以正常创建。"""
        mock_class = MagicMock()
        desc = RuntimeDescriptor(
            name="test-runtime",
            runtime_class=mock_class,
            description="Test runtime",
        )

        assert desc.name == "test-runtime"
        assert desc.runtime_class is mock_class
        assert desc.description == "Test runtime"


# ── RuntimeRegistry ───────────────────────────────────────────────


class TestRuntimeRegistry:
    """运行时注册表测试。"""

    @pytest.fixture
    def clean_registry(self):
        """每个测试前清理注册表。"""
        # 保存原有注册表
        saved = runtime_registry._runtimes.copy()
        runtime_registry._runtimes.clear()
        yield
        # 恢复原有注册表
        runtime_registry._runtimes.clear()
        runtime_registry._runtimes.update(saved)

    def test_register_runtime(self, clean_registry):
        """register 应成功添加运行时。"""
        mock_runtime_class = MagicMock(spec=type)

        runtime_registry.register(
            name="mock-runtime",
            runtime_class=mock_runtime_class,
            description="Mock runtime for testing",
        )

        descriptor = runtime_registry.get("mock-runtime")
        assert descriptor is not None
        assert descriptor.name == "mock-runtime"
        assert descriptor.runtime_class is mock_runtime_class

    def test_unregister_runtime(self, clean_registry):
        """unregister 应移除运行时。"""
        mock_runtime_class = MagicMock(spec=type)
        runtime_registry.register("temp-runtime", mock_runtime_class)

        runtime_registry.unregister("temp-runtime")

        assert runtime_registry.get("temp-runtime") is None

    def test_get_existing_runtime(self, clean_registry):
        """get 应返回已注册的运行时描述符。"""
        mock_runtime_class = MagicMock(spec=type)
        runtime_registry.register("test-runtime", mock_runtime_class)

        result = runtime_registry.get("test-runtime")

        assert result is not None
        assert result.name == "test-runtime"

    def test_get_nonexistent_runtime_returns_none(self, clean_registry):
        """get 未注册的运行时应返回 None。"""
        result = runtime_registry.get("non-existent-runtime")

        assert result is None

    def test_list_runtimes(self, clean_registry):
        """list_runtimes 应返回所有已注册的运行时信息。"""
        mock_runtime1 = MagicMock(spec=type)
        mock_runtime2 = MagicMock(spec=type)

        runtime_registry.register("runtime-a", mock_runtime1, description="First")
        runtime_registry.register("runtime-b", mock_runtime2, description="Second")

        result = runtime_registry.list_runtimes()

        assert len(result) == 2
        names = [r["name"] for r in result]
        assert "runtime-a" in names
        assert "runtime-b" in names

    def test_exists_method(self, clean_registry):
        """exists 应检查运行时是否已注册。"""
        mock_runtime = MagicMock(spec=type)
        runtime_registry.register("check-runtime", mock_runtime)

        assert runtime_registry.exists("check-runtime") is True
        assert runtime_registry.exists("unknown") is False

    def test_create_runtime(self, clean_registry):
        """create 应创建运行时实例。"""
        mock_instance = MagicMock()
        mock_runtime_class = MagicMock(return_value=mock_instance)
        runtime_registry.register("factory-test", mock_runtime_class)

        config = RuntimeConfig(runtime_type="factory-test")
        result = runtime_registry.create(
            name="factory-test",
            session_id="test-session",
            config=config,
        )

        assert result is mock_instance
        mock_runtime_class.assert_called_once()

    def test_create_unknown_runtime_raises_value_error(self, clean_registry):
        """create 未知运行时应抛出 ValueError。"""
        config = RuntimeConfig(runtime_type="unknown")

        with pytest.raises(ValueError, match="Unknown runtime"):
            runtime_registry.create(
                name="unknown-runtime",
                session_id="test-session",
                config=config,
            )

    def test_auto_registration_on_import(self):
        """导入模块时应自动注册内置运行时。"""
        # internal 和 http 已在 registry.py 导入时自动注册
        assert runtime_registry.exists("internal") is True
        assert runtime_registry.exists("http") is True


# ── get_runtime 便捷函数 ──────────────────────────────────────────


class TestGetRuntime:
    """get_runtime 便捷函数测试。"""

    def test_get_internal_runtime(self):
        """get_runtime('internal') 应返回 InternalSubAgentRuntime 实例。"""
        from agentpal.runtimes.internal import InternalSubAgentRuntime

        with patch("agentpal.database.AsyncSessionLocal", autospec=True):
            config = RuntimeConfig(runtime_type="internal")
            runtime = get_runtime(
                runtime_type="internal",
                session_id="test-session",
                config=config,
            )

            assert isinstance(runtime, InternalSubAgentRuntime)
            assert runtime.session_id == "test-session"

    def test_get_unknown_runtime_raises_value_error(self):
        """get_runtime 未知运行时应抛出 ValueError。"""
        config = RuntimeConfig(runtime_type="unknown")

        with pytest.raises(ValueError, match="Unknown runtime"):
            get_runtime(
                runtime_type="unknown",
                session_id="test-session",
                config=config,
            )


# ── list_available_runtimes 便捷函数 ──────────────────────────────


class TestListAvailableRuntimes:
    """list_available_runtimes 便捷函数测试。"""

    def test_returns_list_of_dicts(self):
        """应返回字典列表。"""
        result = list_available_runtimes()

        assert isinstance(result, list)
        # 至少应有 internal 运行时
        names = [r["name"] for r in result]
        assert "internal" in names


# ── HTTPAgentRuntime 基础测试 ─────────────────────────────────────


class TestHTTPAgentRuntimeBasics:
    """HTTPAgentRuntime 基础测试。"""

    def test_import_does_not_fail(self):
        """HTTPAgentRuntime 应该可以导入。"""
        from agentpal.runtimes.http import HTTPAgentRuntime

        assert HTTPAgentRuntime is not None

    def test_class_extends_base(self):
        """HTTPAgentRuntime 应继承 BaseAgentRuntime。"""
        from agentpal.runtimes.http import HTTPAgentRuntime

        assert issubclass(HTTPAgentRuntime, BaseAgentRuntime)


# ── InternalSubAgentRuntime 基础测试 ──────────────────────────────


class TestInternalSubAgentRuntimeBasics:
    """InternalSubAgentRuntime 基础测试。"""

    def test_import_does_not_fail(self):
        """InternalSubAgentRuntime 应该可以导入。"""
        from agentpal.runtimes.internal import InternalSubAgentRuntime

        assert InternalSubAgentRuntime is not None

    def test_class_extends_base(self):
        """InternalSubAgentRuntime 应继承 BaseAgentRuntime。"""
        from agentpal.runtimes.internal import InternalSubAgentRuntime

        assert issubclass(InternalSubAgentRuntime, BaseAgentRuntime)
