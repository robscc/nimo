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
import re
import traceback
import uuid
from datetime import datetime, timezone
from time import time
from typing import Any

from loguru import logger

from agentpal.agents.base import BaseAgent
from agentpal.database import commit_with_retry
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

        错误分类处理：
        - Transient error（限流、超时、空响应等）→ 指数退避重试
        - Permanent error（认证、配置、请求格式等）→ 直接 FAILED，不重试
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

            # ── Permanent error → 直接 FAILED，不重试 ─────────
            if not self._is_retryable_error(exc):
                self._log("permanent_error", {
                    "error_type": type(exc).__name__,
                    "error": error_msg[:500],
                })
                logger.warning(
                    "SubAgent task {} permanent error ({}), skip retry",
                    self._task.id, type(exc).__name__,
                )
                await self._update_status(TaskStatus.FAILED, error=error_msg)
                return ""

            # ── Transient error → 指数退避重试 ────────────────
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

        # 计算 tools_schema（循环外只需一次）
        tools_schema = toolkit.get_json_schemas() if toolkit else None
        tool_count = len(tools_schema) if tools_schema else 0
        logger.debug(
            f"SubAgent [{self.session_id}] Toolkit: "
            f"exists={toolkit is not None}, tools={tool_count}"
        )

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

            # tools_schema 已在循环外计算，无需重复
            model = _build_model(self._model_config)

            # 释放 DB 写锁
            if self._db is not None:
                try:
                    await commit_with_retry(
                        self._db,
                        context={
                            "component": "sub_agent",
                            "phase": "before_llm_round",
                            "session_id": self.session_id,
                            "task_id": self._task.id,
                            "agent_name": self._task.agent_name,
                        },
                    )
                except Exception:
                    pass

            logger.debug(
                f"SubAgent [{self.session_id}] LLM call: round={round_idx}, tools={tool_count}"
            )

            response = await model(messages, tools=tools_schema)

            # 校验 LLM 响应有效性（空响应 → 抛出异常供 run() 分类处理）
            self._validate_llm_response(response)

            # 记录响应 block 类型统计
            block_types = {}
            for block in (response.content or []):
                if isinstance(block, dict):
                    bt = block.get("type", "unknown")
                    block_types[bt] = block_types.get(bt, 0) + 1

            self._log("llm_response", {"round": round_idx, "block_types": block_types})
            logger.debug(f"SubAgent [{self.session_id}] LLM response: round={round_idx}, blocks={block_types}")

            tool_calls = [
                b for b in (response.content or [])
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]

            if not tool_calls:
                # 没有 tool_use，检查是否有 thinking 但没有 text（边缘情况）
                thinking_blocks = [
                    b for b in (response.content or [])
                    if isinstance(b, dict) and b.get("type") == "thinking"
                ]
                thinking_content = "".join(
                    b.get("thinking", "") for b in thinking_blocks if isinstance(b, dict)
                )

                # 检测是否在 thinking 中模拟了工具调用（如 <function=browser_use>）
                simulated_tool_pattern = r"<function=([a-zA-Z_][a-zA-Z0-9_]*)>"
                simulated_tools = re.findall(simulated_tool_pattern, thinking_content)

                if simulated_tools:
                    self._log("simulated_tools_detected", {
                        "tools": simulated_tools,
                        "thinking_preview": thinking_content[:500],
                    })
                    logger.warning(
                        f"SubAgent [{self.session_id}] 检测到 thinking 中模拟了工具调用: {simulated_tools}，"
                        f"强制要求模型使用真实工具调用"
                    )

                    # 强制要求模型使用真实工具调用
                    messages.append({
                        "role": "user",
                        "content": (
                            "你刚才在 thinking 中模拟了工具调用，但没有实际执行。"
                            "如果你需要使用工具，请通过实际的 tool_use 调用来执行，"
                            "不要在 thinking 中模拟。现在请继续执行任务。"
                        ),
                    })
                    # 继续下一轮，不退出
                    continue

                if thinking_blocks and not any(
                    b.get("type") == "text" for b in (response.content or [])
                ):
                    logger.warning(
                        f"SubAgent [{self.session_id}] 模型返回 thinking 但无 tool_use 或 text，"
                        f"可能模型未正确调用工具"
                    )
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

                # 工具执行前 commit，避免 SQLite 锁
                if self._db is not None:
                    try:
                        await commit_with_retry(
                            self._db,
                            context={
                                "component": "sub_agent",
                                "phase": "before_tool_call",
                                "session_id": self.session_id,
                                "task_id": self._task.id,
                                "tool_name": tc_name,
                                "agent_name": self._task.agent_name,
                            },
                        )
                    except Exception:
                        pass

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
                        await commit_with_retry(
                            self._db,
                            context={
                                "component": "sub_agent",
                                "phase": "before_force_summary",
                                "session_id": self.session_id,
                                "task_id": self._task.id,
                                "agent_name": self._task.agent_name,
                            },
                        )
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

    # ── LLM 响应校验与错误分类 ────────────────────────────

    def _validate_llm_response(self, response: Any) -> None:
        """校验 LLM 响应有效性，无效时抛出相应错误。

        有效响应须包含至少一项：非空文本 block 或 tool_use block。
        """
        from agentpal.agents.errors import LLMEmptyResponseError

        if response is None:
            raise LLMEmptyResponseError("LLM 返回了 None")

        content = getattr(response, "content", None)
        if content is None:
            raise LLMEmptyResponseError("LLM response.content 为 None")

        if isinstance(content, list) and len(content) == 0:
            raise LLMEmptyResponseError("LLM response.content 为空列表")

        if isinstance(content, list):
            has_text = any(
                isinstance(b, dict)
                and b.get("type") == "text"
                and b.get("text", "").strip()
                for b in content
            )
            has_tool = any(
                isinstance(b, dict) and b.get("type") == "tool_use"
                for b in content
            )
            if not has_text and not has_tool:
                raise LLMEmptyResponseError(
                    f"LLM 响应无有效内容（{len(content)} blocks，无文本或工具调用）"
                )

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        """判断异常是否为可重试的瞬时错误。

        Permanent（直接 FAILED）：
            - 认证/权限错误 (401, 403)
            - 请求格式错误 (400, 422)
            - 自定义 PermanentLLMError

        Transient（可重试）：
            - 限流 (429)
            - 服务端错误 (500, 502, 503, 504)
            - 超时、连接中断
            - LLMEmptyResponseError
        """
        from agentpal.agents.errors import SubAgentError

        # 自定义错误 → 读 retryable 属性
        if isinstance(exc, SubAgentError):
            return exc.retryable

        # OpenAI SDK 错误
        try:
            import openai  # noqa: PLC0415

            if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
                return False
            if isinstance(exc, openai.BadRequestError):
                return False
            if isinstance(exc, (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError)):
                return True
        except ImportError:
            pass

        # HTTP status_code
        status = getattr(exc, "status_code", None)
        if status is not None:
            if status in {400, 401, 403, 404, 422}:
                return False
            if status in {429, 500, 502, 503, 504}:
                return True

        # Python 内置瞬时错误
        if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError, OSError)):
            return True

        # 兜底：视为可重试（由 max_retries 兜底上限）
        return True

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
        """构建工具集（复用主 Agent 的全局工具配置 + Skill 工具 + SubAgent 专用工具）。"""
        if self._db is None:
            return None
        try:
            from agentpal.tools.registry import build_toolkit, ensure_tool_configs, get_enabled_tools

            await ensure_tool_configs(self._db)
            enabled = await get_enabled_tools(self._db)

            # 加载 skill 工具
            skill_tools: list[dict] = []
            try:
                from agentpal.skills.manager import SkillManager
                mgr = SkillManager(self._db)
                skill_tools = await mgr.get_all_skill_tools()
            except Exception as e:
                logger.debug(f"SubAgent [{self.session_id}] 加载 Skill 工具失败: {e}")

            # SubAgent 上下文：包含 produce_artifact 等 subagent_only 工具
            return build_toolkit(enabled, extra_tools=skill_tools or None, is_subagent=True)
        except Exception as e:
            logger.error(f"SubAgent [{self.session_id}] _build_toolkit 异常: {e}")
            return None

    # ── 内部方法 ──────────────────────────────────────────

    async def _update_status(
        self,
        status: TaskStatus,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        from agentpal.services.task_event_bus import task_event_bus

        self._task.status = status
        if result is not None:
            self._task.result = result
        if error is not None:
            self._task.error = error

        # 设置时间戳
        if status == TaskStatus.RUNNING and not self._task.started_at:
            self._task.started_at = datetime.now(timezone.utc)
        if status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            self._task.finished_at = datetime.now(timezone.utc)
            self._task.completed_at = self._task.finished_at

        try:
            await self._db.flush()
            # 状态变更尽快持久化，避免终态长时间停留在内存事务中。
            await commit_with_retry(
                self._db,
                context={
                    "component": "sub_agent",
                    "phase": "update_status",
                    "session_id": self.session_id,
                    "task_id": self._task.id,
                    "status": status.value,
                    "agent_name": self._task.agent_name,
                },
            )
        except Exception:
            pass

        # 发射任务状态事件
        event_type_map = {
            TaskStatus.RUNNING: "task.started",
            TaskStatus.DONE: "task.completed",
            TaskStatus.FAILED: "task.failed",
            TaskStatus.CANCELLED: "task.cancelled",
            TaskStatus.INPUT_REQUIRED: "task.input_required",
            TaskStatus.PAUSED: "task.paused",
        }
        if status in event_type_map:
            await task_event_bus.emit(
                self._task.id,
                event_type_map[status],
                {
                    "status": status.value,
                    "result": result[:500] if result else None,
                    "error": error[:500] if error else None,
                },
                f"任务状态变更为 {status.value}",
            )

        # 任务终态时发布 Session 事件（用于主会话实时卡片更新）
        if status in (TaskStatus.DONE, TaskStatus.FAILED) and self._parent_session_id:
            try:
                from agentpal.services.session_event_bus import session_event_bus

                await session_event_bus.publish(
                    self._parent_session_id,
                    {
                        "type": "async_task_done",
                        "source": "sub_agent",
                        "task_id": self._task.id,
                        "agent_name": self._task.agent_name,
                        "status": status.value,
                        "result_preview": result[:500] if result else None,
                        "error_preview": error[:500] if error else None,
                    },
                )
            except Exception:
                pass

        # 任务终态时发布 WebSocket 通知
        if status in (TaskStatus.DONE, TaskStatus.FAILED):
            try:
                from agentpal.services.notification_bus import (
                    Notification,
                    NotificationType,
                    notification_bus,
                )

                ntype = (
                    NotificationType.SUBAGENT_TASK_DONE
                    if status == TaskStatus.DONE
                    else NotificationType.SUBAGENT_TASK_FAILED
                )
                await notification_bus.publish(
                    Notification(
                        type=ntype,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        payload={
                            "task_id": self._task.id,
                            "agent_name": self._task.agent_name,
                            "status": status.value,
                        },
                    )
                )
            except Exception:
                pass  # 通知失败不影响主流程

    def _log(self, event_type: str, data: dict[str, Any]) -> None:
        """向执行日志追加一条记录，并同步发射到 TaskEventBus。"""
        import asyncio

        from agentpal.services.task_event_bus import task_event_bus

        self._execution_log.append({
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        })

        # 同步发射到事件总线（用于实时 SSE 推送）
        event_type_map = {
            "tool_start": "tool.start",
            "tool_done": "tool.complete",
            "llm_response": "llm.message",
            "user_message": "user.message",
        }
        if event_type in event_type_map:
            message_map = {
                "tool_start": f"开始执行工具 {data.get('name', '')}",
                "tool_done": f"工具 {data.get('name', '')} 执行完成",
                "llm_response": "LLM 响应",
                "user_message": "用户消息",
            }
            # 不要在这里 await，避免阻塞主流程
            asyncio.create_task(
                task_event_bus.emit(
                    self._task.id,
                    event_type_map[event_type],
                    data,
                    message_map.get(event_type, ""),
                )
            )

    async def _emit_progress(self, pct: int, message: str) -> None:
        """发射进度更新事件。"""
        import asyncio

        from agentpal.services.task_event_bus import task_event_bus

        self._task.progress_pct = pct
        self._task.progress_message = message

        try:
            await self._db.flush()
        except Exception:
            pass

        asyncio.create_task(
            task_event_bus.emit(
                self._task.id,
                "task.progress",
                {"pct": pct, "message": message},
                message,
            )
        )

    async def produce_artifact(
        self,
        artifact_type: str,
        content: str,
        title: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """生成任务产出物并保存到数据库。

        Args:
            artifact_type: 产出物类型（如 "code", "doc", "analysis", "summary"）
            content:       产出物内容（文本或 JSON）
            title:         人类可读标题
            extra:      额外元数据

        Returns:
            产出物 ID
        """
        from agentpal.models.session import TaskArtifact

        artifact_id = f"art_{uuid.uuid4().hex[:12]}"
        artifact = TaskArtifact(
            id=artifact_id,
            task_id=self._task.id,
            artifact_type=artifact_type,
            content=content,
            title=title or f"{artifact_type}_{artifact_id[:8]}",
            extra=extra or {},
        )
        self._db.add(artifact)
        await self._db.flush()

        # 发射事件
        from agentpal.services.task_event_bus import task_event_bus

        asyncio.create_task(
            task_event_bus.emit(
                self._task.id,
                "artifact.created",
                {
                    "artifact_id": artifact_id,
                    "artifact_type": artifact_type,
                    "title": artifact.title,
                },
                f"生成产出物：{artifact.title}",
            )
        )

        return artifact_id

    async def request_user_input(
        self,
        question: str,
        context: str | None = None,
    ) -> str:
        """请求用户输入，将任务状态设置为 INPUT_REQUIRED 并等待。

        Args:
            question: 向用户提出的问题
            context: 可选的上下文信息

        Returns:
            用户提供的输入内容
        """
        # 保存当前问题到 meta
        if self._task.meta is None:
            self._task.meta = {}
        self._task.meta["input_request"] = {
            "question": question,
            "context": context,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }

        # 记录到执行日志
        self._log("input_requested", {"question": question, "context": context})

        # 更新任务状态为 INPUT_REQUIRED
        await self._update_status(TaskStatus.INPUT_REQUIRED)

        # 等待用户输入（轮询检查 meta）
        while True:
            await asyncio.sleep(2)  # 每 2 秒检查一次
            # 刷新任务数据
            await self._db.refresh(self._task)
            if self._task.meta and "user_input" in self._task.meta:
                user_input = self._task.meta["user_input"]
                self._log("input_received", {"input": user_input[:500]})
                # 清除输入请求
                del self._task.meta["input_request"]
                await self._db.flush()
                return user_input
            # 检查任务是否被恢复执行
            if self._task.status == TaskStatus.PENDING:
                # 任务已恢复，获取用户输入
                user_input = self._task.meta.get("user_input", "")
                self._log("input_received", {"input": user_input[:500]})
                return user_input
            # 检查任务是否被取消
            if self._task.status == TaskStatus.CANCELLED:
                self._log("task_cancelled", {"reason": self._task.meta.get("cancel_reason", "用户取消")})
                raise asyncio.CancelledError("任务已被取消")

    async def cancel(self, reason: str = "用户取消") -> None:
        """取消正在运行的任务。

        Args:
            reason: 取消原因
        """
        self._log("cancelling", {"reason": reason})
        await self._update_status(TaskStatus.CANCELLED)

        # 保存取消原因
        if self._task.meta is None:
            self._task.meta = {}
        self._task.meta["cancel_reason"] = reason
        self._task.meta["cancelled_at"] = datetime.now(timezone.utc).isoformat()

        try:
            await self._db.flush()
        except Exception:
            pass

        logger.info(f"SubAgent task {self._task.id} cancelled: {reason}")
