"""CronAgent — 定时任务专用 Agent。

特点：
- 上下文只加载 AGENTS.md + SOUL.md（不影响主对话上下文）
- 记录完整执行日志（LLM 对话 + 工具调用）
- 独立的 BufferMemory（任务完成即释放）
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from time import time
from typing import Any

from agentpal.agents.base import BaseAgent
from agentpal.config import get_settings
from agentpal.memory.base import BaseMemory

MAX_TOOL_ROUNDS = 8


class CronAgent(BaseAgent):
    """定时任务执行 Agent。

    Args:
        session_id:    独立会话 ID
        memory:        BufferMemory（不持久化）
        model_config:  模型配置
        execution_log: 可变列表，用于收集完整日志
        db:            AsyncSession（读工具配置）
    """

    def __init__(
        self,
        session_id: str,
        memory: BaseMemory,
        model_config: dict[str, Any],
        execution_log: list[dict[str, Any]],
        db: Any = None,
    ) -> None:
        super().__init__(session_id=session_id, memory=memory)
        self._model_config = model_config
        self._execution_log = execution_log
        self._db = db

    async def run(self, task_prompt: str) -> str:
        """执行任务并返回结果文本，同时收集完整日志。"""
        return await self.reply(task_prompt)

    async def reply(self, user_input: str, **kwargs: Any) -> str:
        """执行任务：构建轻量上下文 + 多轮工具调用。"""
        from agentpal.agents.personal_assistant import _build_model, _extract_text

        system_prompt = await self._build_cron_system_prompt()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        # 记录初始消息到日志
        self._log("system_prompt", {"content": system_prompt})
        self._log("user_message", {"content": user_input})

        toolkit = await self._build_toolkit()
        response = None

        for round_idx in range(MAX_TOOL_ROUNDS):
            tools_schema = toolkit.get_json_schemas() if toolkit else None
            model = _build_model(self._model_config)
            response = await model(messages, tools=tools_schema)

            # 记录 LLM 响应
            response_content = []
            for block in (response.content or []):
                if isinstance(block, dict):
                    response_content.append(block)
            self._log("llm_response", {
                "round": round_idx,
                "content": response_content,
            })

            tool_calls = [
                b for b in (response.content or [])
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]

            if not tool_calls:
                break

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

                self._log("tool_start", {
                    "id": tc_id, "name": tc_name, "input": tc_input,
                })

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
                    "id": tc_id,
                    "name": tc_name,
                    "output": output_text[:1000],
                    "error": error_text,
                    "duration_ms": duration_ms,
                })

                messages.append({
                    "role": "tool", "tool_call_id": tc_id, "content": output_text,
                })

        final_text = _extract_text(response) if response else "（无响应）"
        self._log("final_result", {"text": final_text[:2000]})
        return final_text

    async def _build_cron_system_prompt(self) -> str:
        """构建 cron 专用的轻量 system prompt — 只加载 AGENTS.md + SOUL.md。"""
        from agentpal.workspace.manager import WorkspaceManager

        settings = get_settings()
        ws_manager = WorkspaceManager(Path(settings.workspace_dir))
        ws = await ws_manager.load()

        sections: list[str] = []
        if ws.soul.strip():
            sections.append(f"# Soul & Personality\n\n{ws.soul.strip()}")
        if ws.agents.strip():
            sections.append(f"# Agent Configuration\n\n{ws.agents.strip()}")

        sections.append(
            "# 任务模式\n\n"
            "你正在执行一个定时任务。请专注完成任务，直接给出结果。\n"
            "执行完成后，你的结果将自动发送给主 Agent。"
        )

        return "\n\n---\n\n".join(sections)

    async def _build_toolkit(self) -> Any:
        """构建工具集（复用主 Agent 的全局工具配置）。"""
        if self._db is None:
            return None
        try:
            from agentpal.tools.registry import build_toolkit, ensure_tool_configs, get_enabled_tools

            await ensure_tool_configs(self._db)
            enabled = await get_enabled_tools(self._db)
            return build_toolkit(enabled)
        except Exception:
            return None

    def _log(self, event_type: str, data: dict[str, Any]) -> None:
        """向执行日志追加一条记录。"""
        from datetime import datetime, timezone

        self._execution_log.append({
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        })
