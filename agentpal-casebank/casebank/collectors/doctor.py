"""Connectivity diagnostics for AgentPal data sources."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _ws_url_from_base(base_url: str) -> str:
    root = base_url
    if "/api/v1" in root:
        root = root.split("/api/v1", maxsplit=1)[0]
    if root.startswith("https://"):
        root = "wss://" + root[len("https://") :]
    elif root.startswith("http://"):
        root = "ws://" + root[len("http://") :]
    return root.rstrip("/") + "/api/v1/notifications/ws"


async def run_doctor(base_url: str, timeout_seconds: int = 8) -> list[CheckResult]:
    """Run REST/SSE/WS connectivity checks."""

    results: list[CheckResult] = []

    try:
        import httpx
    except Exception as exc:
        results.append(CheckResult(name="rest_dependencies", ok=False, detail=f"httpx unavailable: {exc}"))
        return results

    try:
        from casebank.collectors.sse_client import SSEClient
    except Exception as exc:
        results.append(CheckResult(name="sse_dependencies", ok=False, detail=f"SSE client unavailable: {exc}"))
        return results
    timeout = httpx.Timeout(timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for name, path in [
            ("rest_sessions", "/sessions?limit=1"),
            ("rest_tools_logs", "/tools/logs?limit=1"),
            ("rest_scheduler_stats", "/scheduler/stats"),
        ]:
            url = f"{base_url.rstrip('/')}{path}"
            try:
                resp = await client.get(url)
                if resp.status_code < 400:
                    results.append(CheckResult(name=name, ok=True, detail=f"HTTP {resp.status_code}"))
                else:
                    results.append(CheckResult(name=name, ok=False, detail=f"HTTP {resp.status_code}"))
            except Exception as exc:
                results.append(CheckResult(name=name, ok=False, detail=str(exc)))

    # SSE check (scheduler events should emit snapshot periodically)
    sse = SSEClient(timeout_seconds=timeout_seconds)
    try:
        event = await asyncio.wait_for(
            _read_first_sse_event(sse, f"{base_url.rstrip('/')}/scheduler/events"),
            timeout=timeout_seconds + 2,
        )
        results.append(CheckResult(name="sse_scheduler_events", ok=True, detail=f"received: {list(event.keys())}"))
    except Exception as exc:
        results.append(CheckResult(name="sse_scheduler_events", ok=False, detail=str(exc)))

    # WS check (notifications)
    try:
        import websockets

        ws_url = _ws_url_from_base(base_url)
        async with websockets.connect(ws_url) as ws:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout_seconds)
            results.append(CheckResult(name="ws_notifications", ok=True, detail=f"received: {str(msg)[:120]}"))
    except Exception as exc:
        results.append(CheckResult(name="ws_notifications", ok=False, detail=str(exc)))

    return results


async def _read_first_sse_event(sse, url: str) -> dict:
    async for event in sse.iter_events(url):
        return event
    raise RuntimeError("no SSE event received")
