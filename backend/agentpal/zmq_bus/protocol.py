"""ZMQ 消息协议 — Envelope 信封 + MessageType 枚举 + msgpack 序列化。

所有 Agent 间通信统一使用 Envelope 封装。
序列化使用 msgpack（二进制、紧凑、tcp 友好），切换传输层时只需改地址不改协议。

ZMQ 帧格式：
  ROUTER/DEALER:  [identity | b"" | envelope_bytes]
  PUB/SUB:        [topic_bytes | envelope_bytes]
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any

import msgpack
from pydantic import BaseModel, Field


class MessageType(StrEnum):
    """消息类型枚举。"""

    # HTTP API → Agent
    CHAT_REQUEST = "chat_request"
    DISPATCH_TASK = "dispatch_task"
    CRON_TRIGGER = "cron_trigger"
    TOOL_GUARD_RESOLVE = "tool_guard_resolve"  # API → PA: 工具安全确认转发

    # Agent → HTTP (via PUB/SUB)
    STREAM_EVENT = "stream_event"
    TASK_EVENT = "task_event"

    # Agent ↔ Agent
    AGENT_REQUEST = "agent_request"
    AGENT_RESPONSE = "agent_response"
    AGENT_NOTIFY = "agent_notify"
    AGENT_BROADCAST = "agent_broadcast"

    # Plan Mode
    PLAN_STEP_DONE = "plan_step_done"  # Broker → PA: 计划步骤完成

    # Lifecycle
    AGENT_REGISTER = "agent_register"
    AGENT_HEARTBEAT = "agent_heartbeat"
    AGENT_SHUTDOWN = "agent_shutdown"
    AGENT_STATE_CHANGE = "agent_state_change"  # Scheduler → Dashboard SSE

    # Client ↔ Scheduler 控制通信
    ENSURE_PA = "ensure_pa"              # Client → Scheduler: 请求确保 PA 进程就绪
    ENSURE_PA_ACK = "ensure_pa_ack"      # Scheduler → Client: PA 就绪确认
    DISPATCH_SUB = "dispatch_sub"        # Client → Scheduler: 派遣 SubAgent
    DISPATCH_SUB_ACK = "dispatch_sub_ack"  # Scheduler → Client: SubAgent 派遣确认
    LIST_AGENTS = "list_agents"          # Client → Scheduler: 查询 Agent 列表
    LIST_AGENTS_RESP = "list_agents_resp"  # Scheduler → Client: Agent 列表响应
    GET_STATS = "get_stats"              # Client → Scheduler: 获取统计信息
    GET_STATS_RESP = "get_stats_resp"    # Scheduler → Client: 统计信息响应
    STOP_AGENT_REQ = "stop_agent_req"    # Client → Scheduler: 停止指定 Agent
    STOP_AGENT_RESP = "stop_agent_resp"  # Scheduler → Client: 停止 Agent 响应
    SCHEDULER_SHUTDOWN = "scheduler_shutdown"  # Client → Scheduler: 关闭 Scheduler 进程
    CONFIG_RELOAD = "config_reload"      # 广播: 配置重载通知到所有子进程


class Envelope(BaseModel):
    """所有 ZMQ 消息的统一信封。

    Attributes:
        msg_id:     消息唯一 ID（UUID），用于关联 request/response
        msg_type:   消息类型
        source:     发送方 identity（如 "pa:session-123"）
        target:     接收方 identity（如 "sub:coder:task-456"）
        reply_to:   关联的请求消息 ID（可选）
        session_id: 会话上下文（可选）
        payload:    消息体（任意 dict）
        timestamp:  Unix 时间戳（秒）
    """

    msg_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    msg_type: MessageType
    source: str
    target: str
    reply_to: str | None = None
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)

    def serialize(self) -> bytes:
        """将信封序列化为 msgpack 二进制。"""
        data = {
            "msg_id": self.msg_id,
            "msg_type": str(self.msg_type),
            "source": self.source,
            "target": self.target,
            "reply_to": self.reply_to,
            "session_id": self.session_id,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }
        return msgpack.packb(data, use_bin_type=True)

    @classmethod
    def deserialize(cls, data: bytes) -> Envelope:
        """从 msgpack 二进制反序列化信封。"""
        unpacked = msgpack.unpackb(data, raw=False)
        return cls(
            msg_id=unpacked["msg_id"],
            msg_type=MessageType(unpacked["msg_type"]),
            source=unpacked["source"],
            target=unpacked["target"],
            reply_to=unpacked.get("reply_to"),
            session_id=unpacked.get("session_id"),
            payload=unpacked.get("payload", {}),
            timestamp=unpacked.get("timestamp", 0.0),
        )

    def make_reply(
        self,
        msg_type: MessageType,
        payload: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> Envelope:
        """基于当前信封创建回复信封。

        自动设置 reply_to、target（=原 source）、session_id。
        """
        return Envelope(
            msg_type=msg_type,
            source=source or self.target,
            target=self.source,
            reply_to=self.msg_id,
            session_id=self.session_id,
            payload=payload or {},
        )
