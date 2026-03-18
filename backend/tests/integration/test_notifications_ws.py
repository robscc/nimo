"""WebSocket 通知端点集成测试。

使用 Starlette TestClient 的 websocket_connect() 测试 WS 连接、消息接收、心跳。
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time

import pytest
from starlette.testclient import TestClient

from agentpal.database import get_db, get_db_standalone
from agentpal.main import create_app
from agentpal.services.notification_bus import (
    Notification,
    NotificationType,
    notification_bus,
)

# ── 测试数据库覆盖（复用现有模式）─────────────────────────────


@pytest.fixture
def test_app():
    """创建测试应用（同步 fixture，配合 Starlette TestClient）。"""
    app = create_app()

    # 注意：WebSocket 测试用 Starlette TestClient，不需要真正的 DB 操作，
    # 但仍需覆盖依赖以避免连接真实数据库。
    async def override_db():
        yield None

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_db_standalone] = override_db
    return app


# ── WebSocket 端点测试 ────────────────────────────────────────


class TestNotificationsWebSocket:
    """WebSocket /api/v1/notifications/ws 端点测试。"""

    def test_ws_connect_and_receive_ping(self, test_app):
        """连接成功后，超时时应收到 ping 心跳。"""
        # 使用极短的心跳间隔来加速测试
        import agentpal.api.v1.endpoints.notifications as notifications_mod

        original_interval = notifications_mod._HEARTBEAT_INTERVAL
        notifications_mod._HEARTBEAT_INTERVAL = 0.5  # 500ms

        try:
            client = TestClient(test_app)
            with client.websocket_connect("/api/v1/notifications/ws") as ws:
                data = ws.receive_json(mode="text")
                assert data["type"] == "ping"
        finally:
            notifications_mod._HEARTBEAT_INTERVAL = original_interval

    def test_ws_receive_notification(self, test_app):
        """发布通知后，WebSocket 客户端应收到消息。"""
        import agentpal.api.v1.endpoints.notifications as notifications_mod

        original_interval = notifications_mod._HEARTBEAT_INTERVAL
        notifications_mod._HEARTBEAT_INTERVAL = 5.0  # 足够长，不会收到 ping

        try:
            client = TestClient(test_app)
            with client.websocket_connect("/api/v1/notifications/ws") as ws:
                # 在另一个线程中发布通知（TestClient 在后台线程运行 ASGI app）
                def publish_later():
                    time.sleep(0.3)
                    # TestClient 运行在自己的 event loop 里，
                    # 但 notification_bus 的 put_nowait 是线程安全的
                    notif = Notification(
                        type=NotificationType.SUBAGENT_TASK_DONE,
                        payload={"task_id": "test-123", "status": "done"},
                    )
                    # 直接调用 put_nowait 绕过 async
                    data = notif.model_dump()
                    for q in list(notification_bus._subscribers):
                        with contextlib.suppress(asyncio.QueueFull):
                            q.put_nowait(data)

                t = threading.Thread(target=publish_later)
                t.start()

                data = ws.receive_json(mode="text")
                assert data["type"] == "subagent_task_done"
                assert data["payload"]["task_id"] == "test-123"

                t.join()
        finally:
            notifications_mod._HEARTBEAT_INTERVAL = original_interval

    def test_ws_subscriber_cleanup_on_disconnect(self, test_app):
        """WebSocket 断开后，订阅者应被清理。"""
        initial_count = notification_bus.subscriber_count

        client = TestClient(test_app)
        with client.websocket_connect("/api/v1/notifications/ws"):
            # 连接期间，订阅者计数应增加
            # 注意：由于 TestClient 在后台线程运行，可能需要短暂等待
            time.sleep(0.1)
            assert notification_bus.subscriber_count >= initial_count + 1

        # 断开后等待清理
        time.sleep(0.2)
        assert notification_bus.subscriber_count == initial_count
