"""SubAgent — 异步子任务执行器，拥有独立 Session、Memory 和模型配置。

重设计要点：
- 独立的上下文和记忆系统
- 角色定位决定是否接受任务
- 独立模型配置（可继承主 Agent）
- 完整执行日志（LLM 对话 + 工具调用）
- 支持 Agent 间通信（通过 MessageBus）
- 生命周期由主 Agent 管理
"""

from __future__ import annotations

import asyncio
import json
import traceback
import uuid
from datetime import datetime, timezone
from time import time
from typing import Any

from loguru import logger

from agentpal.agents.base import BaseAgent
from agentpal.memory.base import BaseMemory
from agentpal.models.session import SubAgentTask, TaskStatus


class SubAgent(BaseAgent):
    """异步子任务执行代理。

    Args:
        session_id:     独立的子会话 ID（格式：sub:<parent_session>:<task_id>）
        memory:         子 Agent 的记忆后端（默认 BufferMemory）
        task:           对应的 SubAgentTask 数据库记录
        db:             AsyncSession
        model_config:   LLM 配置 dict
        role_prompt:    角色系统提示词
        max_tool_rounds: 最大工具调用轮次
        parent_session_id: 父会话 ID（用于 Agent 间通信）
    """

    def __init__(
        self,
        session_id: str,
        memory: BaseMemory,
        task: SubAgentTask,
        db: Any,
        model_config: dict[str, Any] | None = None,
        role_prompt: str = "",
        max_tool_rounds: int = 8,
        parent_session_id: str = "",
    ) -> None:
        super().__init__(session_id=session_id, memory=memory)
        self._task = task
        self._db = db
        self._model_config = model_config or {}
        self._role_prompt = role_prompt
        self._max_tool_rounds = max_tool_rounds
        self._parent_session_id = parent_session_id
        self._execution_log: list[dict[str, Any]] = []

    # ── 主入口 ────────────────────────────────────────────

    async def run(self, task_prompt: str) -> str:
        """异步执行任务，自动更新任务状态和执行日志。

        失败时自动重试（指数退避），超过 max_retries 则标记 FAILED。
        """
        await self._update_status(TaskStatus.RUNNING)

        # 检查是否有来自其他 Agent 的消息
        await self._check_incoming_messages()

        try:
            result = await self.reply(task_prompt)
            self._task.execution_log = self._execution_log
            await self._update_status(TaskStatus.DONE, result=result)
            return result
        except Exception as exc:  # noqa: BLE001
            error_msg = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            self._task.execution_log = self._execution_log

            # ── 自动重试逻辑 ──────────────────────────────────
            if self._task.retry_count < self._task.max_retries:
                self._task.retry_count += 1
                retry_count = self._task.retry_count
                backoff = min(2 ** retry_count, 30)
                self._log("retry_scheduled", {
                    "retry_count": retry_count,
                    "max_retries": self._task.max_retries,
                    "backoff_seconds": backoff,
                    "error": error_msg[:500],
                })
                logger.info(
                    "SubAgent task {} retry {}/{} after {}s",
                    self._task.id, retry_count, self._task.max_retries, backoff,
                )
                self._task.execution_log = self._execution_log
                await self._update_status(TaskStatus.PENDING)
                asyncio.create_task(self._retry(task_prompt, backoff))
                return ""

            await self._update_status(TaskStatus.FAILED, error=error_msg)
            return ""

    async def _retry(self, task_prompt: str, backoff: float) -> None:
        """延迟后重新执行任务（指数退避）。"""
        await asyncio.sleep(backoff)
        self._log("retry_start", {
            "retry_count": self._task.retry_count,
            "max_retries": self._task.max_retries,
        })
        await self.run(task_prompt)

    async def reply(self, user_input: str, **kwargs: Any) -> str:
        """执行子任务：角色 prompt + 多轮工具调用 + 完整日志。"""
        from agentpal.agents.personal_assistant import _build_model, _extract_text

        await self._remember_user(user_input)

        system_prompt = self._build_sub_system_prompt()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        # 记录初始消息
        self._log("system_prompt", {"content": system_prompt})
        self._log("user_message", {"content": user_input})

        toolkit = await self._build_toolkit()
        response = None
        loop_exhausted = False  # 标记是否因轮次耗尽退出

        for round_idx in range(self._max_tool_rounds):
            # 每轮开始前检查消息
            incoming = await self._check_incoming_messages()
            if incoming:
                # 将收到的消息作为上下文注入
                for msg in incoming:
                    inject = (
                        f"[来自 {msg['from_agent']} 的消息]\n{msg['content']}"
                    )
                    messages.append({"role": "user", "content": inject})
                    self._log("incoming_message", msg)

            tools_schema = toolkit.get_json_schemas() if toolkit else None
            model = _build_model(self._model_config)

            # 释放 DB 写锁
            if self._db is not None:
                try:
                    await self._db.commit()
                except Exception:
                    pass

            response = await model(messages, tools=tools_schema)

            # 记录响应
            response_content = []
            for block in (response.content or []):
                if isinstance(block, dict):
                    response_content.append(block)
            self._log("llm_response", {"round": round_idx, "content": response_content})

            tool_calls = [
                b for b in (response.content or [])
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]

            if not tool_calls:
                break  # 有文字回复，正常退出

            # 最后一轮仍有工具调用，标记需要强制文字总结
            if round_idx == self._max_tool_rounds - 1:
                loop_exhausted = True

            # 构建 assistant 消息
            openai_tool_calls = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("input", {}), ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ]
            text_parts = [
                b["text"] for b in (response.content or [])
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            messages.append({
                "role": "assistant",
                "content": "".join(text_parts) or None,
                "tool_calls": openai_tool_calls,
            })

            # 执行工具
            for tc in tool_calls:
                tc_id = tc.get("id", str(uuid.uuid4()))
                tc_name = tc.get("name", "")
                tc_input = tc.get("input", {})

                self._log("tool_start", {"id": tc_id, "name": tc_name, "input": tc_input})

                start_ms = int(time() * 1000)
                output_text = ""
                error_text = None

                try:
                    tool_response = None
                    async for chunk in await toolkit.call_tool_function(tc):
                        tool_response = chunk
                    if tool_response:
                        output_text = "".join(
                            b.get("text", "")
                            for b in (tool_response.content or [])
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    else:
                        output_text = "（无输出）"
                except Exception as exc:
                    error_text = str(exc)
                    output_text = f"<error>{exc}</error>"

                duration_ms = int(time() * 1000) - start_ms
                self._log("tool_done", {
                    "id": tc_id, "name": tc_name,
                    "output": output_text[:1000], "error": error_text,
                    "duration_ms": duration_ms,
                })

                messages.append({"role": "tool", "tool_call_id": tc_id, "content": output_text})

        # ── 强制最终文字总结（当轮次耗尽且仍无文字回复时）──────────
        if loop_exhausted or (response is not None and not _extract_text(response)):
            self._log("force_summary", {"reason": "loop_exhausted" if loop_exhausted else "no_text"})
            try:
                messages.append({
                    "role": "user",
                    "content": (
                        "请根据以上工具调用的结果，给出最终的完整文字回答。"
                        "不要再调用任何工具，直接整理并输出结论。"
                    ),
                })
                model = _build_model(self._model_config)
                if self._db is not None:
                    try:
                        await self._db.commit()
                    except Exception:
                        pass
                response = await model(messages, tools=None)  # 禁用工具，强制文字输出
                summary_content = [
                    b for b in (response.content or []) if isinstance(b, dict)
                ]
                self._log("force_summary_response", {"content": summary_content})
            except Exception as exc:
                self._log("force_summary_error", {"error": str(exc)})

        final_text = _extract_text(response) if response else "（无响应）"
        self._log("final_result", {"text": final_text[:2000]})
        await self._remember_assistant(final_text)
        return final_text

    # ── System Prompt ─────────────────────────────────────

    def _build_sub_system_prompt(self) -> str:
        """构建 SubAgent 的 system prompt。"""
        parts: list[str] = []

        if self._role_prompt:
            parts.append(f"# 你的角色\n\n{self._role_prompt}")
        else:
            parts.append(
                "# 你的角色\n\n"
                "你是一个专注的任务执行代理。请认真完成被分配的任务，直接给出结果，无需寒暄。"
            )

        parts.append(
            "# 工作原则\n\n"
            "- 专注于当前任务，不偏离主题\n"
            "- 优先使用工具验证结果\n"
            "- 遇到困难时可以向其他 Agent 发送协作请求\n"
            "- 完成后给出清晰、结构化的结果"
        )

        return "\n\n---\n\n".join(parts)

    # ── Agent 间通信 ──────────────────────────────────────

    async def _check_incoming_messages(self) -> list[dict[str, Any]]:
        """检查并接收来自其他 Agent 的消息。"""
        if self._db is None or not self._parent_session_id:
            return []
        try:
            from agentpal.agents.message_bus import MessageBus

            bus = MessageBus(self._db)
            agent_name = self._task.agent_name or self.session_id
            messages = await bus.receive_pending(
                agent_name, self._parent_session_id
            )
            return messages
        except Exception as e:
            logger.debug(f"SubAgent 检查消息失败: {e}")
            return []

    async def send_message(
        self,
        to_agent: str,
        content: str,
        message_type: str = "request",
    ) -> None:
        """向另一个 Agent 发送消息。"""
        if self._db is None:
            return
        try:
            from agentpal.agents.message_bus import MessageBus

            bus = MessageBus(self._db)
            agent_name = self._task.agent_name or self.session_id
            await bus.send(
                from_agent=agent_name,
                to_agent=to_agent,
                parent_session_id=self._parent_session_id,
                content=content,
                message_type=message_type,
            )
        except Exception as e:
            logger.warning(f"SubAgent 发送消息失败: {e}")

    # ── 工具集构建 ────────────────────────────────────────

    async def _build_toolkit(self) -> Any:
        """构建工具集（复用主 Agent 的全局工具配置 + Skill 工具）。"""
        if self._db is None:
            return None
        try:
            from agentpal.tools.registry import build_toolkit, ensure_tool_configs, get_enabled_tools

            await ensure_tool_configs(self._db)
            enabled = await get_enabled_tools(self._db)

            # 加载 skill 工具（引用关系，与主 Agent 共享）
            skill_tools: list[dict] = []
            try:
                from agentpal.skills.manager import SkillManager
                mgr = SkillManager(self._db)
                skill_tools = await mgr.get_all_skill_tools()
            except Exception:
                pass

            return build_toolkit(enabled, extra_tools=skill_tools or None)
        except Exception:
            return None

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

    def _log(self, event_type: str, data: dict[str, Any]) -> None:
        """向执行日志追加一条记录。"""
        self._execution_log.append({
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        })
