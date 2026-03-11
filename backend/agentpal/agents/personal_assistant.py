"""PersonalAssistant — 主助手 Agent，负责对话 + 派遣 SubAgent。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agentpal.agents.base import BaseAgent
from agentpal.config import get_settings
from agentpal.memory.base import BaseMemory
from agentpal.memory.factory import MemoryFactory
from agentpal.models.session import SubAgentTask, TaskStatus

DEFAULT_SYSTEM_PROMPT = """你是一个智能个人助手。你可以：
1. 回答用户的问题
2. 帮助用户完成任务
3. 在需要时将复杂任务委托给子代理异步处理

请保持友好、简洁、专业的风格。"""


class PersonalAssistant(BaseAgent):
    """主助手，与用户直接对话。

    Args:
        session_id:    会话 ID（通常为渠道 + 用户 ID 的组合）
        memory:        记忆后端实例
        system_prompt: 系统提示词
        model_config:  模型配置 dict，默认从全局 Settings 读取
    """

    def __init__(
        self,
        session_id: str,
        memory: BaseMemory,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        model_config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(session_id=session_id, memory=memory, system_prompt=system_prompt)
        self._model_config = model_config or _default_model_config()

    # ── 核心对话 ──────────────────────────────────────────

    async def reply(self, user_input: str, **kwargs: Any) -> str:
        """处理用户输入，返回助手回复。"""
        await self._remember_user(user_input)
        history = await self._get_history(limit=20)

        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(history[:-1])  # 历史（不含刚写入的 user msg）
        messages.append({"role": "user", "content": user_input})

        response = await self._call_llm(messages)
        await self._remember_assistant(response)
        return response

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

    # ── LLM 调用 ──────────────────────────────────────────

    async def _call_llm(self, messages: list[dict[str, Any]]) -> str:
        """调用 agentscope 1.x 模型，返回文本回复。"""
        model = _build_model(self._model_config)
        # agentscope 1.x __call__ 是异步协程，直接 await
        response = await model(messages)
        return _extract_text(response)


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
    """根据 provider 实例化 agentscope 1.x 模型对象。

    支持：
    - "dashscope"  → DashScopeChatModel
    - "openai"     → OpenAIChatModel（官方 API）
    - "compatible" → OpenAIChatModel + 自定义 base_url（任何 OpenAI 兼容服务）
    """
    provider = config.get("provider", "dashscope")
    model_name = config.get("model_name", "qwen-max")
    api_key = config.get("api_key", "")
    base_url = config.get("base_url", "")

    if provider == "dashscope":
        from agentscope.model import DashScopeChatModel
        return DashScopeChatModel(model_name=model_name, api_key=api_key, stream=False)

    if provider in ("openai", "compatible"):
        from agentscope.model import OpenAIChatModel
        # base_url 通过 client_kwargs 传入 OpenAI 客户端
        client_kwargs: dict[str, Any] = {}
        if base_url:
            client_kwargs["base_url"] = base_url
        return OpenAIChatModel(
            model_name=model_name,
            api_key=api_key,
            stream=False,
            client_kwargs=client_kwargs or None,
        )

    raise ValueError(f"不支持的 LLM provider: {provider!r}，可选：dashscope / openai / compatible")


def _extract_text(response: Any) -> str:
    """从 agentscope 1.x ChatResponse 中提取纯文本。"""
    # response.content 是 list[TextBlock | ToolUseBlock | ...]
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts) if parts else str(response)
