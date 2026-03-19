"""Runtime Registry — Agent 运行时注册表和工厂。

提供运行时类型的注册、查找和创建功能，
支持通过配置文件动态切换运行时实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Type

from loguru import logger

from agentpal.runtimes.base import BaseAgentRuntime, RuntimeConfig


@dataclass
class RuntimeDescriptor:
    """运行时描述符。

    Attributes:
        name: 运行时名称（如 "internal", "http"）
        runtime_class: 运行时类
        description: 描述信息
        config_schema: 配置 Schema（JSON Schema 格式）
    """

    name: str
    runtime_class: Type[BaseAgentRuntime]
    description: str = ""
    config_schema: dict[str, Any] = field(default_factory=dict)


class RuntimeRegistry:
    """运行时注册表（单例）。

    用法：
        # 注册运行时
        registry.register("internal", InternalSubAgentRuntime, "内置 SubAgent")

        # 获取运行时
        runtime = registry.create("internal", session_id="xxx", config=...)
    """

    _instance: RuntimeRegistry | None = None
    _runtimes: dict[str, RuntimeDescriptor]

    def __new__(cls) -> RuntimeRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._runtimes = {}
        return cls._instance

    def register(
        self,
        name: str,
        runtime_class: Type[BaseAgentRuntime],
        description: str = "",
        config_schema: dict[str, Any] | None = None,
    ) -> None:
        """注册运行时类型。

        Args:
            name: 运行时名称
            runtime_class: 运行时类
            description: 描述信息
            config_schema: 配置 Schema
        """
        self._runtimes[name] = RuntimeDescriptor(
            name=name,
            runtime_class=runtime_class,
            description=description,
            config_schema=config_schema or {},
        )
        logger.info(f"Registered runtime: {name}")

    def unregister(self, name: str) -> None:
        """注销运行时类型。

        Args:
            name: 运行时名称
        """
        if name in self._runtimes:
            del self._runtimes[name]
            logger.info(f"Unregistered runtime: {name}")

    def get(self, name: str) -> RuntimeDescriptor | None:
        """获取运行时描述符。

        Args:
            name: 运行时名称

        Returns:
            运行时描述符，不存在则返回 None
        """
        return self._runtimes.get(name)

    def list_runtimes(self) -> list[dict[str, Any]]:
        """列出所有已注册的运行时。

        Returns:
            运行时信息列表
        """
        return [
            {
                "name": desc.name,
                "description": desc.description,
                "config_schema": desc.config_schema,
            }
            for desc in self._runtimes.values()
        ]

    def create(
        self,
        name: str,
        session_id: str,
        config: RuntimeConfig,
        **kwargs: Any,
    ) -> BaseAgentRuntime:
        """创建运行时实例。

        Args:
            name: 运行时名称
            session_id: 会话 ID
            config: 运行时配置
            **kwargs: 额外参数

        Returns:
            运行时实例

        Raises:
            ValueError: 运行时未注册
        """
        descriptor = self.get(name)
        if descriptor is None:
            available = ", ".join(self._runtimes.keys())
            raise ValueError(
                f"Unknown runtime '{name}'. Available: {available}"
            )

        logger.info(f"Creating runtime: {name} for session {session_id}")

        return descriptor.runtime_class(
            session_id=session_id,
            config=config,
            **kwargs,
        )

    def exists(self, name: str) -> bool:
        """检查运行时是否已注册。

        Args:
            name: 运行时名称

        Returns:
            是否存在
        """
        return name in self._runtimes


# ── 全局注册表实例 ─────────────────────────────────────────

runtime_registry = RuntimeRegistry()


# ── 自动注册内置运行时 ────────────────────────────────────

def _register_builtin_runtimes() -> None:
    """注册内置运行时。"""
    # InternalSubAgentRuntime
    try:
        from agentpal.runtimes.internal import InternalSubAgentRuntime

        runtime_registry.register(
            name="internal",
            runtime_class=InternalSubAgentRuntime,
            description="Built-in SubAgent runtime (local execution)",
            config_schema={
                "type": "object",
                "properties": {
                    "max_tool_rounds": {
                        "type": "integer",
                        "default": 16,
                        "description": "Maximum tool call rounds",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "default": 300,
                        "description": "Execution timeout in seconds",
                    },
                },
            },
        )
    except ImportError as e:
        logger.warning(f"Failed to register internal runtime: {e}")

    # HTTPAgentRuntime
    try:
        from agentpal.runtimes.http import HTTPAgentRuntime

        runtime_registry.register(
            name="http",
            runtime_class=HTTPAgentRuntime,
            description="Remote HTTP Agent service (pi-mono, OpenClaw, etc.)",
            config_schema={
                "type": "object",
                "properties": {
                    "base_url": {
                        "type": "string",
                        "description": "Base URL of the remote service",
                    },
                    "api_key": {
                        "type": "string",
                        "description": "API key for authentication",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "default": 300,
                        "description": "Request timeout in seconds",
                    },
                },
                "required": ["base_url"],
            },
        )
    except ImportError as e:
        logger.warning(f"Failed to register http runtime: {e}")


# 自动注册
_register_builtin_runtimes()


# ── 便捷函数 ──────────────────────────────────────────────

def get_runtime(
    runtime_type: str,
    session_id: str,
    config: RuntimeConfig | None = None,
    **kwargs: Any,
) -> BaseAgentRuntime:
    """获取运行时实例的便捷函数。

    Args:
        runtime_type: 运行时类型
        session_id: 会话 ID
        config: 运行时配置
        **kwargs: 额外参数

    Returns:
        运行时实例
    """
    if config is None:
        config = RuntimeConfig(runtime_type=runtime_type)

    return runtime_registry.create(
        name=runtime_type,
        session_id=session_id,
        config=config,
        **kwargs,
    )


def list_available_runtimes() -> list[dict[str, Any]]:
    """列出所有可用的运行时。

    Returns:
        运行时信息列表
    """
    return runtime_registry.list_runtimes()
