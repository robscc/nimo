"""SubAgent — 异步子任务执行器，拥有独立 Session 和 Memory。

设计说明：
- SubAgent 由 PersonalAssistant.dispatch_sub_agent() 创建
- 每个 SubAgent 持有独立的 session_id，记忆与主助手完全隔离
- 通过 asyncio.create_task() 在后台异步运行
- 任务完成/失败后更新 SubAgentTask 记录（状态 + 结果）
- 后续可扩展：工具调用、多步规划、进度回调等
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any

from agentpal.agents.base import BaseAgent
from agentpal.memory.base import BaseMemory, MemoryMessage, MemoryRole
from agentpal.models.session import SubAgentTask, TaskStatus


class SubAgent(BaseAgent):
    """异步子任务执行代理。

    Args:
        session_id:   独立的子会话 ID（格式：sub:<parent_session>:<task_id>）
        memory:       子 Agent 的记忆后端（默认 BufferMemory，任务结束即释放）
        task:         对应的 SubAgentTask 数据库记录
        db:           AsyncSession，用于更新任务状态
        model_config: LLM 配置
    """

    def __init__(
        self,
        session_id: str,
        memory: BaseMemory,
        task: SubAgentTask,
        db: Any,  # AsyncSession，避免循环导入
        model_config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(session_id=session_id, memory=memory)
        self._task = task
        self._db = db
        self._model_config = model_config or {}

    # ── 主入口 ────────────────────────────────────────────

    async def run(self, task_prompt: str) -> str:
        """异步执行任务，自动更新任务状态。"""
        await self._update_status(TaskStatus.RUNNING)
        try:
            result = await self.reply(task_prompt)
            await self._update_status(TaskStatus.DONE, result=result)
            return result
        except Exception as exc:  # noqa: BLE001
            error_msg = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            await self._update_status(TaskStatus.FAILED, error=error_msg)
            return ""

    async def reply(self, user_input: str, **kwargs: Any) -> str:
        """执行子任务并返回结果文本。"""
        await self._remember_user(user_input)

        # 构建简洁的任务提示
        system = (
            "你是一个专注的任务执行代理。"
            "请认真完成被分配的任务，直接给出结果，无需多余的寒暄。"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_input},
        ]

        response = await self._call_llm(messages)
        await self._remember_assistant(response)
        return response

    # ── 内部方法 ──────────────────────────────────────────

    async def _call_llm(self, messages: list[dict[str, Any]]) -> str:
        """调用 LLM，返回文本。"""
        from agentscope.models import load_model_by_config_name

        model = load_model_by_config_name(self._model_config.get("config_name", "default"))
        response = model(messages)
        return response.text

    async def _update_status(
        self,
        status: TaskStatus,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        self._task.status = status
        if result is not None:
            self._task.result = result
        if error is not None:
            self._task.error = error
        if status in (TaskStatus.DONE, TaskStatus.FAILED):
            self._task.finished_at = datetime.now(timezone.utc)
        try:
            await self._db.flush()
        except Exception:
            # DB 更新失败不应影响任务结果
            pass
