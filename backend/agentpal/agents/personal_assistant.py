"""PersonalAssistant — 主助手 Agent，负责对话 + 派遣 SubAgent。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import agentscope
from agentscope.agents import UserAgent
from agentscope.message import Msg

from agentpal.agents.base import BaseAgent
from agentpal.agents.sub_agent import SubAgent
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
        system_prompt: 系统提示词，默认使用 DEFAULT_SYSTEM_PROMPT
        model_config:  AgentScope 模型配置，默认从全局 Settings 读取
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
        self._sub_tasks: dict[str, SubAgentTask] = {}  # 内存级任务索引（补充 DB）

        # 初始化 AgentScope（幂等，重复调用无副作用）
        agentscope.init(model_configs=[self._model_config])

    # ── 核心对话 ──────────────────────────────────────────

    async def reply(self, user_input: str, **kwargs: Any) -> str:
        """处理用户输入，返回助手回复。"""
        await self._remember_user(user_input)
        history = await self._get_history(limit=20)

        # 构建带历史的消息列表
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(history[:-1])  # 历史（不含刚写入的 user）
        messages.append({"role": "user", "content": user_input})

        response = await self._call_llm(messages)
        await self._remember_assistant(response)
        return response

    # ── SubAgent 派遣 ─────────────────────────────────────

    async def dispatch_sub_agent(
        self,
        task_prompt: str,
        db: Any,  # AsyncSession，避免循环导入用 Any
        context: dict[str, Any] | None = None,
    ) -> SubAgentTask:
        """创建并异步启动一个 SubAgent 任务。

        Args:
            task_prompt: 子任务描述
            db:          AsyncSession，用于持久化任务记录
            context:     额外上下文信息

        Returns:
            SubAgentTask 记录（status=PENDING，异步执行中）
        """
        import asyncio

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

        # 创建 SubAgent（使用独立 memory 实例）
        sub_memory = MemoryFactory.create("buffer")  # SubAgent 默认纯内存
        sub_agent = SubAgent(
            session_id=sub_session_id,
            memory=sub_memory,
            task=task,
            db=db,
            model_config=self._model_config,
        )

        # 异步执行（不阻塞主对话）
        asyncio.create_task(sub_agent.run(task_prompt))
        return task

    # ── 内部方法 ──────────────────────────────────────────

    async def _call_llm(self, messages: list[dict[str, Any]]) -> str:
        """调用 AgentScope LLM，返回文本回复。"""
        settings = get_settings()
        from agentscope.models import load_model_by_config_name

        model = load_model_by_config_name(self._model_config["config_name"])
        response = model(messages)
        return response.text


def _default_model_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "config_name": "default",
        "model_type": "dashscope_chat" if settings.llm_provider == "dashscope" else "openai_chat",
        "model_name": settings.llm_model,
        "api_key": settings.llm_api_key,
    }
