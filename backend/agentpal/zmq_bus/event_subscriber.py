"""EventSubscriber — ZMQ SUB socket 异步迭代器。

HTTP 端点通过 EventSubscriber 订阅 Agent 发布的事件流，
逐条 yield SSE 事件 dict，直到收到 done/error 终止信号。

使用方式::

    async with EventSubscriber(ctx, events_addr, "session:abc", filter_msg_id="req-123") as sub:
        async for event in sub:
            yield f"data: {json.dumps(event)}\\n\\n"
"""

from __future__ import annotations

import asyncio
from typing import Any

import zmq
import zmq.asyncio
from loguru import logger

from agentpal.zmq_bus.protocol import Envelope


class EventSubscriber:
    """ZMQ SUB socket 的异步迭代器包装。

    Args:
        ctx:             共享的 zmq.asyncio.Context
        events_addr:     XPUB broker 地址（如 "inproc://agent-events"）
        topic:           订阅主题（如 "session:abc123"、"task:task-456"）
        filter_msg_id:   可选，仅返回该 msg_id 相关的事件（用于关联 request/response）
        recv_timeout_ms: 单次 recv 超时（毫秒），超时时发送 heartbeat 给 SSE 客户端
    """

    def __init__(
        self,
        ctx: zmq.asyncio.Context,
        events_addr: str,
        topic: str,
        filter_msg_id: str | None = None,
        recv_timeout_ms: int = 30_000,
    ) -> None:
        self._ctx = ctx
        self._events_addr = events_addr
        self._topic = topic
        self._filter_msg_id = filter_msg_id
        self._recv_timeout_ms = recv_timeout_ms
        self._sub: zmq.asyncio.Socket | None = None
        self._closed = False

    async def _ensure_socket(self) -> zmq.asyncio.Socket:
        """延迟创建并连接 SUB socket。"""
        if self._sub is None:
            self._sub = self._ctx.socket(zmq.SUB)
            self._sub.connect(self._events_addr)
            self._sub.subscribe(self._topic.encode("utf-8"))
            # Give ZMQ a moment to establish the subscription
            await asyncio.sleep(0.01)
            logger.debug(f"EventSubscriber: 已订阅 topic={self._topic} addr={self._events_addr}")
        return self._sub

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict[str, Any]:
        """接收下一个事件。

        Returns:
            事件 payload dict（即 Envelope.payload）

        Raises:
            StopAsyncIteration: 收到 done/error 类型事件后终止迭代
        """
        if self._closed:
            raise StopAsyncIteration

        sock = await self._ensure_socket()

        while True:
            try:
                # Use poll with timeout to enable heartbeat
                if await sock.poll(timeout=self._recv_timeout_ms):
                    frames = await sock.recv_multipart()
                else:
                    # Timeout → return heartbeat event for SSE keep-alive
                    return {"type": "heartbeat"}

                if len(frames) < 2:
                    continue

                # frames: [topic_bytes, envelope_bytes]
                envelope_bytes = frames[1]
                envelope = Envelope.deserialize(envelope_bytes)

                # Optional msg_id filtering
                if self._filter_msg_id:
                    related_msg_id = envelope.payload.get("_msg_id") or envelope.reply_to
                    if related_msg_id != self._filter_msg_id and envelope.msg_id != self._filter_msg_id:
                        # Not related to our request, skip
                        continue

                event = envelope.payload

                # Check for terminal events
                event_type = event.get("type", "")
                if event_type in ("done", "error"):
                    self._closed = True
                    return event

                return event

            except zmq.ZMQError as e:
                if e.errno == zmq.ETERM:
                    # Context terminated
                    self._closed = True
                    raise StopAsyncIteration from e
                raise
            except asyncio.CancelledError:
                self._closed = True
                raise StopAsyncIteration

    async def close(self) -> None:
        """关闭 SUB socket。"""
        self._closed = True
        if self._sub is not None:
            try:
                self._sub.unsubscribe(self._topic.encode("utf-8"))
            except zmq.ZMQError:
                pass
            self._sub.close(linger=0)
            self._sub = None
            logger.debug(f"EventSubscriber: 已关闭 topic={self._topic}")

    # ── Context Manager ───────────────────────────────────

    async def __aenter__(self) -> EventSubscriber:
        await self._ensure_socket()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
