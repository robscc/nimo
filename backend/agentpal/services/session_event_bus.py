"""Session 事件总线 — 向 SSE 订阅方广播会话新消息事件。

使用方式：
    # 订阅者（SSE 端点）
    queue = session_event_bus.subscribe(session_id)
    event = await asyncio.wait_for(queue.get(), timeout=30.0)

    # 发布者（定时任务调度器）
    await session_event_bus.publish(session_id, {"type": "new_message", ...})
"""

from __future__ import annotations

import asyncio
from typing import Any


class SessionEventBus:
    """Per-session asyncio 事件总线。

    每个 session 可有多个并发 SSE 订阅者。
    当有新消息写入 session 时（如定时任务完成通知），向所有订阅者广播事件。
    """

    def __init__(self) -> None:
        # session_id -> list of subscriber queues
        self._subs: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue:
        """订阅指定 session 的事件，返回消息队列。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subs.setdefault(session_id, []).append(q)
        return q

    def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        """取消订阅，客户端断开时调用。"""
        subs = self._subs.get(session_id, [])
        try:
            subs.remove(queue)
        except ValueError:
            pass
        if not subs:
            self._subs.pop(session_id, None)

    async def publish(self, session_id: str, event: dict[str, Any]) -> None:
        """向指定 session 的所有订阅者广播事件。

        队列满时丢弃本次事件（防止慢速客户端阻塞发布者）。
        """
        for q in list(self._subs.get(session_id, [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @property
    def subscriber_count(self) -> int:
        """当前全部 session 的订阅者总数（调试用）。"""
        return sum(len(v) for v in self._subs.values())


# 全局单例
session_event_bus = SessionEventBus()
