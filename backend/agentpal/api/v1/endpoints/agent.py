"""Agent 对话 API 端点。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.agents.personal_assistant import PersonalAssistant
from agentpal.config import get_settings
from agentpal.database import get_db, utc_isoformat
from agentpal.memory.factory import MemoryFactory
from agentpal.models.session import SessionRecord, SessionStatus, SubAgentTask

router = APIRouter()


async def _ensure_session(db: AsyncSession, session_id: str, channel: str) -> None:
    """Upsert SessionRecord，确保 session 始终出现在列表中。"""
    now = datetime.now(timezone.utc)
    stmt = (
        sqlite_insert(SessionRecord)
        .values(
            id=session_id,
            channel=channel,
            status=SessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"updated_at": now},
        )
    )
    await db.execute(stmt)
    await db.commit()


class ChatRequest(BaseModel):
    session_id: str
    message: str
    channel: str = "web"
    user_id: str = "anonymous"


class DispatchRequest(BaseModel):
    parent_session_id: str
    task_prompt: str
    context: dict[str, Any] | None = None
    task_type: str | None = None
    agent_name: str | None = None


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: str | None
    error: str | None
    agent_name: str | None = None
    task_type: str | None = None


class TaskListItem(BaseModel):
    task_id: str
    status: str
    agent_name: str | None
    task_type: str | None
    task_prompt: str
    parent_session_id: str
    result: str | None
    error: str | None
    created_at: str
    finished_at: str | None


@router.post("/chat")
async def chat(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    """主助手流式对话接口（SSE）。

    返回 text/event-stream，每个事件为::

        data: {"type": "tool_start", "id": "...", "name": "...", "input": {...}}
        data: {"type": "tool_done",  "id": "...", "name": "...", "output": "...", ...}
        data: {"type": "text_delta", "delta": "..."}
        data: {"type": "done"}
        data: {"type": "error",      "message": "..."}
    """
    await _ensure_session(db, req.session_id, req.channel)

    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)
    assistant = PersonalAssistant(session_id=req.session_id, memory=memory, db=db)

    async def event_stream() -> AsyncGenerator[str, None]:
        async for event in assistant.reply_stream(req.message):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            # send_file_to_user 成功 → 额外 emit file 事件
            if (
                event.get("type") == "tool_done"
                and event.get("name") == "send_file_to_user"
                and not event.get("error")
            ):
                try:
                    info = json.loads(event.get("output", "{}"))
                    if info.get("status") == "sent":
                        file_event = {
                            "type": "file",
                            "url": info["url"],
                            "name": info["filename"],
                            "mime": info.get("mime", "application/octet-stream"),
                        }
                        yield f"data: {json.dumps(file_event, ensure_ascii=False)}\n\n"
                except Exception:
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


@router.post("/dispatch", response_model=TaskStatusResponse)
async def dispatch_sub_agent(req: DispatchRequest, db: AsyncSession = Depends(get_db)):
    """派遣 SubAgent 异步执行任务。"""
    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)
    assistant = PersonalAssistant(session_id=req.parent_session_id, memory=memory, db=db)
    task = await assistant.dispatch_sub_agent(
        task_prompt=req.task_prompt,
        db=db,
        context=req.context,
        task_type=req.task_type,
        agent_name=req.agent_name,
    )
    return TaskStatusResponse(
        task_id=task.id,
        status=task.status,
        result=task.result,
        error=task.error,
        agent_name=task.agent_name,
        task_type=task.task_type,
    )


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    """查询 SubAgent 任务状态。"""
    result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(
        task_id=task.id,
        status=task.status,
        result=task.result,
        error=task.error,
        agent_name=task.agent_name,
        task_type=task.task_type,
    )


@router.get("/tasks", response_model=list[TaskListItem])
async def list_tasks(
    status: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """列出所有 SubAgent 历史任务，按创建时间倒序。

    Args:
        status: 可选过滤状态（pending/running/done/failed/cancelled）
        limit: 最多返回条数（默认 100）
    """
    stmt = select(SubAgentTask).order_by(SubAgentTask.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(SubAgentTask.status == status)
    result = await db.execute(stmt)
    tasks = result.scalars().all()
    return [
        TaskListItem(
            task_id=t.id,
            status=t.status,
            agent_name=t.agent_name,
            task_type=t.task_type,
            task_prompt=t.task_prompt,
            parent_session_id=t.parent_session_id,
            result=t.result,
            error=t.error,
            created_at=utc_isoformat(t.created_at),
            finished_at=utc_isoformat(t.finished_at) if t.finished_at else None,
        )
        for t in tasks
    ]


# ── Tool Guard ────────────────────────────────────────────


class ToolGuardResolveRequest(BaseModel):
    approved: bool


@router.post("/tool-guard/{request_id}/resolve")
async def resolve_tool_guard(request_id: str, req: ToolGuardResolveRequest):
    """用户确认或拒绝工具调用安全请求。"""
    from agentpal.tools.tool_guard import ToolGuardManager

    guard = ToolGuardManager.get_instance()
    if not guard.resolve(request_id, req.approved):
        raise HTTPException(status_code=404, detail="Guard request not found or expired")
    return {"status": "ok", "request_id": request_id, "approved": req.approved}
