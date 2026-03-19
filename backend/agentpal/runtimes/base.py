"""BaseAgentRuntime — Agent 运行时抽象基类。

定义统一的 Agent 运行时接口，支持多种 Agent 提供者：
- InternalSubAgentRuntime: 内置 SubAgent（本地执行）
- HTTPAgentRuntime: 远程 HTTP Agent 服务（如 pi-mono/OpenClaw）
- LangGraphRuntime: LangGraph 工作流引擎

核心概念：
- Runtime: 负责 Agent 的生命周期管理和执行
- 每个 Runtime 持有独立的 session/memory/db 上下文
- 支持流式和非流式两种执行模式
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator


class RuntimeStatus(Enum):
    """运行时状态。"""

    IDLE = "idle"  # 空闲，可接受新任务
    RUNNING = "running"  # 正在执行任务
    PAUSED = "paused"  # 暂停（等待输入或其他事件）
    ERROR = "error"  # 错误状态


@dataclass
class RuntimeConfig:
    """运行时配置。

    Attributes:
        runtime_type: 运行时类型标识符（如 "internal", "http", "langgraph"）
        model_config: 模型配置 dict
        max_tool_rounds: 最大工具调用轮次
        timeout_seconds: 执行超时（秒）
        extra: 额外配置参数
    """

    runtime_type: str
    model_config: dict[str, Any] | None = None
    max_tool_rounds: int = 16
    timeout_seconds: float = 300.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """执行结果。

    Attributes:
        success: 是否成功
        output: 输出文本
        error: 错误信息（如果有）
        metadata: 元数据（token 用量、执行时间等）
    """

    success: bool
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseAgentRuntime(ABC):
    """Agent 运行时抽象基类。

    职责：
    1. 管理 Agent 生命周期（初始化、执行、清理）
    2. 提供统一的执行接口（同步/流式）
    3. 处理状态转换和错误恢复
    4. 支持取消操作

    子类需要实现：
    - _initialize(): 初始化运行时
    - _execute_core(): 核心执行逻辑
    - _stream_core(): 流式执行逻辑
    - _cleanup(): 清理资源
    - _cancel(): 取消当前执行
    """

    def __init__(
        self,
        session_id: str,
        config: RuntimeConfig,
        db: Any | None = None,
        memory: Any | None = None,
        parent_session_id: str | None = None,
    ) -> None:
        """初始化运行时。

        Args:
            session_id: 会话 ID
            config: 运行时配置
            db: 数据库 session（可选）
            memory: 记忆模块（可选）
            parent_session_id: 父会话 ID（用于 Agent 间通信）
        """
        self.session_id = session_id
        self.config = config
        self.db = db
        self.memory = memory
        self.parent_session_id = parent_session_id
        self._status = RuntimeStatus.IDLE
        self._current_task_id: str | None = None

    # ── 公共接口 ────────────────────────────────────────────

    @property
    def status(self) -> RuntimeStatus:
        """获取当前状态。"""
        return self._status

    @property
    def current_task_id(self) -> str | None:
        """获取当前执行的任务 ID。"""
        return self._current_task_id

    async def execute(
        self,
        task_prompt: str,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> ExecutionResult:
        """执行任务（非流式）。

        Args:
            task_prompt: 任务提示词
            task_id: 任务 ID（可选）
            **kwargs: 额外参数

        Returns:
            ExecutionResult: 执行结果
        """
        self._status = RuntimeStatus.RUNNING
        self._current_task_id = task_id

        try:
            await self._initialize()
            result = await self._execute_core(task_prompt, **kwargs)
            self._status = RuntimeStatus.IDLE
            return result
        except asyncio.CancelledError:
            self._status = RuntimeStatus.IDLE
            raise
        except Exception as e:
            self._status = RuntimeStatus.ERROR
            return ExecutionResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
            )
        finally:
            await self._cleanup()
            self._current_task_id = None

    async def stream(
        self,
        task_prompt: str,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式执行任务。

        Args:
            task_prompt: 任务提示词
            task_id: 任务 ID（可选）
            **kwargs: 额外参数

        Yields:
            SSE 事件 dict，包含：
            - {"type": "thinking_delta", "delta": "..."}
            - {"type": "tool_start", "id": "...", "name": "...", "input": {...}}
            - {"type": "tool_done", "id": "...", "output": "..."}
            - {"type": "text_delta", "delta": "..."}
            - {"type": "done", "result": "..."}
            - {"type": "error", "message": "..."}
        """
        self._status = RuntimeStatus.RUNNING
        self._current_task_id = task_id

        try:
            await self._initialize()
            async for event in self._stream_core(task_prompt, **kwargs):
                yield event
            self._status = RuntimeStatus.IDLE
        except asyncio.CancelledError:
            self._status = RuntimeStatus.IDLE
            raise
        except Exception as e:
            self._status = RuntimeStatus.ERROR
            yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
        finally:
            await self._cleanup()
            self._current_task_id = None

    async def cancel(self) -> None:
        """取消当前执行。"""
        if self._status == RuntimeStatus.RUNNING:
            await self._cancel()
            self._status = RuntimeStatus.IDLE

    # ── 抽象方法（子类必须实现） ─────────────────────────────

    @abstractmethod
    async def _initialize(self) -> None:
        """初始化运行时。

        子类在此处进行资源初始化，如：
        - 加载模型
        - 建立连接
        - 准备工具集
        """

    @abstractmethod
    async def _execute_core(self, task_prompt: str, **kwargs: Any) -> ExecutionResult:
        """核心执行逻辑（非流式）。

        Args:
            task_prompt: 任务提示词
            **kwargs: 额外参数

        Returns:
            ExecutionResult: 执行结果
        """

    @abstractmethod
    def _stream_core(
        self, task_prompt: str, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        """核心流式执行逻辑。

        Args:
            task_prompt: 任务提示词
            **kwargs: 额外参数

        Yields:
            SSE 事件 dict
        """

    @abstractmethod
    async def _cleanup(self) -> None:
        """清理资源。

        子类在此处释放资源，如：
        - 关闭连接
        - 释放内存
        - 保存状态
        """

    @abstractmethod
    async def _cancel(self) -> None:
        """取消当前执行。

        子类实现优雅的中断逻辑。
        """

    # ── 辅助方法 ────────────────────────────────────────────

    def _log(self, event_type: str, data: dict[str, Any]) -> None:
        """记录执行日志（可选，子类可重写）。

        Args:
            event_type: 事件类型
            data: 事件数据
        """
        # 默认实现：打印到控制台
        print(f"[{self.session_id}] {event_type}: {data}")


# ── 工具调用相关数据结构 ───────────────────────────────────


@dataclass
class ToolCall:
    """工具调用请求。

    Attributes:
        id: 调用 ID
        name: 工具名称
        arguments: 参数字典
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """工具调用结果。

    Attributes:
        id: 调用 ID
        output: 输出内容
        error: 错误信息（如果有）
        duration_ms: 执行时长（毫秒）
    """

    id: str
    output: str = ""
    error: str | None = None
    duration_ms: float = 0.0
