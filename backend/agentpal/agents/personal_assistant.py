"""PersonalAssistant — 主助手 Agent，支持工具调用 + SubAgent 派遣。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from time import time
from typing import Any

from agentpal.agents.base import BaseAgent
from agentpal.config import get_settings
from agentpal.memory.base import BaseMemory
from agentpal.memory.factory import MemoryFactory
from agentpal.models.session import SubAgentTask, TaskStatus
from agentpal.workspace.context_builder import ContextBuilder
from agentpal.workspace.manager import WorkspaceManager
from agentpal.workspace.memory_writer import MemoryWriter

MAX_TOOL_ROUNDS = 32  # 最大工具调用轮次，防止死循环


class PersonalAssistant(BaseAgent):
    """主助手，与用户直接对话，支持多轮工具调用。

    Args:
        session_id:   会话 ID
        memory:       记忆后端实例
        model_config: 模型配置 dict
        db:           AsyncSession（读工具配置 + 写调用日志，可选）
    """

    def __init__(
        self,
        session_id: str,
        memory: BaseMemory,
        system_prompt: str = "",   # 保留参数兼容旧代码，实际由 workspace 动态构建
        model_config: dict[str, Any] | None = None,
        db: Any = None,
    ) -> None:
        super().__init__(session_id=session_id, memory=memory, system_prompt=system_prompt)
        self._model_config = model_config or _default_model_config()
        self._db = db
        settings = get_settings()
        self._ws_manager = WorkspaceManager(Path(settings.workspace_dir))
        self._context_builder = ContextBuilder()
        self._memory_writer = MemoryWriter()

    # ── System Prompt 动态构建 ────────────────────────────

    async def _build_system_prompt(
        self,
        enabled_tool_names: list[str] | None = None,
        skill_prompts: list[dict] | None = None,
    ) -> str:
        """从 workspace 读取文件，动态组装 system prompt。"""
        ws = await self._ws_manager.load()
        return self._context_builder.build_system_prompt(
            ws, enabled_tool_names, skill_prompts=skill_prompts,
        )

    # ── 核心对话（含工具调用循环）────────────────────────

    async def reply(self, user_input: str, **kwargs: Any) -> str:
        """处理用户输入，支持多轮工具调用后返回最终回复。"""
        await self._remember_user(user_input)
        history = await self._get_history(limit=20)
        toolkit = await self._build_active_toolkit()

        enabled_tool_names = _get_tool_names(toolkit)
        skill_prompts = await self._load_prompt_skills()
        system_prompt = await self._build_system_prompt(
            enabled_tool_names or None, skill_prompts=skill_prompts or None,
        )

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(history[:-1])
        messages.append({"role": "user", "content": user_input})

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

            for tool_call in tool_calls:
                tool_msg = await self._execute_tool(toolkit, tool_call)
                messages.append(tool_msg)

        final_text = _extract_text(response)
        await self._remember_assistant(final_text)
        # 触发记忆压缩（后台异步，不阻塞）
        await self._memory_writer.maybe_flush(
            self.session_id, self.memory, self._ws_manager, self._model_config
        )
        return final_text

    async def reply_stream(
        self, user_input: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式对话，yield SSE 事件 dict。

        事件类型::

            {"type": "thinking_delta", "delta": "..."}
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
            toolkit = await self._build_active_toolkit()

            enabled_tool_names = _get_tool_names(toolkit)
            skill_prompts = await self._load_prompt_skills()
            system_prompt = await self._build_system_prompt(
                enabled_tool_names or None, skill_prompts=skill_prompts or None,
            )

            messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
            messages.extend(history[:-1])
            messages.append({"role": "user", "content": user_input})

            final_text = ""
            accumulated_thinking = ""
            accumulated_tool_calls: list[dict[str, Any]] = []
            accumulated_files: list[dict[str, Any]] = []

            for _ in range(MAX_TOOL_ROUNDS):
                tools_schema = toolkit.get_json_schemas() if toolkit else None
                model = _build_model(self._model_config, stream=True)

                # stream=True → model() 返回 AsyncGenerator[ChatResponse, None]
                # 每个 chunk 含截至当前的累积内容（非增量），需自行计算 delta
                response_gen = await model(messages, tools=tools_schema)

                prev_thinking_len = 0
                prev_text_len = 0
                final_response = None

                async for chunk in response_gen:
                    final_response = chunk
                    has_tool_calls = any(
                        isinstance(b, dict) and b.get("type") == "tool_use"
                        for b in (chunk.content or [])
                    )

                    for block in (chunk.content or []):
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")

                        if btype == "thinking":
                            full = block.get("thinking", "")
                            if len(full) > prev_thinking_len:
                                yield {"type": "thinking_delta", "delta": full[prev_thinking_len:]}
                                prev_thinking_len = len(full)
                            accumulated_thinking = full  # 记录最终完整 thinking

                        elif btype == "text" and not has_tool_calls:
                            full = block.get("text", "")
                            if len(full) > prev_text_len:
                                yield {"type": "text_delta", "delta": full[prev_text_len:]}
                                prev_text_len = len(full)

                if final_response is None:
                    break

                response = final_response
                tool_calls = [
                    b for b in (response.content or [])
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                ]

                if not tool_calls:
                    final_text = "".join(
                        b.get("text", "")
                        for b in (response.content or [])
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                    break

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

                    # 收集工具调用记录
                    accumulated_tool_calls.append({
                        "id": tc_id,
                        "name": tc_name,
                        "input": tc_input,
                        "output": output_text,
                        "error": error_text,
                        "duration_ms": duration_ms,
                        "status": "done",
                    })

                    # 收集 send_file_to_user 产生的文件
                    if tc_name == "send_file_to_user" and not error_text:
                        try:
                            info = json.loads(output_text)
                            if info.get("status") == "sent":
                                accumulated_files.append({
                                    "url": info["url"],
                                    "name": info["filename"],
                                    "mime": info.get("mime", "application/octet-stream"),
                                })
                        except Exception:
                            pass

            # 构造 meta 并持久化
            meta: dict[str, Any] = {}
            if accumulated_thinking:
                meta["thinking"] = accumulated_thinking
            if accumulated_tool_calls:
                meta["tool_calls"] = accumulated_tool_calls
            if accumulated_files:
                meta["files"] = accumulated_files

            await self._remember_assistant(final_text, meta=meta or None)
            yield {"type": "done"}

            # 触发记忆压缩（后台异步，不阻塞 SSE）
            await self._memory_writer.maybe_flush(
                self.session_id, self.memory, self._ws_manager, self._model_config
            )

        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "message": str(exc)}

    # ── SubAgent 派遣 ─────────────────────────────────────

    async def dispatch_sub_agent(
        self,
        task_prompt: str,
        db: Any,
        context: dict[str, Any] | None = None,
        task_type: str | None = None,
        agent_name: str | None = None,
    ) -> SubAgentTask:
        """创建并异步启动一个 SubAgent 任务。

        支持角色路由：
        - 如果指定 agent_name，直接使用该 SubAgent
        - 如果指定 task_type，自动匹配合适的 SubAgent
        - 都未指定，使用默认配置

        Args:
            task_prompt:  任务描述
            db:           AsyncSession
            context:      附加上下文
            task_type:    任务类型（用于自动路由）
            agent_name:   指定 SubAgent 名称
        """
        from agentpal.agents.registry import SubAgentRegistry
        from agentpal.agents.sub_agent import SubAgent
        from agentpal.models.agent import SubAgentDefinition

        task_id = str(uuid.uuid4())
        sub_session_id = f"sub:{self.session_id}:{task_id}"

        # 查找合适的 SubAgent 定义
        registry = SubAgentRegistry(db)
        agent_def: SubAgentDefinition | None = None
        role_prompt = ""
        model_config = self._model_config
        max_tool_rounds = 8

        if agent_name:
            agent_def = await db.get(SubAgentDefinition, agent_name)
        elif task_type:
            agent_def = await registry.find_agent_for_task(task_type)

        if agent_def:
            agent_name = agent_def.name
            role_prompt = agent_def.role_prompt or ""
            model_config = agent_def.get_model_config(self._model_config)
            max_tool_rounds = agent_def.max_tool_rounds

        task = SubAgentTask(
            id=task_id,
            parent_session_id=self.session_id,
            sub_session_id=sub_session_id,
            task_prompt=task_prompt,
            status=TaskStatus.PENDING,
            agent_name=agent_name,
            task_type=task_type,
            execution_log=[],
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
            model_config=model_config,
            role_prompt=role_prompt,
            max_tool_rounds=max_tool_rounds,
            parent_session_id=self.session_id,
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

        # 释放 SQLite 写锁：先提交已有的事务（memory writes 等），
        # 这样 skill_cli 等工具创建新 session 写入时不会被 "database is locked" 阻塞。
        if self._db is not None:
            try:
                await self._db.commit()
            except Exception:
                pass

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

    async def _load_prompt_skills(self) -> list[dict[str, Any]]:
        """加载已启用的 prompt 型技能内容，用于注入 system prompt。"""
        if self._db is None:
            return []
        try:
            from agentpal.skills.manager import SkillManager
            from agentpal.models.session import SessionRecord
            from sqlalchemy import select

            # 查询 session 级 skill 配置
            session_skill_names: list[str] | None = None
            result = await self._db.execute(
                select(SessionRecord).where(SessionRecord.id == self.session_id)
            )
            session_record = result.scalar_one_or_none()
            if session_record and session_record.enabled_skills is not None:
                session_skill_names = session_record.enabled_skills

            mgr = SkillManager(self._db)
            return await mgr.get_prompt_skills(session_skill_names)
        except Exception:
            return []

    async def _build_active_toolkit(self) -> Any:
        """从 DB 读取已启用工具 + 已启用 Skill 工具，构建 agentscope Toolkit。

        支持 session 级别的工具/技能覆盖：
        - session.enabled_tools 非 null → 使用 session 配置与全局启用工具的交集
        - session.enabled_skills 非 null → 使用 session 配置与全局启用技能的交集
        - 如果 session 配置了全局未启用的工具/技能，待全局启用后自动生效
        """
        if self._db is None:
            return None
        from sqlalchemy import select

        from agentpal.models.session import SessionRecord
        from agentpal.tools.registry import build_toolkit, ensure_tool_configs, get_enabled_tools

        await ensure_tool_configs(self._db)
        global_enabled = await get_enabled_tools(self._db)

        # 查询 session 级配置
        session_tools: list[str] | None = None
        session_skills: list[str] | None = None
        result = await self._db.execute(
            select(SessionRecord).where(SessionRecord.id == self.session_id)
        )
        session_record = result.scalar_one_or_none()
        if session_record:
            session_tools = session_record.enabled_tools
            session_skills = session_record.enabled_skills

        # 计算最终启用的内置工具
        if session_tools is not None:
            # session 级配置：取 session 配置与全局启用的交集
            enabled = [t for t in session_tools if t in global_enabled]
        else:
            enabled = global_enabled

        # 加载 skill 工具
        skill_tools: list[dict] = []
        try:
            from agentpal.skills.manager import SkillManager

            mgr = SkillManager(self._db)
            all_skill_tools = await mgr.get_all_skill_tools()

            if session_skills is not None:
                # session 级 skill 过滤
                enabled_skill_names = set(session_skills)
                # 获取全局启用的技能名
                all_skills = await mgr.list_skills()
                global_enabled_skill_names = {
                    s["name"] for s in all_skills if s["enabled"]
                }
                # 交集：session 配置 ∩ 全局启用
                active_skill_names = enabled_skill_names & global_enabled_skill_names
                skill_tools = [
                    t for t in all_skill_tools
                    if t.get("skill_name", "") in active_skill_names
                ]
            else:
                skill_tools = all_skill_tools
        except Exception:
            pass

        return build_toolkit(enabled, extra_tools=skill_tools or None)


# ── 辅助函数 ──────────────────────────────────────────────

def _get_tool_names(toolkit: Any) -> list[str]:
    """从 toolkit 中提取工具名称列表。"""
    if toolkit is None:
        return []
    try:
        tools = getattr(toolkit, "tools", None)
        if isinstance(tools, dict):
            return list(tools.keys())
        return []
    except Exception:
        return []


def _default_model_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "provider": settings.llm_provider,
        "model_name": settings.llm_model,
        "api_key": settings.llm_api_key,
        "base_url": settings.llm_base_url,
    }


def _build_model(config: dict[str, Any], stream: bool = False) -> Any:
    """根据 provider 实例化 agentscope 1.x 模型对象。"""
    provider = config.get("provider", "dashscope")
    model_name = config.get("model_name", "qwen-max")
    api_key = config.get("api_key", "")
    base_url = config.get("base_url", "")

    if provider == "dashscope":
        from agentscope.model import DashScopeChatModel
        return DashScopeChatModel(model_name=model_name, api_key=api_key, stream=stream)

    if provider in ("openai", "compatible"):
        from agentscope.model import OpenAIChatModel
        client_kwargs: dict[str, Any] = {}
        if base_url:
            client_kwargs["base_url"] = base_url
        return OpenAIChatModel(
            model_name=model_name,
            api_key=api_key,
            stream=stream,
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


def _extract_thinking(response: Any) -> tuple[str, str]:
    """从 ChatResponse.content 中提取 (thinking_text, answer_text)。

    agentscope 1.x 的 OpenAIChatModel 已将 reasoning_content 解析为
    ThinkingBlock(type="thinking", thinking=...)，直接从 content 读取即可。
    """
    thinking_parts: list[str] = []
    text_parts: list[str] = []
    for block in getattr(response, "content", []):
        if isinstance(block, dict):
            if block.get("type") == "thinking":
                thinking_parts.append(block.get("thinking", ""))
            elif block.get("type") == "text":
                text_parts.append(block.get("text", ""))
    thinking = "".join(thinking_parts).strip()
    text = "".join(text_parts).strip()
    if not text and not thinking_parts:
        text = str(response)
    return thinking, text
