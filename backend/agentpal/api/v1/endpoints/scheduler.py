"""Scheduler 监控 API 端点。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


# ── 响应模型 ──────────────────────────────────────────

class AgentProcessInfoResponse(BaseModel):
    process_id: str
    agent_type: str  # "pa" | "sub_agent" | "cron"
    state: str  # AgentState value
    session_id: str | None = None
    task_id: str | None = None
    agent_name: str | None = None
    os_pid: int | None = None
    started_at: str  # ISO format
    last_active_at: str  # ISO format
    idle_seconds: float
    error: str | None = None


class SchedulerStatsResponse(BaseModel):
    total_processes: int
    pa_count: int
    sub_agent_count: int
    cron_count: int
    by_state: dict[str, int]
    total_memory_mb: float
    uptime_seconds: float


# ── 辅助 ──────────────────────────────────────────────

def _get_scheduler(request: Request) -> Any:
    """从 app.state 获取 AgentScheduler。"""
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler not available")
    return scheduler


# ── 端点 ──────────────────────────────────────────────

@router.get("/agents", response_model=list[AgentProcessInfoResponse])
async def list_agents(request: Request):
    """列出所有活跃 Agent 进程。"""
    scheduler = _get_scheduler(request)
    agents = scheduler.list_agents()
    return [
        AgentProcessInfoResponse(**a.to_dict())
        for a in agents
    ]


@router.get("/stats", response_model=SchedulerStatsResponse)
async def scheduler_stats(request: Request):
    """Scheduler 聚合统计。"""
    scheduler = _get_scheduler(request)
    return SchedulerStatsResponse(**scheduler.get_stats())


@router.get("/events")
async def scheduler_events(request: Request):
    """Scheduler SSE 事件流（状态变更实时推送）。

    目前简单实现：每 5 秒推送一次完整状态快照。
    """
    scheduler = _get_scheduler(request)

    async def event_stream() -> AsyncGenerator[str, None]:
        import asyncio

        try:
            while True:
                agents = scheduler.list_agents()
                data = [a.to_dict() for a in agents]
                event = {"type": "state_snapshot", "agents": data}
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/agents/{identity}/stop")
async def stop_agent(identity: str, request: Request):
    """手动停止一个 Agent 进程。"""
    scheduler = _get_scheduler(request)
    success = await scheduler.stop_agent(identity)
    if not success:
        raise HTTPException(status_code=404, detail=f"Agent '{identity}' not found")
    return {"status": "stopped", "identity": identity}
