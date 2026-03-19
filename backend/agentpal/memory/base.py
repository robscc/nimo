"""Memory 抽象接口定义。

所有具体实现都必须继承 BaseMemory 并实现全部抽象方法。
新的后端（如向量数据库、mem0、ReMe）只需实现此接口即可无缝接入。
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


class MemoryAccessLevel(StrEnum):
    """记忆查询权限级别。

    权限范围从小到大：
    - SESSION:  仅限当前 session（默认，兼容旧行为）
    - USER:     同一用户的所有 session
    - CHANNEL:  同一渠道的所有 session
    - GLOBAL:   全局查询（管理员）
    """

    SESSION = "session"
    USER = "user"
    CHANNEL = "channel"
    GLOBAL = "global"


@dataclass
class MemoryScope:
    """记忆查询权限范围。

    用于跨 session 查询时控制权限边界。
    优先级：session_id > user_id > channel > global_access。

    Attributes:
        session_id:    限定单个 session（设置后忽略其他字段）
        user_id:       限定某用户的所有 session
        channel:       限定某渠道的所有 session
        global_access: 是否允许全局查询
        access_level:  权限级别（自动推导，或手动指定）
    """

    session_id: str | None = None
    user_id: str | None = None
    channel: str | None = None
    global_access: bool = False

    @property
    def access_level(self) -> MemoryAccessLevel:
        """根据字段自动推导权限级别。"""
        if self.session_id:
            return MemoryAccessLevel.SESSION
        if self.user_id:
            return MemoryAccessLevel.USER
        if self.channel:
            return MemoryAccessLevel.CHANNEL
        if self.global_access:
            return MemoryAccessLevel.GLOBAL
        return MemoryAccessLevel.SESSION

    def validate(self) -> None:
        """校验 scope 是否有效（至少指定一个范围）。

        Raises:
            ValueError: 未指定任何查询范围
        """
        if not any([self.session_id, self.user_id, self.channel, self.global_access]):
            raise ValueError(
                "MemoryScope 至少需要指定 session_id、user_id、channel 或 global_access"
            )


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
        user_id:     所属用户 ID（跨 session 查询需要）
        channel:     所属渠道（跨 session 查询需要）
        memory_type: 记忆分类（personal / task / tool / conversation）
    """

    session_id: str
    role: MemoryRole | str
    content: str
    id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    user_id: str | None = None
    channel: str | None = None
    memory_type: str = "conversation"

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
    - cross_session_search() 支持跨 session 的记忆检索
    - summarize() 预留钩子，未来可由 LLM 自动压缩历史

    扩展指南：
        class VectorMemory(BaseMemory):
            async def search(self, session_id, query, limit):
                # 调用向量数据库实现语义检索
                ...
            async def cross_session_search(self, scope, query, limit):
                # 跨 session 向量检索
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

    async def cross_session_search(
        self,
        scope: MemoryScope,
        query: str,
        limit: int = 10,
    ) -> list[MemoryMessage]:
        """跨 session 记忆检索。

        根据 MemoryScope 定义的权限范围搜索记忆。
        默认实现：如果 scope 指定了 session_id，回退到 search()；
        否则返回空列表（子类应覆盖实现）。

        Args:
            scope:  查询权限范围
            query:  搜索关键词或语义查询
            limit:  返回最大条数

        Returns:
            匹配的 MemoryMessage 列表
        """
        scope.validate()
        if scope.session_id:
            return await self.search(scope.session_id, query, limit)
        return []

    async def get_summary(self, session_id: str) -> str | None:
        """返回该 session 的摘要（默认 None，可由 LLM 服务层覆盖生成）。"""
        return None

    async def count(self, session_id: str) -> int:
        """返回该 session 消息总数（默认通过 get_recent 估算，子类可优化）。"""
        msgs = await self.get_recent(session_id, limit=10_000)
        return len(msgs)

    async def mark_compressed(self, session_id: str, message_ids: list[str]) -> int:
        """标记消息为已压缩（在 meta JSON 中设置 compressed=true）。

        默认 no-op，SQLiteMemory / HybridMemory 覆盖实现。

        Returns:
            实际标记的消息数
        """
        return 0
