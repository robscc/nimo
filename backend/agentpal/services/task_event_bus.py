"""TaskEventBus — SubAgent 任务事件总线。

向 SSE 订阅方广播 SubAgent 任务执行过程中的事件。

使用方式：
    # 订阅者（SSE 端点）
    queue = task_event_bus.subscribe(task_id)
    event = await asyncio.wait_for(queue.get(), timeout=30.0)

    # 发布者（SubAgent 执行器）
    await task_event_bus.emit(task_id, "task.progress", {"pct": 50, "message": "Processing..."})
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class TaskEventBus:
    """Per-task asyncio 事件总线。

    每个 task 可有多个并发 SSE 订阅者。
    当 SubAgent 任务执行过程中产生事件时，向所有订阅者广播。
    """

    def __init__(self) -> None:
        # task_id -> list of subscriber queues
        self._subs: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, task_id: str) -> asyncio.Queue:
        """订阅指定 task 的事件，返回消息队列。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subs.setdefault(task_id, []).append(q)
        logger.debug(f"TaskEventBus: 新订阅者加入 task={task_id}")
        return q

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        """取消订阅，客户端断开时调用。"""
        subs = self._subs.get(task_id, [])
        try:
            subs.remove(queue)
        except ValueError:
            pass
        if not subs:
            self._subs.pop(task_id, None)
            logger.debug(f"TaskEventBus: 无订阅者，清理 task={task_id}")

    async def emit(self, task_id: str, event_type: str, event_data: dict[str, Any] | None = None, message: str | None = None) -> None:
        """向指定 task 的所有订阅者广播事件。

        Args:
            task_id:    任务 ID
            event_type: 事件类型（如 "task.progress", "tool.start"）
            event_data: 事件负载数据
            message:    人类可读的消息描述

        队列满时丢弃本次事件（防止慢速客户端阻塞发布者）。
        """
        event = {
            "event_type": event_type,
            "event_data": event_data or {},
            "message": message,
        }
        for q in list(self._subs.get(task_id, [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(f"TaskEventBus: 队列已满，丢弃事件 task={task_id} event={event_type}")
                pass

    async def emit_to_many(self, task_ids: list[str], event_type: str, event_data: dict[str, Any] | None = None, message: str | None = None) -> None:
        """向多个任务广播同一事件（用于群发场景）。"""
        for tid in task_ids:
            await self.emit(tid, event_type, event_data, message)

    @property
    def subscriber_count(self) -> int:
        """当前全部 task 的订阅者总数（调试用）。"""
        return sum(len(v) for v in self._subs.values())

    def get_subscriber_count_for_task(self, task_id: str) -> int:
        """获取指定 task 的订阅者数量（调试用）。"""
        return len(self._subs.get(task_id, []))


# 全局单例
task_event_bus = TaskEventBus()
