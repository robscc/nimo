"""Minimal SSE client built on httpx streaming."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx


class SSEClient:
    """Simple SSE subscriber with reconnect-friendly generator API."""

    def __init__(self, timeout_seconds: int = 20) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)

    async def iter_events(
        self,
        url: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield JSON objects from SSE `data:` lines.

        Non-JSON payloads are wrapped in `{\"raw\": ...}`.
        """

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(method, url, params=params, json=json_body) as response:
                response.raise_for_status()

                buffer: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith(":"):
                        # heartbeat
                        continue
                    if not line:
                        if not buffer:
                            continue
                        payload = "\n".join(buffer)
                        buffer.clear()
                        try:
                            yield json.loads(payload)
                        except json.JSONDecodeError:
                            yield {"raw": payload}
                        continue
                    if line.startswith("data:"):
                        buffer.append(line[len("data:") :].strip())

    async def consume_forever(
        self,
        url: str,
        callback,
        reconnect_delay_seconds: int = 3,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> None:
        """Continuously consume SSE and invoke callback(event)."""

        while True:
            try:
                async for event in self.iter_events(
                    url=url,
                    method=method,
                    params=params,
                    json_body=json_body,
                ):
                    await callback(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(reconnect_delay_seconds)
