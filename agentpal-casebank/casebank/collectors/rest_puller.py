"""REST pull helpers for backfill and reconciliation."""

from __future__ import annotations

from typing import Any

import httpx


class RestPuller:
    """Thin typed wrapper around AgentPal APIs."""

    def __init__(self, base_url: str, timeout_seconds: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            return response.json()

    async def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._get("/sessions", params={"channel": "web", "limit": limit})

    async def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/sessions/{session_id}/messages")

    async def get_session_usage(self, session_id: str) -> dict[str, Any]:
        return await self._get(f"/sessions/{session_id}/usage")

    async def list_session_sub_tasks(self, session_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/sessions/{session_id}/sub-tasks")

    async def list_agent_tasks(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        return await self._get("/agent/tasks", params={"limit": limit, "offset": offset})

    async def get_task(self, task_id: str) -> dict[str, Any]:
        return await self._get(f"/tasks/{task_id}")

    async def list_tool_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        return await self._get("/tools/logs", params={"limit": limit})

    async def list_cron_executions(self, limit: int = 200) -> list[dict[str, Any]]:
        return await self._get("/cron/executions/all", params={"limit": limit})

    async def get_cron_execution_detail(self, execution_id: str) -> dict[str, Any]:
        return await self._get(f"/cron/executions/{execution_id}/detail")

    async def get_scheduler_stats(self) -> dict[str, Any]:
        return await self._get("/scheduler/stats")
