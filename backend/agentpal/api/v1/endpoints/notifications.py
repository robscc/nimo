"""WebSocket 通知端点 — 向前端推送系统事件。

路径：/api/v1/notifications/ws
协议：WebSocket
消息格式：JSON（Notification.model_dump()）
心跳：每 30 秒无消息时发送 {"type": "ping"}
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from agentpal.services.notification_bus import notification_bus

router = APIRouter()

_HEARTBEAT_INTERVAL = 30.0  # 秒


@router.websocket("/ws")
async def notifications_ws(ws: WebSocket) -> None:
    """WebSocket 端点：订阅全局通知。"""
    await ws.accept()
    queue = notification_bus.subscribe()
    logger.debug(
        "WS notifications 已连接 (subscribers={})",
        notification_bus.subscriber_count,
    )

    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    queue.get(), timeout=_HEARTBEAT_INTERVAL
                )
                await ws.send_json(data)
            except asyncio.TimeoutError:
                # 超时无消息，发送心跳保持连接
                await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("WS notifications 异常断开: {}", e)
    finally:
        notification_bus.unsubscribe(queue)
        logger.debug(
            "WS notifications 已断开 (subscribers={})",
            notification_bus.subscriber_count,
        )
