"""Agent 对话 API 端点。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.agents.personal_assistant import PersonalAssistant
from agentpal.config import get_settings
from agentpal.database import AsyncSessionLocal, get_db, utc_isoformat
from agentpal.memory.factory import MemoryFactory
from agentpal.models.session import SessionRecord, SessionStatus, SubAgentTask

router = APIRouter()


def _get_zmq_manager(request: Request) -> Any:
    """从 app.state 获取 ZMQ manager（如果可用）。"""
    return getattr(request.app.state, "zmq_manager", None)


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
    images: list[str] | None = None  # base64 data URI 列表（多模态图片输入）


class DispatchRequest(BaseModel):
    parent_session_id: str
    task_prompt: str
    context: dict[str, Any] | None = None
    task_type: str | None = None
    agent_name: str | None = None
    priority: int = Field(default=5, ge=1, le=10, description="任务优先级 1-10（10 最高）")
    max_retries: int = Field(default=3, ge=0, le=10, description="最大重试次数 0-10")
    blocking: bool = Field(default=False, description="是否阻塞等待任务完成")
    wait_seconds: int = Field(default=120, ge=0, description="阻塞模式下的最大等待时间（秒）")


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
async def chat(req: ChatRequest, request: Request):
    """主助手流式对话接口（SSE）。

    优先使用 ZMQ 桥接模式（通过 AgentDaemonManager），
    ZMQ 不可用时回退到直接调用 PersonalAssistant。

    返回 text/event-stream，每个事件为::

        data: {"type": "tool_start", "id": "...", "name": "...", "input": {...}}
        data: {"type": "tool_done",  "id": "...", "name": "...", "output": "...", ...}
        data: {"type": "text_delta", "delta": "..."}
        data: {"type": "done"}
        data: {"type": "error",      "message": "..."}
    """
    # 用短事务完成 session upsert，立即释放连接，避免阻塞 SSE 流期间的其他写操作
    async with AsyncSessionLocal() as db:
        await _ensure_session(db, req.session_id, req.channel)

    zmq_manager = _get_zmq_manager(request)

    if zmq_manager is not None:
        # ── ZMQ 桥接模式 ──────────────────────────────────
        return await _chat_via_zmq(req, zmq_manager)
    else:
        # ── 直接调用模式（回退）──────────────────────────
        return await _chat_direct(req)


async def _chat_via_zmq(req: ChatRequest, zmq_manager: Any) -> StreamingResponse:
    """通过 ZMQ 桥接的 SSE 流式对话。

    流程：
    1. ensure_pa_daemon(session_id) → 确保 PA daemon 运行中
    2. 生成 msg_id = uuid4()
    3. 创建 EventSubscriber(topic="session:{session_id}", filter_msg_id=msg_id)
    4. 发送 CHAT_REQUEST envelope 到 pa:{session_id}
    5. EventSubscriber 异步迭代 → yield SSE events
    6. 收到 done/error → 关闭 SUB socket
    """
    from agentpal.zmq_bus.protocol import Envelope, MessageType

    session_id = req.session_id
    msg_id = str(uuid.uuid4())

    # 1. 确保 PA daemon 运行
    await zmq_manager.ensure_pa_daemon(session_id)

    # 2. 创建事件订阅者
    subscriber = zmq_manager.create_event_subscriber(
        topic=f"session:{session_id}",
        filter_msg_id=msg_id,
    )

    # 3. 发送 CHAT_REQUEST
    envelope = Envelope(
        msg_id=msg_id,
        msg_type=MessageType.CHAT_REQUEST,
        source="api:chat",
        target=f"pa:{session_id}",
        session_id=session_id,
        payload={
            "message": req.message,
            "images": req.images,
            "channel": req.channel,
            "user_id": req.user_id,
        },
    )
    await zmq_manager.send_to_agent(f"pa:{session_id}", envelope)

    # 4. 订阅事件 → SSE 流
    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async with subscriber:
                async for event in subscriber:
                    # 过滤掉内部字段
                    event_clean = {k: v for k, v in event.items() if not k.startswith("_")}
                    event_type = event_clean.get("type", "")

                    # 跳过 heartbeat（SSE 保活由注释行处理）
                    if event_type == "heartbeat":
                        yield ": heartbeat\n\n"
                        continue

                    yield f"data: {json.dumps(event_clean, ensure_ascii=False)}\n\n"

                    # done 或 error → 结束
                    if event_type in ("done", "error"):
                        return
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _chat_direct(req: ChatRequest) -> StreamingResponse:
    """直接调用 PersonalAssistant 的 SSE 流式对话（ZMQ 不可用时回退）。"""
    settings = get_settings()

    async def event_stream() -> AsyncGenerator[str, None]:
        # 在 generator 内部独立管理 session，与 SSE 流同生命周期
        # 不通过 Depends(get_db) 注入，避免持有连接阻塞其他请求
        async with AsyncSessionLocal() as db:
            memory = MemoryFactory.create(settings.memory_backend, db=db)
            assistant = PersonalAssistant(session_id=req.session_id, memory=memory, db=db)
            async for event in assistant.reply_stream(req.message, images=req.images):
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
async def dispatch_sub_agent(req: DispatchRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """派遣 SubAgent 异步执行任务。

    优先通过 ZMQ 派遣（AgentDaemonManager），不可用时回退到直接模式。

    支持两种模式：
    - 非阻塞模式（blocking=False）：立即返回任务 ID 和初始状态
    - 阻塞模式（blocking=True）：等待任务完成后返回最终结果
    """
    zmq_manager = _get_zmq_manager(request)

    if zmq_manager is not None and not req.blocking:
        # ── ZMQ 非阻塞派遣 ──────────────────────────────
        return await _dispatch_via_zmq(req, zmq_manager, db)
    else:
        # ── 直接模式（阻塞模式或 ZMQ 不可用）───────────
        return await _dispatch_direct(req, db)


async def _dispatch_via_zmq(req: DispatchRequest, zmq_manager: Any, db: AsyncSession) -> TaskStatusResponse:
    """通过 ZMQ 非阻塞派遣 SubAgent。"""
    from agentpal.agents.registry import SubAgentRegistry
    from agentpal.models.agent import SubAgentDefinition

    settings = get_settings()
    parent_session_id = req.parent_session_id
    task_id = str(uuid.uuid4())
    agent_name = req.agent_name
    task_type = req.task_type
    role_prompt = ""
    model_config = {
        "provider": settings.llm_provider,
        "model_name": settings.llm_model,
        "api_key": settings.llm_api_key,
        "base_url": settings.llm_base_url,
    }
    max_tool_rounds = 8

    # 查找合适的 SubAgent 定义
    registry = SubAgentRegistry(db)
    agent_def: SubAgentDefinition | None = None

    if agent_name:
        agent_def = await db.get(SubAgentDefinition, agent_name)
    elif task_type:
        agent_def = await registry.find_agent_for_task(task_type)

    if agent_def:
        agent_name = agent_def.name
        role_prompt = agent_def.role_prompt or ""
        model_config = agent_def.get_model_config(model_config)
        max_tool_rounds = agent_def.max_tool_rounds

    # Clamp priority and max_retries
    priority = max(1, min(10, req.priority))
    max_retries = max(0, min(10, req.max_retries))

    # 创建 SubAgentTask 记录
    from agentpal.models.session import TaskStatus

    task = SubAgentTask(
        id=task_id,
        parent_session_id=parent_session_id,
        sub_session_id=f"sub:{parent_session_id}:{task_id}",
        task_prompt=req.task_prompt,
        status=TaskStatus.PENDING,
        agent_name=agent_name,
        task_type=task_type,
        execution_log=[],
        meta=req.context or {},
        priority=priority,
        max_retries=max_retries,
    )
    db.add(task)
    await db.commit()

    # 通过 ZMQ 创建 SubAgent daemon 并派遣任务
    await zmq_manager.create_sub_daemon(
        agent_name=agent_name or "default",
        task_id=task_id,
        task_prompt=req.task_prompt,
        parent_session_id=parent_session_id,
        model_config=model_config,
        role_prompt=role_prompt,
        max_tool_rounds=max_tool_rounds,
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


async def _dispatch_direct(req: DispatchRequest, db: AsyncSession) -> TaskStatusResponse:
    """直接派遣 SubAgent（阻塞模式或 ZMQ 不可用）。"""
    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)
    assistant = PersonalAssistant(session_id=req.parent_session_id, memory=memory, db=db)

    if req.blocking:
        # 阻塞模式：使用工具层面的 dispatch_sub_agent
        from agentpal.tools.builtin import dispatch_sub_agent as builtin_dispatch

        result_text = await builtin_dispatch(
            task_prompt=req.task_prompt,
            parent_session_id=req.parent_session_id,
            task_type=req.task_type or "",
            agent_name=req.agent_name or "",
            wait_seconds=req.wait_seconds,
            blocking=True,
        )

        # 从数据库获取最新的 task 记录
        from agentpal.models.agent import SubAgentTask as AgentSubTask

        result = await db.execute(
            select(AgentSubTask)
            .where(AgentSubTask.parent_session_id == req.parent_session_id)
            .order_by(AgentSubTask.created_at.desc())
            .limit(1)
        )
        task = result.scalars().first()

        return TaskStatusResponse(
            task_id=task.id if task else "unknown",
            status=task.status.value if task else "unknown",
            result=result_text,
            error=getattr(task, "error", None),
            agent_name=getattr(task, "agent_name", None),
            task_type=getattr(task, "task_type", None),
            priority=getattr(task, "priority", 5),
            retry_count=getattr(task, "retry_count", 0),
            max_retries=getattr(task, "max_retries", 3),
            created_at=utc_isoformat(task.created_at) if task else None,
        )
    else:
        # 非阻塞模式：立即返回
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
