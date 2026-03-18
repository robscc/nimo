"""NotificationBus 单元测试。"""

from __future__ import annotations

import asyncio

import pytest

from agentpal.services.notification_bus import (
    Notification,
    NotificationBus,
    NotificationType,
)


def _make_notification(
    ntype: NotificationType = NotificationType.SUBAGENT_TASK_DONE,
    **payload: object,
) -> Notification:
    return Notification(type=ntype, payload=payload or {"task_id": "t1"})


class TestNotificationBus:
    """NotificationBus 核心行为。"""

    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self):
        """订阅后能收到发布的消息。"""
        bus = NotificationBus()
        queue = bus.subscribe()

        notif = _make_notification()
        await bus.publish(notif)

        data = queue.get_nowait()
        assert data["type"] == NotificationType.SUBAGENT_TASK_DONE.value
        assert data["payload"]["task_id"] == "t1"
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_multi_subscriber_broadcast(self):
        """多个订阅者都能收到同一条消息。"""
        bus = NotificationBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        q3 = bus.subscribe()

        await bus.publish(_make_notification())

        for q in (q1, q2, q3):
            data = q.get_nowait()
            assert data["type"] == NotificationType.SUBAGENT_TASK_DONE.value

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        """取消订阅后不再收到消息。"""
        bus = NotificationBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()

        bus.unsubscribe(q1)

        await bus.publish(_make_notification())

        assert q1.empty()
        assert not q2.empty()

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_queue(self):
        """取消一个未订阅的队列不报错。"""
        bus = NotificationBus()
        random_queue: asyncio.Queue = asyncio.Queue()
        bus.unsubscribe(random_queue)  # 不应抛异常

    @pytest.mark.asyncio
    async def test_slow_consumer_drop(self):
        """慢消费者（队列满）时丢弃消息，不阻塞发布。"""
        bus = NotificationBus()
        # 用 maxsize=1 的队列模拟慢消费者
        small_q: asyncio.Queue = asyncio.Queue(maxsize=1)
        bus._subscribers.append(small_q)

        # 第一条应成功入队
        await bus.publish(_make_notification(task_id="first"))
        assert small_q.qsize() == 1

        # 第二条应被丢弃（队列满），不抛异常
        await bus.publish(_make_notification(task_id="second"))
        assert small_q.qsize() == 1  # 仍然是 1

        # 取出的应该是第一条
        data = small_q.get_nowait()
        assert data["payload"]["task_id"] == "first"

    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self):
        """没有订阅者时发布不报错。"""
        bus = NotificationBus()
        await bus.publish(_make_notification())  # 不应抛异常

    @pytest.mark.asyncio
    async def test_subscriber_count(self):
        """subscriber_count 属性正确反映当前订阅者数量。"""
        bus = NotificationBus()
        assert bus.subscriber_count == 0

        q1 = bus.subscribe()
        assert bus.subscriber_count == 1

        q2 = bus.subscribe()
        assert bus.subscriber_count == 2

        bus.unsubscribe(q1)
        assert bus.subscriber_count == 1

        bus.unsubscribe(q2)
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_notification_types(self):
        """所有 NotificationType 都可以正常发布和接收。"""
        bus = NotificationBus()
        queue = bus.subscribe()

        for ntype in NotificationType:
            await bus.publish(Notification(type=ntype, payload={"t": ntype.value}))

        for ntype in NotificationType:
            data = queue.get_nowait()
            assert data["type"] == ntype.value
            assert data["payload"]["t"] == ntype.value

    @pytest.mark.asyncio
    async def test_notification_model_defaults(self):
        """Notification 模型的默认值正确。"""
        notif = Notification(type=NotificationType.CRON_EXECUTION_DONE)
        assert notif.timestamp  # 非空
        assert notif.payload == {}
