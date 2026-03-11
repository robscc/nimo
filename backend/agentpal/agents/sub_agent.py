"""SubAgent — 异步子任务执行器，拥有独立 Session 和 Memory。"""

from __future__ import annotations

import asyncio
import traceback
from datetime import datetime, timezone
from typing import Any

from agentpal.agents.base import BaseAgent
from agentpal.agents.personal_assistant import _build_model, _extract_text
from agentpal.memory.base import BaseMemory
from agentpal.models.session import SubAgentTask, TaskStatus


class SubAgent(BaseAgent):
    """异步子任务执行代理。

    Args:
        session_id:   独立的子会话 ID（格式：sub:<parent_session>:<task_id>）
        memory:       子 Agent 的记忆后端（默认 BufferMemory，任务结束即释放）
        task:         对应的 SubAgentTask 数据库记录
        db:           AsyncSession，用于更新任务状态
        model_config: LLM 配置 dict
    """

    def __init__(
        self,
        session_id: str,
        memory: BaseMemory,
        task: SubAgentTask,
        db: Any,
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

        messages = [
            {
                "role": "system",
                "content": "你是一个专注的任务执行代理。请认真完成被分配的任务，直接给出结果，无需多余的寒暄。",
            },
            {"role": "user", "content": user_input},
        ]

        model = _build_model(self._model_config)
        response = await model(messages)
        text = _extract_text(response)
        await self._remember_assistant(text)
        return text

    # ── 内部方法 ──────────────────────────────────────────

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
            pass
