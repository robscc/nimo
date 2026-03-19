"""InternalSubAgentRuntime — 内置 SubAgent 运行时适配器。

将现有的 SubAgent 逻辑适配到 BaseAgentRuntime 接口，
保持向后兼容的同时提供统一的运行时抽象。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator

from loguru import logger

from agentpal.agents.sub_agent import SubAgent
from agentpal.memory.buffer import BufferMemory
from agentpal.models.session import SubAgentTask, TaskStatus
from agentpal.runtimes.base import (
    BaseAgentRuntime,
    ExecutionResult,
    RuntimeConfig,
    RuntimeStatus,
)


class InternalSubAgentRuntime(BaseAgentRuntime):
    """内置 SubAgent 运行时适配器。

    封装现有的 SubAgent 类，提供统一的 Runtime 接口。
    支持：
    - 非流式执行（execute）
    - 流式执行（stream）
    - 任务取消
    - 状态管理
    """

    def __init__(
        self,
        session_id: str,
        config: RuntimeConfig,
        db: Any | None = None,
        memory: Any | None = None,
        parent_session_id: str | None = None,
        task: SubAgentTask | None = None,
        role_prompt: str = "",
    ) -> None:
        """初始化运行时。

        Args:
            session_id: 会话 ID
            config: 运行时配置
            db: 数据库 session
            memory: 记忆模块（可选，默认创建 BufferMemory）
            parent_session_id: 父会话 ID
            task: SubAgentTask 数据库记录
            role_prompt: 角色系统提示词
        """
        super().__init__(
            session_id=session_id,
            config=config,
            db=db,
            memory=memory,
            parent_session_id=parent_session_id,
        )
        self._task = task
        self._role_prompt = role_prompt
        self._sub_agent: SubAgent | None = None
        self._cancel_flag = False

    async def _initialize(self) -> None:
        """初始化 SubAgent。"""
        if self.db is None:
            raise RuntimeError("Database session is required for InternalSubAgentRuntime")

        # 如果没有传入 task，创建一个临时的
        if self._task is None:
            self._task = SubAgentTask(
                id=f"temp_{self.session_id}",
                parent_session_id=self.parent_session_id or self.session_id,
                sub_session_id=f"sub:{self.parent_session_id or self.session_id}:temp",
                task_prompt="",
                status=TaskStatus.PENDING,
                agent_name=None,
            )

        # 创建记忆模块
        if self.memory is None:
            self.memory = BufferMemory()

        # 从配置中提取模型配置
        model_config = self.config.model_config or {}

        # 创建 SubAgent 实例
        self._sub_agent = SubAgent(
            session_id=self.session_id,
            memory=self.memory,
            task=self._task,
            db=self.db,
            model_config=model_config,
            role_prompt=self._role_prompt,
            max_tool_rounds=self.config.max_tool_rounds,
            parent_session_id=self.parent_session_id or "",
        )

        self._status = RuntimeStatus.IDLE
        logger.debug(f"InternalSubAgentRuntime initialized for session {self.session_id}")

    async def _execute_core(self, task_prompt: str, **kwargs: Any) -> ExecutionResult:
        """执行任务（非流式）。

        Args:
            task_prompt: 任务提示词
            **kwargs: 额外参数

        Returns:
            ExecutionResult: 执行结果
        """
        if self._sub_agent is None:
            raise RuntimeError("SubAgent not initialized")

        start_time = asyncio.get_event_loop().time()

        try:
            # 执行任务
            result_text = await self._sub_agent.run(task_prompt)

            elapsed = asyncio.get_event_loop().time() - start_time

            return ExecutionResult(
                success=True,
                output=result_text,
                metadata={
                    "elapsed_seconds": elapsed,
                    "session_id": self.session_id,
                    "task_id": self._task.id if self._task else None,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed = asyncio.get_event_loop().time() - start_time
            logger.exception(f"InternalSubAgentRuntime execution failed: {e}")
            return ExecutionResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
                metadata={
                    "elapsed_seconds": elapsed,
                    "session_id": self.session_id,
                },
            )

    async def _stream_core(
        self, task_prompt: str, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式执行任务。

        由于 SubAgent 本身不支持真正的流式输出，
        这里采用事件监听的方式，将执行日志转换为 SSE 事件。

        Args:
            task_prompt: 任务提示词
            **kwargs: 额外参数

        Yields:
            SSE 事件 dict
        """
        if self._sub_agent is None:
            raise RuntimeError("SubAgent not initialized")

        # 订阅任务事件
        from agentpal.services.task_event_bus import task_event_bus

        # 创建队列接收事件
        event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def on_event(event: dict[str, Any]) -> None:
            """事件回调。"""
            event_queue.put_nowait(event)

        # 订阅该任务的事件
        task_id = self._task.id if self._task else None
        if task_id:
            task_event_bus.subscribe(task_id, on_event)

        try:
            # 启动任务（在后台运行）
            task = asyncio.create_task(self._sub_agent.run(task_prompt))

            # 持续监听事件
            while True:
                try:
                    # 等待事件或任务完成
                    event_future = asyncio.ensure_future(event_queue.get())
                    done, pending = await asyncio.wait(
                        [event_future, task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    # 处理已完成的事件
                    for fut in done:
                        if fut is event_future and not task.done():
                            # 收到事件
                            event = event_future.result()
                            yield self._convert_event_to_sse(event)
                        elif fut is task:
                            # 任务完成
                            result = fut.result()
                            yield {"type": "done", "result": result}
                            break

                    # 检查是否有更多事件
                    if not task.done():
                        continue

                except asyncio.CancelledError:
                    task.cancel()
                    raise

        except asyncio.CancelledError:
            logger.info(f"Stream cancelled for session {self.session_id}")
            raise
        except Exception as e:
            logger.exception(f"Stream error: {e}")
            yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
        finally:
            # 取消订阅
            if task_id:
                task_event_bus.unsubscribe(task_id, on_event)

    def _convert_event_to_sse(self, event: dict[str, Any]) -> dict[str, Any]:
        """将任务事件转换为 SSE 格式。

        Args:
            event: 任务事件 dict

        Returns:
            SSE 事件 dict
        """
        event_type = event.get("event_type", "")
        data = event.get("data", {})

        mapping = {
            "tool.start": lambda d: {
                "type": "tool_start",
                "id": d.get("id", ""),
                "name": d.get("name", ""),
                "input": d.get("input", {}),
            },
            "tool.complete": lambda d: {
                "type": "tool_done",
                "id": d.get("id", ""),
                "output": d.get("output", "")[:2000],
                "duration_ms": d.get("duration_ms", 0),
            },
            "llm.message": lambda d: {
                "type": "text_delta",
                "delta": str(d.get("content", ""))[:500],
            },
            "task.progress": lambda d: {
                "type": "progress",
                "pct": d.get("pct", 0),
                "message": d.get("message", ""),
            },
            "artifact.created": lambda d: {
                "type": "artifact",
                "artifact_id": d.get("artifact_id", ""),
                "artifact_type": d.get("artifact_type", ""),
                "title": d.get("title", ""),
            },
        }

        converter = mapping.get(event_type, lambda d: {"type": "unknown", "raw": d})
        return converter(data)

    async def _cleanup(self) -> None:
        """清理资源。

        目前 SubAgent 不需要特殊清理，保留接口供未来扩展。
        """
        logger.debug(f"InternalSubAgentRuntime cleanup for session {self.session_id}")
        self._sub_agent = None

    async def _cancel(self) -> None:
        """取消当前执行。

        设置取消标志并调用 SubAgent 的 cancel 方法。
        """
        self._cancel_flag = True
        if self._sub_agent and self._task:
            await self._sub_agent.cancel(reason="Runtime cancelled")
        logger.info(f"InternalSubAgentRuntime cancelled for session {self.session_id}")

    # ── InternalSubAgentRuntime 特有方法 ─────────────────────

    def get_sub_agent(self) -> SubAgent | None:
        """获取内部的 SubAgent 实例（用于高级用法）。"""
        return self._sub_agent

    def get_task(self) -> SubAgentTask | None:
        """获取关联的 SubAgentTask 记录。"""
        return self._task
