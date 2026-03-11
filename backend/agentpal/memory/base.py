"""Memory 抽象接口定义。

所有具体实现都必须继承 BaseMemory 并实现全部抽象方法。
新的后端（如向量数据库）只需实现此接口即可无缝接入。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class MemoryRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class MemoryMessage:
    """记忆系统中传递的统一消息格式。

    Attributes:
        session_id:  所属 Session 标识（可以是用户 ID、对话 ID 等）
        role:        消息角色
        content:     消息文本内容
        id:          唯一标识，持久化后由存储层填充
        created_at:  消息时间戳（UTC）
        metadata:    扩展元数据（来源渠道、工具调用信息等）
    """

    session_id: str
    role: MemoryRole | str
    content: str
    id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_agentscope_msg(self) -> dict[str, Any]:
        """转换为 AgentScope Msg 格式。"""
        return {
            "role": str(self.role),
            "content": self.content,
            "name": self.metadata.get("name", str(self.role)),
        }


class BaseMemory(ABC):
    """记忆后端抽象接口。

    设计原则：
    - 所有操作均为异步，避免阻塞 FastAPI 事件循环
    - session_id 隔离不同用户/SubAgent 的上下文
    - search() 默认返回空列表，向量后端覆盖此方法实现语义检索
    - summarize() 预留钩子，未来可由 LLM 自动压缩历史

    扩展指南：
        class VectorMemory(BaseMemory):
            async def search(self, session_id, query, limit):
                # 调用向量数据库实现语义检索
                ...
    """

    # ── 必须实现 ──────────────────────────────────────────

    @abstractmethod
    async def add(self, message: MemoryMessage) -> MemoryMessage:
        """写入一条消息，返回含 id 的消息对象。"""

    @abstractmethod
    async def get_recent(self, session_id: str, limit: int = 20) -> list[MemoryMessage]:
        """获取最近 limit 条消息（按时间升序）。"""

    @abstractmethod
    async def clear(self, session_id: str) -> None:
        """清空指定 session 的全部记忆。"""

    # ── 可选覆盖 ──────────────────────────────────────────

    async def search(
        self,
        session_id: str,
        query: str,
        limit: int = 5,
    ) -> list[MemoryMessage]:
        """语义/关键词检索（默认回退到关键词匹配，向量后端应覆盖此方法）。"""
        recent = await self.get_recent(session_id, limit=200)
        q = query.lower()
        matched = [m for m in recent if q in m.content.lower()]
        return matched[-limit:]

    async def get_summary(self, session_id: str) -> str | None:
        """返回该 session 的摘要（默认 None，可由 LLM 服务层覆盖生成）。"""
        return None

    async def count(self, session_id: str) -> int:
        """返回该 session 消息总数（默认通过 get_recent 估算，子类可优化）。"""
        msgs = await self.get_recent(session_id, limit=10_000)
        return len(msgs)
