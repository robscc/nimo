"""MessageBus — SubAgent 间异步通信。

设计：
- Agent 发消息 → 写入 agent_messages 表
- Agent 在每轮工具调用前检查 pending 消息
- 收到消息后合并到当前上下文，继续执行
- 支持 request/response 对话模式和 notify 单向通知
"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.models.message import AgentMessage, MessageStatus, MessageType


class MessageBus:
    """Agent 间消息总线。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def send(
        self,
        *,
        from_agent: str,
        to_agent: str,
        parent_session_id: str,
        content: str,
        message_type: str = MessageType.NOTIFY,
        metadata: dict[str, Any] | None = None,
        in_reply_to: str | None = None,
    ) -> AgentMessage:
        """发送一条消息。

        Args:
            from_agent:        发送方名称（"main" 表示主 Agent）
            to_agent:          接收方名称
            parent_session_id: 主会话 ID
            content:           消息正文
            message_type:      消息类型
            metadata:          附加元数据
            in_reply_to:       回复哪条消息

        Returns:
            创建的 AgentMessage 记录
        """
        msg = AgentMessage(
            id=str(uuid.uuid4()),
            from_agent=from_agent,
            to_agent=to_agent,
            parent_session_id=parent_session_id,
            message_type=message_type,
            content=content,
            extra=metadata or {},
            status=MessageStatus.PENDING,
            in_reply_to=in_reply_to,
        )
        self._db.add(msg)
        await self._db.flush()
        logger.debug(f"消息发送: {from_agent} → {to_agent} ({message_type})")
        return msg

    async def receive_pending(
        self,
        agent_name: str,
        parent_session_id: str | None = None,
        mark_delivered: bool = True,
    ) -> list[dict[str, Any]]:
        """接收指定 Agent 的待处理消息。

        Args:
            agent_name:        接收方 Agent 名称
            parent_session_id: 限定会话（可选）
            mark_delivered:    是否标记为已送达

        Returns:
            消息列表（按时间正序）
        """
        stmt = (
            select(AgentMessage)
            .where(
                AgentMessage.to_agent == agent_name,
                AgentMessage.status == MessageStatus.PENDING,
            )
            .order_by(AgentMessage.created_at)
        )
        if parent_session_id:
            stmt = stmt.where(AgentMessage.parent_session_id == parent_session_id)

        result = await self._db.execute(stmt)
        messages = result.scalars().all()

        if mark_delivered and messages:
            msg_ids = [m.id for m in messages]
            await self._db.execute(
                update(AgentMessage)
                .where(AgentMessage.id.in_(msg_ids))
                .values(status=MessageStatus.DELIVERED)
            )
            await self._db.flush()

        return [self._to_dict(m) for m in messages]

    async def mark_processed(self, message_id: str) -> None:
        """标记消息为已处理。"""
        await self._db.execute(
            update(AgentMessage)
            .where(AgentMessage.id == message_id)
            .values(status=MessageStatus.PROCESSED)
        )
        await self._db.flush()

    async def get_conversation(
        self,
        parent_session_id: str,
        agent_a: str,
        agent_b: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """获取两个 Agent 之间的对话历史。"""
        stmt = (
            select(AgentMessage)
            .where(
                AgentMessage.parent_session_id == parent_session_id,
                (
                    (AgentMessage.from_agent == agent_a) & (AgentMessage.to_agent == agent_b)
                ) | (
                    (AgentMessage.from_agent == agent_b) & (AgentMessage.to_agent == agent_a)
                ),
            )
            .order_by(AgentMessage.created_at.desc())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        messages = result.scalars().all()
        return [self._to_dict(m) for m in reversed(messages)]

    async def get_session_messages(
        self, parent_session_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """获取会话内所有 Agent 间消息。"""
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.parent_session_id == parent_session_id)
            .order_by(AgentMessage.created_at.desc())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        messages = result.scalars().all()
        return [self._to_dict(m) for m in reversed(messages)]

    @staticmethod
    def _to_dict(msg: AgentMessage) -> dict[str, Any]:
        return {
            "id": msg.id,
            "from_agent": msg.from_agent,
            "to_agent": msg.to_agent,
            "parent_session_id": msg.parent_session_id,
            "message_type": msg.message_type,
            "content": msg.content,
            "extra": msg.extra or {},
            "status": msg.status,
            "in_reply_to": msg.in_reply_to,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }
