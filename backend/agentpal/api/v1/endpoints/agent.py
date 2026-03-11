"""Agent 对话 API 端点。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.agents.personal_assistant import PersonalAssistant
from agentpal.config import get_settings
from agentpal.database import get_db
from agentpal.memory.factory import MemoryFactory
from agentpal.models.session import SubAgentTask

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str
    message: str
    channel: str = "web"
    user_id: str = "anonymous"


class DispatchRequest(BaseModel):
    parent_session_id: str
    task_prompt: str
    context: dict[str, Any] | None = None


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: str | None
    error: str | None


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
    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)
    assistant = PersonalAssistant(session_id=req.session_id, memory=memory, db=db)

    async def event_stream() -> AsyncGenerator[str, None]:
        async for event in assistant.reply_stream(req.message):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

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
    )
    return TaskStatusResponse(
        task_id=task.id,
        status=task.status,
        result=task.result,
        error=task.error,
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
    )
