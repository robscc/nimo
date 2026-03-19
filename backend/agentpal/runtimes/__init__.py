"""Agent Runtime 包 — 统一的 Agent 运行时抽象层。

提供多种运行时实现：
- InternalSubAgentRuntime: 内置 SubAgent（本地执行）
- HTTPAgentRuntime: 远程 HTTP Agent 服务
- LangGraphRuntime: LangGraph 工作流引擎（预留）
"""

from agentpal.runtimes.base import (
    BaseAgentRuntime,
    ExecutionResult,
    RuntimeConfig,
    RuntimeStatus,
    ToolCall,
    ToolResult,
)
from agentpal.runtimes.http import HTTPAgentRuntime
from agentpal.runtimes.internal import InternalSubAgentRuntime
from agentpal.runtimes.registry import (
    RuntimeRegistry,
    get_runtime,
    list_available_runtimes,
    runtime_registry,
)

__all__ = [
    "BaseAgentRuntime",
    "ExecutionResult",
    "RuntimeConfig",
    "RuntimeStatus",
    "ToolCall",
    "ToolResult",
    "InternalSubAgentRuntime",
    "HTTPAgentRuntime",
    "RuntimeRegistry",
    "runtime_registry",
    "get_runtime",
    "list_available_runtimes",
]
