"""MessageBus — SubAgent 间异步通信。

设计：
- Hybrid 模式：DB 审计 + ZMQ 实时投递
- Agent 发消息 → 写入 agent_messages 表（审计 + 历史查询）
- 如果 ZMQ manager 可用，同时通过 ZMQ AGENT_NOTIFY 实时投递
- Agent 在每轮工具调用前检查 pending 消息（ZMQ 模式下消息通过 DEALER 推送，此接口保留用于历史兼容）
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
    """Agent 间消息总线（Hybrid 模式）。

    Args:
        db:          AsyncSession（DB 审计 + 历史查询）
        zmq_manager: AgentDaemonManager 实例（可选，提供 ZMQ 实时投递）
    """

    def __init__(self, db: AsyncSession, zmq_manager: Any = None) -> None:
        self._db = db
        self._zmq = zmq_manager

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

        # 通过 ZMQ 实时投递（如果可用）
        if self._zmq is not None:
            try:
                from agentpal.zmq_bus.protocol import Envelope
                from agentpal.zmq_bus.protocol import MessageType as ZmqMsgType

                # 推断 target identity
                target_identity = self._resolve_target_identity(to_agent, parent_session_id)

                envelope = Envelope(
                    msg_type=ZmqMsgType.AGENT_NOTIFY,
                    source=from_agent,
                    target=target_identity,
                    session_id=parent_session_id,
                    payload={
                        "message_id": msg.id,
                        "from_agent": from_agent,
                        "to_agent": to_agent,
                        "content": content,
                        "message_type": message_type,
                        "metadata": metadata or {},
                        "in_reply_to": in_reply_to,
                    },
                )
                await self._zmq.send_to_agent(target_identity, envelope)
                logger.debug(f"ZMQ 实时投递: {from_agent} → {target_identity}")
            except Exception as e:
                logger.debug(f"ZMQ 投递失败（回退到 DB 轮询）: {e}")

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

    @staticmethod
    def _resolve_target_identity(to_agent: str, parent_session_id: str) -> str:
        """将 agent 名称解析为 ZMQ identity。

        Args:
            to_agent:          目标 agent 名称
            parent_session_id: 父会话 ID

        Returns:
            ZMQ socket identity（如 "pa:session-123"、"sub:coder:task-456"）
        """
        if to_agent == "main":
            # 主 Agent → PA daemon
            return f"pa:{parent_session_id}"
        elif to_agent.startswith("pa:") or to_agent.startswith("sub:") or to_agent.startswith("cron:"):
            # 已经是 ZMQ identity 格式
            return to_agent
        else:
            # SubAgent 名称 → 尝试匹配 identity
            # 无法精确匹配时使用 PA 作为中转
            return f"pa:{parent_session_id}"
