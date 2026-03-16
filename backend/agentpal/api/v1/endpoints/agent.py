"""Agent 对话 API 端点。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
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
    priority: int = Field(default=5, ge=1, le=10, description="任务优先级 1-10（10 最高）")
    max_retries: int = Field(default=3, ge=0, le=10, description="最大重试次数 0-10")


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: str | None
    error: str | None
    agent_name: str | None = None
    task_type: str | None = None
    priority: int = 5
    retry_count: int = 0
    max_retries: int = 3
    created_at: str | None = None


class TaskListResponse(BaseModel):
    items: list[TaskStatusResponse]
    total: int
    limit: int
    offset: int


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
        priority=req.priority,
        max_retries=req.max_retries,
    )
    return TaskStatusResponse(
        task_id=task.id,
        status=task.status,
        result=task.result,
        error=task.error,
        agent_name=task.agent_name,
        task_type=task.task_type,
        priority=task.priority,
        retry_count=task.retry_count,
        max_retries=task.max_retries,
        created_at=utc_isoformat(task.created_at),
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
        priority=task.priority,
        retry_count=task.retry_count,
        max_retries=task.max_retries,
        created_at=utc_isoformat(task.created_at),
    )


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    status: str | None = Query(None, description="按状态过滤"),
    priority_min: int | None = Query(None, ge=1, le=10, description="最低优先级"),
    priority_max: int | None = Query(None, ge=1, le=10, description="最高优先级"),
    parent_session_id: str | None = Query(None, description="按父会话过滤"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """列出 SubAgent 任务，支持状态/优先级/分页过滤。"""
    from sqlalchemy import func

    # 构建查询条件
    query = select(SubAgentTask)
    count_query = select(func.count()).select_from(SubAgentTask)

    if status:
        query = query.where(SubAgentTask.status == status)
        count_query = count_query.where(SubAgentTask.status == status)
    if priority_min is not None:
        query = query.where(SubAgentTask.priority >= priority_min)
        count_query = count_query.where(SubAgentTask.priority >= priority_min)
    if priority_max is not None:
        query = query.where(SubAgentTask.priority <= priority_max)
        count_query = count_query.where(SubAgentTask.priority <= priority_max)
    if parent_session_id:
        query = query.where(SubAgentTask.parent_session_id == parent_session_id)
        count_query = count_query.where(SubAgentTask.parent_session_id == parent_session_id)

    # 计算总数
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 排序：优先级高 → 创建时间新
    query = query.order_by(
        SubAgentTask.priority.desc(),
        SubAgentTask.created_at.desc(),
    ).offset(offset).limit(limit)

    result = await db.execute(query)
    tasks = result.scalars().all()

    items = [
        TaskStatusResponse(
            task_id=t.id,
            status=t.status,
            result=t.result,
            error=t.error,
            agent_name=t.agent_name,
            task_type=t.task_type,
            priority=t.priority,
            retry_count=t.retry_count,
            max_retries=t.max_retries,
            created_at=utc_isoformat(t.created_at),
        )
        for t in tasks
    ]

    return TaskListResponse(items=items, total=total, limit=limit, offset=offset)



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
