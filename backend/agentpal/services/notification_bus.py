"""NotificationBus — 全局通知事件总线。

向 WebSocket 订阅方广播系统事件（SubAgent 任务完成、Cron 执行结果等）。

使用方式：
    # 订阅者（WebSocket 端点）
    queue = notification_bus.subscribe()
    notification = await asyncio.wait_for(queue.get(), timeout=30.0)

    # 发布者（SubAgent / CronScheduler）
    await notification_bus.publish(Notification(
        type=NotificationType.SUBAGENT_TASK_DONE,
        payload={"task_id": "...", "status": "done"},
    ))
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class NotificationType(str, Enum):
    """通知类型枚举。"""

    SUBAGENT_TASK_DONE = "subagent_task_done"
    SUBAGENT_TASK_FAILED = "subagent_task_failed"
    CRON_EXECUTION_DONE = "cron_execution_done"
    CRON_EXECUTION_FAILED = "cron_execution_failed"


class Notification(BaseModel):
    """通知消息模型。"""

    type: NotificationType
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    payload: dict[str, Any] = Field(default_factory=dict)


class NotificationBus:
    """全局通知事件总线。

    基于 asyncio.Queue 的 fan-out pub/sub。
    每个 WebSocket 连接调用 subscribe() 获取一个独立队列，
    发布者通过 publish() 向所有队列广播消息。
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """订阅通知，返回消息队列。

        队列 maxsize=256，防止慢消费者拖累整个系统。
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """取消订阅，WebSocket 断开时调用。"""
        with contextlib.suppress(ValueError):
            self._subscribers.remove(queue)

    async def publish(self, notification: Notification) -> None:
        """向所有订阅者广播通知。

        队列满时丢弃本次事件（防止慢速客户端阻塞发布者）。
        """
        data = notification.model_dump()
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(data)

    @property
    def subscriber_count(self) -> int:
        """当前订阅者数量（调试用）。"""
        return len(self._subscribers)


# 全局单例
notification_bus = NotificationBus()
