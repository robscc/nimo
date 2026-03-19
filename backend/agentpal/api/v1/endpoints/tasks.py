"""SubAgent 任务管理 API 端点。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.database import get_db
from agentpal.models.session import SubAgentTask, TaskArtifact, TaskEvent, TaskStatus

router = APIRouter()


class SubmitUserInputRequest(BaseModel):
    """提交用户输入的请求体。"""

    user_input: str
    continue_execution: bool = True


@router.get("/{task_id}")
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """获取单个任务详情。"""
    result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 '{task_id}' 不存在")
    return task


@router.get("/{task_id}/events")
async def stream_task_events(task_id: str, db: AsyncSession = Depends(get_db)):
    """SSE 流：实时推送 SubAgent 任务事件。

    客户端连接后，会立即收到历史事件，然后持续接收新事件直到任务结束。
    """
    # 验证任务是否存在
    result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 '{task_id}' 不存在")

    from agentpal.services.task_event_bus import task_event_bus

    # 订阅事件总线
    queue = task_event_bus.subscribe(task_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # 先发送历史事件
            history_result = await db.execute(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id)
                .order_by(TaskEvent.created_at.asc())
            )
            history_events = history_result.scalars().all()
            for event in history_events:
                yield f"data: {json.dumps({'event_type': event.event_type, 'event_data': event.event_data or {}, 'message': event.message, 'created_at': event.created_at.isoformat()})}\n\n"

            # 持续监听新事件
            while True:
                try:
                    import asyncio

                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    # 从数据库获取完整的 event 记录（包含 created_at）
                    latest_result = await db.execute(
                        select(TaskEvent)
                        .where(TaskEvent.task_id == task_id, TaskEvent.event_type == event["event_type"])
                        .order_by(TaskEvent.created_at.desc())
                        .limit(1)
                    )
                    latest_event = latest_result.scalar_one_or_none()
                    event_with_time = {
                        "event_type": event["event_type"],
                        "event_data": event["event_data"],
                        "message": event["message"],
                        "created_at": latest_event.created_at.isoformat() if latest_event else None,
                    }
                    yield f"data: {json.dumps(event_with_time)}\n\n"
                except TimeoutError:
                    # 心跳：检查任务状态
                    if task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
                        break
                    # 重新获取任务状态
                    refresh_result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == task_id))
                    refreshed_task = refresh_result.scalar_one_or_none()
                    if refreshed_task and refreshed_task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
                        break
                    continue
                except asyncio.CancelledError:
                    break

        finally:
            # 取消订阅
            task_event_bus.unsubscribe(task_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Nginx: 禁用缓冲
        },
    )


@router.get("/{task_id}/artifacts")
async def list_task_artifacts(task_id: str, db: AsyncSession = Depends(get_db)):
    """列出任务的所有产出物。"""
    # 验证任务是否存在
    result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 '{task_id}' 不存在")

    artifacts_result = await db.execute(
        select(TaskArtifact).where(TaskArtifact.task_id == task_id).order_by(TaskArtifact.created_at.asc())
    )
    artifacts = artifacts_result.scalars().all()
    return artifacts


@router.get("/{task_id}/artifacts/{artifact_id}")
async def get_task_artifact(task_id: str, artifact_id: str, db: AsyncSession = Depends(get_db)):
    """获取单个产出物内容。"""
    result = await db.execute(
        select(TaskArtifact).where(TaskArtifact.id == artifact_id, TaskArtifact.task_id == task_id)
    )
    artifact = result.scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"产出物 '{artifact_id}' 不存在")
    return artifact


@router.post("/{task_id}/input")
async def submit_user_input(
    task_id: str,
    request: SubmitUserInputRequest,
    db: AsyncSession = Depends(get_db),
):
    """向处于 INPUT_REQUIRED 状态的任务提交用户输入并恢复执行。

    Args:
        task_id: 任务 ID
        user_input: 用户提供的输入内容
        continue_execution: 是否继续执行任务（默认 True）

    Returns:
        任务当前状态
    """
    result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 '{task_id}' 不存在")

    if task.status != TaskStatus.INPUT_REQUIRED:
        raise HTTPException(
            status_code=400,
            detail=f"任务当前状态为 '{task.status.value}'，不需要用户输入",
        )

    # 将用户输入存储到任务的 meta 字段中
    if task.meta is None:
        task.meta = {}
    task.meta["user_input"] = request.user_input
    task.meta["user_input_timestamp"] = json.dumps(__import__("datetime").datetime.now().isoformat())

    # 如果 continue_execution 为 True，则将任务状态改为 PENDING 以恢复执行
    if request.continue_execution:
        task.status = TaskStatus.PENDING
        task.meta["resumed_at"] = json.dumps(__import__("datetime").datetime.now(timezone.utc).isoformat())

    await db.commit()

    # 发射恢复事件
    from agentpal.services.task_event_bus import task_event_bus

    asyncio = __import__("asyncio")
    asyncio.create_task(
        task_event_bus.emit(
            task_id,
            "task.resumed",
            {"user_input": request.user_input[:500]},
            "任务已恢复执行",
        )
    )

    return {
        "task_id": task_id,
        "status": task.status.value,
        "message": "用户输入已提交，任务已恢复执行" if request.continue_execution else "用户输入已提交，任务保持暂停",
    }


# ──────────────────────────────────────────────────────────
# Artifact 相关 API
# ──────────────────────────────────────────────────────────


class ArtifactCreate(BaseModel):
    """创建产出物的请求体。"""

    task_id: str
    name: str
    artifact_type: str = "text"
    content: str | None = None
    file_path: str | None = None
    mime_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/artifacts", response_model=dict[str, str], tags=["artifacts"])
async def create_artifact(request: ArtifactCreate, db: AsyncSession = Depends(get_async_db)) -> dict[str, str]:
    """为任务创建产出物（代码、报告、图表等）。"""
    import uuid

    from agentpal.models.session import TaskArtifact

    artifact_id = str(uuid.uuid4())
    artifact = TaskArtifact(
        id=artifact_id,
        task_id=request.task_id,
        name=request.name,
        artifact_type=request.artifact_type,
        content=request.content,
        file_path=request.file_path,
        mime_type=request.mime_type,
        metadata=request.metadata,
    )
    db.add(artifact)
    await db.commit()

    # Emit event
    from agentpal.services.task_event_bus import task_event_bus

    asyncio.create_task(
        task_event_bus.emit(
            request.task_id,
            "task.artifact_created",
            {"artifact_id": artifact_id, "name": request.name},
            f"已创建产出物：{request.name}",
        )
    )

    return {"artifact_id": artifact_id, "status": "created"}


@router.get("/{task_id}/artifacts", response_model=list[dict[str, Any]], tags=["artifacts"])
async def list_artifacts(task_id: str, db: AsyncSession = Depends(get_async_db)) -> list[dict[str, Any]]:
    """获取任务的所有产出物列表。"""
    from sqlalchemy import select

    from agentpal.models.session import TaskArtifact

    result = await db.execute(select(TaskArtifact).where(TaskArtifact.task_id == task_id).order_by(TaskArtifact.created_at.desc()))
    artifacts = result.scalars().all()

    return [
        {
            "id": a.id,
            "name": a.name,
            "artifact_type": a.artifact_type,
            "mime_type": a.mime_type,
            "size_bytes": a.size_bytes,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in artifacts
    ]


@router.get("/artifacts/{artifact_id}", response_model=dict[str, Any], tags=["artifacts"])
async def get_artifact(artifact_id: str, db: AsyncSession = Depends(get_async_db)) -> dict[str, Any]:
    """获取单个产出物的详细内容。"""
    from sqlalchemy import select

    from agentpal.models.session import TaskArtifact

    result = await db.execute(select(TaskArtifact).where(TaskArtifact.id == artifact_id))
    artifact = result.scalar_one_or_none()

    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    return {
        "id": artifact.id,
        "task_id": artifact.task_id,
        "name": artifact.name,
        "artifact_type": artifact.artifact_type,
        "content": artifact.content,
        "file_path": artifact.file_path,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "extra": artifact.extra,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
    }


# ──────────────────────────────────────────────────────────
# Task Cancel API
# ──────────────────────────────────────────────────────────


class CancelTaskRequest(BaseModel):
    """取消任务的请求体。"""

    reason: str | None = "用户取消"


@router.post("/{task_id}/cancel", response_model=dict[str, Any], tags=["tasks"])
async def cancel_task(
    task_id: str,
    request: CancelTaskRequest | None = None,
    db: AsyncSession = Depends(get_async_db),
) -> dict[str, Any]:
    """取消正在运行的 SubAgent 任务。"""
    from sqlalchemy import select

    from agentpal.models.session import SubAgentTask, TaskStatus

    result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == task_id))
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # 只有运行中或暂停的任务可以取消
    if task.status not in (TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.PENDING, TaskStatus.INPUT_REQUIRED):
        return {
            "task_id": task_id,
            "status": task.status.value,
            "message": f"任务状态为 {task.status.value}，无需取消",
        }

    # 更新任务状态
    task.status = TaskStatus.CANCELLED
    if task.meta is None:
        task.meta = {}
    task.meta["cancel_reason"] = request.reason if request else "用户取消"
    task.meta["cancelled_at"] = datetime.now(timezone.utc).isoformat()
    task.finished_at = datetime.now(timezone.utc)

    await db.commit()

    # 发射事件
    from agentpal.services.task_event_bus import task_event_bus

    asyncio.create_task(
        task_event_bus.emit(
            task_id,
            "task.cancelled",
            {"reason": task.meta["cancel_reason"]},
            f"任务已取消：{task.meta['cancel_reason']}",
        )
    )

    return {
        "task_id": task_id,
        "status": TaskStatus.CANCELLED.value,
        "message": "任务已成功取消",
    }


