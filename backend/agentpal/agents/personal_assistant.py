"""PersonalAssistant — 主助手 Agent，支持工具调用 + SubAgent 派遣。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from time import time
from typing import Any

from agentpal.agents.base import BaseAgent
from agentpal.config import get_settings
from agentpal.memory.base import BaseMemory
from agentpal.memory.factory import MemoryFactory
from agentpal.models.session import SubAgentTask, TaskStatus

DEFAULT_SYSTEM_PROMPT = """你是一个智能个人助手。你可以：
1. 回答用户的问题
2. 帮助用户完成任务
3. 使用工具（Shell命令、读写文件、浏览网页、查询时间等）
4. 在需要时将复杂任务委托给子代理异步处理

请保持友好、简洁、专业的风格。使用工具前请简短说明你的意图。"""

MAX_TOOL_ROUNDS = 8  # 最大工具调用轮次，防止死循环


class PersonalAssistant(BaseAgent):
    """主助手，与用户直接对话，支持多轮工具调用。

    Args:
        session_id:    会话 ID
        memory:        记忆后端实例
        system_prompt: 系统提示词
        model_config:  模型配置 dict
        db:            AsyncSession（读工具配置 + 写调用日志，可选）
    """

    def __init__(
        self,
        session_id: str,
        memory: BaseMemory,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        model_config: dict[str, Any] | None = None,
        db: Any = None,
    ) -> None:
        super().__init__(session_id=session_id, memory=memory, system_prompt=system_prompt)
        self._model_config = model_config or _default_model_config()
        self._db = db

    # ── 核心对话（含工具调用循环）────────────────────────

    async def reply(self, user_input: str, **kwargs: Any) -> str:
        """处理用户输入，支持多轮工具调用后返回最终回复。"""
        await self._remember_user(user_input)
        history = await self._get_history(limit=20)

        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(history[:-1])
        messages.append({"role": "user", "content": user_input})

        toolkit = await self._build_active_toolkit()
        response = None

        # ── 工具调用循环 ──────────────────────────────────
        for _ in range(MAX_TOOL_ROUNDS):
            tools_schema = toolkit.get_json_schemas() if toolkit else None
            model = _build_model(self._model_config)
            response = await model(messages, tools=tools_schema)

            tool_calls = [
                block for block in (response.content or [])
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]

            if not tool_calls:
                break  # 无工具调用，返回文本回复

            # 将 agentscope ToolUseBlock 转成 OpenAI tool_calls 格式
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
            # assistant 消息中的文本部分（可为 None）
            text_parts = [
                b["text"] for b in (response.content or [])
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            messages.append({
                "role": "assistant",
                "content": "".join(text_parts) or None,
                "tool_calls": openai_tool_calls,
            })

            # 执行所有工具，每个工具结果作为单独的 tool 消息追加
            for tool_call in tool_calls:
                tool_msg = await self._execute_tool(toolkit, tool_call)
                messages.append(tool_msg)

        final_text = _extract_text(response)
        await self._remember_assistant(final_text)
        return final_text

    async def reply_stream(
        self, user_input: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式对话，yield SSE 事件 dict。

        事件类型::

            {"type": "tool_start", "id": "...", "name": "...", "input": {...}}
            {"type": "tool_done",  "id": "...", "name": "...", "output": "...",
             "error": null, "duration_ms": 3}
            {"type": "text_delta", "delta": "..."}
            {"type": "done"}
            {"type": "error",      "message": "..."}
        """
        try:
            await self._remember_user(user_input)
            history = await self._get_history(limit=20)

            messages: list[dict[str, Any]] = []
            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})
            messages.extend(history[:-1])
            messages.append({"role": "user", "content": user_input})

            toolkit = await self._build_active_toolkit()
            final_text = ""

            for _ in range(MAX_TOOL_ROUNDS):
                tools_schema = toolkit.get_json_schemas() if toolkit else None
                model = _build_model(self._model_config)
                response = await model(messages, tools=tools_schema)

                tool_calls = [
                    b for b in (response.content or [])
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                ]

                if not tool_calls:
                    # 最终回复：分块流式输出
                    final_text = _extract_text(response)
                    for i in range(0, len(final_text), 4):
                        yield {"type": "text_delta", "delta": final_text[i : i + 4]}
                        await asyncio.sleep(0.008)
                    break

                # 转换为 OpenAI tool_calls 格式
                openai_tool_calls = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(
                                tc.get("input", {}), ensure_ascii=False
                            ),
                        },
                    }
                    for tc in tool_calls
                ]
                text_parts = [
                    b["text"]
                    for b in (response.content or [])
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                messages.append(
                    {
                        "role": "assistant",
                        "content": "".join(text_parts) or None,
                        "tool_calls": openai_tool_calls,
                    }
                )

                # 逐一执行工具，实时 yield 事件
                for tool_call in tool_calls:
                    tc_id = tool_call.get("id", str(uuid.uuid4()))
                    tc_name = tool_call.get("name", "")
                    tc_input = tool_call.get("input", {})

                    yield {"type": "tool_start", "id": tc_id, "name": tc_name, "input": tc_input}

                    output_text, error_text, duration_ms = await self._run_tool(toolkit, tool_call)

                    yield {
                        "type": "tool_done",
                        "id": tc_id,
                        "name": tc_name,
                        "output": output_text,
                        "error": error_text,
                        "duration_ms": duration_ms,
                    }
                    messages.append(
                        {"role": "tool", "tool_call_id": tc_id, "content": output_text}
                    )

            await self._remember_assistant(final_text)
            yield {"type": "done"}

        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "message": str(exc)}

    # ── SubAgent 派遣 ─────────────────────────────────────

    async def dispatch_sub_agent(
        self,
        task_prompt: str,
        db: Any,
        context: dict[str, Any] | None = None,
    ) -> SubAgentTask:
        """创建并异步启动一个 SubAgent 任务。"""
        import asyncio
        from agentpal.agents.sub_agent import SubAgent

        task_id = str(uuid.uuid4())
        sub_session_id = f"sub:{self.session_id}:{task_id}"

        task = SubAgentTask(
            id=task_id,
            parent_session_id=self.session_id,
            sub_session_id=sub_session_id,
            task_prompt=task_prompt,
            status=TaskStatus.PENDING,
            meta=context or {},
        )
        db.add(task)
        await db.flush()

        sub_memory = MemoryFactory.create("buffer")
        sub_agent = SubAgent(
            session_id=sub_session_id,
            memory=sub_memory,
            task=task,
            db=db,
            model_config=self._model_config,
        )
        asyncio.create_task(sub_agent.run(task_prompt))
        return task

    # ── 工具执行 ──────────────────────────────────────────

    async def _run_tool(
        self, toolkit: Any, tool_call: dict[str, Any]
    ) -> tuple[str, str | None, int]:
        """执行单个工具，返回 (output_text, error_text, duration_ms)，并写调用日志。"""
        tool_name = tool_call.get("name", "")
        tool_input = tool_call.get("input", {})
        start_ms = int(time() * 1000)
        output_text = ""
        error_text: str | None = None

        try:
            tool_response = None
            async for chunk in await toolkit.call_tool_function(tool_call):
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

        if self._db is not None:
            from agentpal.tools.registry import log_tool_call
            try:
                await log_tool_call(
                    self._db,
                    session_id=self.session_id,
                    tool_name=tool_name,
                    input_data=tool_input,
                    output=output_text,
                    error=error_text,
                    duration_ms=duration_ms,
                )
            except Exception:
                pass

        return output_text, error_text, duration_ms

    async def _execute_tool(self, toolkit: Any, tool_call: dict[str, Any]) -> dict[str, Any]:
        """执行单个工具调用，返回 OpenAI tool 消息（供非流式 reply() 使用）。"""
        tool_id = tool_call.get("id", str(uuid.uuid4()))
        output_text, _, _ = await self._run_tool(toolkit, tool_call)
        return {"role": "tool", "tool_call_id": tool_id, "content": output_text}

    async def _build_active_toolkit(self) -> Any:
        """从 DB 读取已启用工具 + 已启用 Skill 工具，构建 agentscope Toolkit。"""
        if self._db is None:
            return None
        from agentpal.tools.registry import build_toolkit, ensure_tool_configs, get_enabled_tools
        await ensure_tool_configs(self._db)
        enabled = await get_enabled_tools(self._db)

        # 加载已启用 Skill 的工具
        skill_tools: list[dict] = []
        try:
            from agentpal.skills.manager import SkillManager
            mgr = SkillManager(self._db)
            skill_tools = await mgr.get_all_skill_tools()
        except Exception:
            pass  # Skill 系统不可用时不影响内置工具

        return build_toolkit(enabled, extra_tools=skill_tools or None)


