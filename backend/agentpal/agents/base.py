"""BaseAgent — 所有 Agent 的抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agentpal.memory.base import BaseMemory, MemoryMessage, MemoryRole


class BaseAgent(ABC):
    """Agent 抽象基类。

    约定：
    - 每个 Agent 持有一个 session_id（会话隔离）
    - 通过注入的 BaseMemory 读写上下文，不直接操作数据库
    - reply() 是唯一的对外接口
    """

    def __init__(
        self,
        session_id: str,
        memory: BaseMemory,
        system_prompt: str = "",
    ) -> None:
        self.session_id = session_id
        self.memory = memory
        self.system_prompt = system_prompt

    @abstractmethod
    async def reply(self, user_input: str, **kwargs: Any) -> str:
        """处理用户输入，返回 Agent 回复文本。"""

    # ── 记忆操作快捷方法 ──────────────────────────────────

    async def _remember_user(self, content: str) -> None:
        await self.memory.add(
            MemoryMessage(session_id=self.session_id, role=MemoryRole.USER, content=content)
        )

    async def _remember_assistant(self, content: str, meta: dict | None = None) -> None:
        await self.memory.add(
            MemoryMessage(
                session_id=self.session_id,
                role=MemoryRole.ASSISTANT,
                content=content,
                metadata=meta or {},
            )
        )

    async def _get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近对话历史，转换为 AgentScope Msg 格式。"""
        msgs = await self.memory.get_recent(self.session_id, limit=limit)
        return [m.to_agentscope_msg() for m in msgs]
