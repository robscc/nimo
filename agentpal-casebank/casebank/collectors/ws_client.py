"""WebSocket consumer for notifications."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import websockets


class WSClient:
    """Simple reconnecting websocket subscriber."""

    async def consume_forever(
        self,
        url: str,
        callback: Callable[[dict], Awaitable[None]],
        reconnect_delay_seconds: int = 3,
    ) -> None:
        while True:
            try:
                async with websockets.connect(url) as ws:
                    async for message in ws:
                        try:
                            payload = json.loads(message)
                        except json.JSONDecodeError:
                            payload = {"raw": message}
                        await callback(payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(reconnect_delay_seconds)