# ── 辅助函数 ──────────────────────────────────────────────

def _default_model_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "provider": settings.llm_provider,
        "model_name": settings.llm_model,
        "api_key": settings.llm_api_key,
        "base_url": settings.llm_base_url,
    }


def _build_model(config: dict[str, Any]) -> Any:
    """根据 provider 实例化 agentscope 1.x 模型对象。"""
    provider = config.get("provider", "dashscope")
    model_name = config.get("model_name", "qwen-max")
    api_key = config.get("api_key", "")
    base_url = config.get("base_url", "")

    if provider == "dashscope":
        from agentscope.model import DashScopeChatModel
        return DashScopeChatModel(model_name=model_name, api_key=api_key, stream=False)

    if provider in ("openai", "compatible"):
        from agentscope.model import OpenAIChatModel
        client_kwargs: dict[str, Any] = {}
        if base_url:
            client_kwargs["base_url"] = base_url
        return OpenAIChatModel(
            model_name=model_name,
            api_key=api_key,
            stream=False,
            client_kwargs=client_kwargs or None,
        )

    raise ValueError(f"不支持的 LLM provider: {provider!r}")


def _extract_text(response: Any) -> str:
    """从 agentscope 1.x ChatResponse 中提取纯文本。"""
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts) if parts else str(response)
