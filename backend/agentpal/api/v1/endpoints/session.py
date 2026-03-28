"""Session 管理 API。"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.config import get_settings
from agentpal.database import get_db, get_db_standalone, utc_isoformat
from agentpal.memory.factory import MemoryFactory
from agentpal.models.llm_usage import LLMCallLog
from agentpal.models.memory import MemoryRecord
from agentpal.models.session import SessionRecord, SessionStatus, SubAgentTask, TaskStatus
from agentpal.services.session_event_bus import session_event_bus

router = APIRouter()


def _current_model_name() -> str:
    """从 config.yaml 动态读取当前 LLM 模型名。"""
    from agentpal.services.config_file import ConfigFileManager

    cfg = ConfigFileManager().load()
    return cfg.get("llm", {}).get("model", "qwen-max")


# ── Response Models ────────────────────────────────────────


class SessionResponse(BaseModel):
    id: str
    channel: str
    user_id: str | None
    status: str


class SessionSummary(BaseModel):
    id: str
    title: str
    channel: str
    model_name: str | None
    message_count: int
    sub_tasks_count: int
    created_at: str
    updated_at: str


class SessionMeta(BaseModel):
    """Session 元信息，包含模型、上下文大小、工具/技能列表。"""
    id: str
    channel: str
    model_name: str | None
    context_tokens: int | None
    enabled_tools: list[str] | None  # null = 跟随全局
    enabled_skills: list[str] | None  # null = 跟随全局
    tool_guard_threshold: int | None  # null = 跟随全局
    message_count: int
    created_at: str
    updated_at: str


class SessionConfigUpdate(BaseModel):
    """Session 级工具/技能配置更新请求。"""
    enabled_tools: list[str] | None = None  # null = 跟随全局
    enabled_skills: list[str] | None = None  # null = 跟随全局
    # model_name 不再通过 Session 配置，统一从 config.yaml 读取
    tool_guard_threshold: int | None = None  # null = 跟随全局


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: str
    meta: dict | None = None


class CreateSessionResponse(BaseModel):
    id: str


# ── Endpoints ─────────────────────────────────────────────


@router.get("", response_model=list[SessionSummary])
async def list_sessions(
    channel: str = "web",
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """列出指定 channel 的 active session 列表，按 updated_at 倒序。"""
    # 1. 查询 sessions
    result = await db.execute(
        select(SessionRecord)
        .where(SessionRecord.channel == channel, SessionRecord.status == SessionStatus.ACTIVE)
        .order_by(SessionRecord.updated_at.desc())
        .limit(limit)
    )
    sessions = result.scalars().all()

    if not sessions:
        return []

    session_ids = [s.id for s in sessions]

    # 2. 批量查 message_count（避免 N+1）
    count_result = await db.execute(
        select(MemoryRecord.session_id, func.count().label("cnt"))
        .where(MemoryRecord.session_id.in_(session_ids))
        .group_by(MemoryRecord.session_id)
    )
    count_map: dict[str, int] = {row.session_id: row.cnt for row in count_result}

    # 3. 批量查每个 session 的第一条 user 消息（用于生成 title）
    first_msg_result = await db.execute(
        select(MemoryRecord)
        .where(MemoryRecord.session_id.in_(session_ids), MemoryRecord.role == "user")
        .order_by(MemoryRecord.session_id, MemoryRecord.created_at.asc())
    )
    # Python 侧取每个 session 第一条
    first_msg_map: dict[str, str] = {}
    for rec in first_msg_result.scalars().all():
        if rec.session_id not in first_msg_map:
            first_msg_map[rec.session_id] = rec.content

    # 4. 批量查 sub_tasks_count
    sub_tasks_result = await db.execute(
        select(SubAgentTask.parent_session_id, func.count().label("cnt"))
        .where(SubAgentTask.parent_session_id.in_(session_ids))
        .group_by(SubAgentTask.parent_session_id)
    )
    sub_tasks_map: dict[str, int] = {row.parent_session_id: row.cnt for row in sub_tasks_result}

    summaries = []
    current_model = _current_model_name()
    for s in sessions:
        raw_title = first_msg_map.get(s.id, "")
        title = raw_title[:30] + ("…" if len(raw_title) > 30 else "") if raw_title else "新对话"
        summaries.append(
            SessionSummary(
                id=s.id,
                title=title,
                channel=s.channel,
                model_name=current_model,
                message_count=count_map.get(s.id, 0),
                sub_tasks_count=sub_tasks_map.get(s.id, 0),
                created_at=utc_isoformat(s.created_at),
                updated_at=utc_isoformat(s.updated_at),
            )
        )
    return summaries


@router.post("", response_model=CreateSessionResponse, status_code=201)
async def create_session(
    channel: str = "web",
    db: AsyncSession = Depends(get_db),
):
    """创建新 session，返回 id。"""
    session_id = f"{channel}:{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    session = SessionRecord(
        id=session_id,
        channel=channel,
        status=SessionStatus.ACTIVE,
        # model_name 不再持久化到 DB，统一从 config.yaml 读取
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    await db.commit()
    return CreateSessionResponse(id=session_id)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SessionRecord).where(SessionRecord.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse(
        id=session.id,
        channel=session.channel,
        user_id=session.user_id,
        status=session.status,
    )


@router.get("/{session_id}/meta", response_model=SessionMeta)
async def get_session_meta(session_id: str, db: AsyncSession = Depends(get_db)):
    """获取 session 元信息（模型、上下文大小、工具/技能列表）。"""
    result = await db.execute(select(SessionRecord).where(SessionRecord.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 统计消息数
    count_result = await db.execute(
        select(func.count()).where(MemoryRecord.session_id == session_id)
    )
    message_count = count_result.scalar() or 0

    return SessionMeta(
        id=session.id,
        channel=session.channel,
        model_name=_current_model_name(),
        context_tokens=session.context_tokens,
        enabled_tools=session.enabled_tools,
        enabled_skills=session.enabled_skills,
        tool_guard_threshold=session.tool_guard_threshold,
        message_count=message_count,
        created_at=utc_isoformat(session.created_at),
        updated_at=utc_isoformat(session.updated_at),
    )


@router.patch("/{session_id}/config", response_model=SessionMeta)
async def update_session_config(
    session_id: str,
    req: SessionConfigUpdate,
    db: AsyncSession = Depends(get_db_standalone),
):
    """更新 session 级工具/技能配置。

    使用 get_db_standalone（独立短事务），避免与 SSE 流式 chat
    的长事务冲突导致 SQLite "database is locked"。

    规则：
    - enabled_tools/enabled_skills 为 null → 跟随全局配置
    - 为非空列表 → session 使用指定工具，但范围不能超过全局已启用工具
    - 如果 session 配置了全局没有启用的工具/技能，当全局启用后会自动在 session 生效
    """
    result = await db.execute(select(SessionRecord).where(SessionRecord.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if req.enabled_tools is not None:
        session.enabled_tools = req.enabled_tools
    if req.enabled_skills is not None:
        session.enabled_skills = req.enabled_skills
    if req.tool_guard_threshold is not None:
        session.tool_guard_threshold = req.tool_guard_threshold

    session.updated_at = datetime.now(timezone.utc)
    await db.commit()

    # 返回更新后的 meta
    count_result = await db.execute(
        select(func.count()).where(MemoryRecord.session_id == session_id)
    )
    message_count = count_result.scalar() or 0

    return SessionMeta(
        id=session.id,
        channel=session.channel,
        model_name=_current_model_name(),
        context_tokens=session.context_tokens,
        enabled_tools=session.enabled_tools,
        enabled_skills=session.enabled_skills,
        tool_guard_threshold=session.tool_guard_threshold,
        message_count=message_count,
        created_at=utc_isoformat(session.created_at),
        updated_at=utc_isoformat(session.updated_at),
    )


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db_standalone)):
    """将 session 标记为 archived（软删除），同时清除记忆记录。

    使用 get_db_standalone 避免与 SSE 流长事务冲突。
    """
    result = await db.execute(select(SessionRecord).where(SessionRecord.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.status = SessionStatus.ARCHIVED
    # 同时清除记忆，避免孤儿记录
    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)
    await memory.clear(session_id)
    await db.commit()


class SubTaskSummary(BaseModel):
    id: str
    sub_session_id: str
    task_prompt: str
    status: str
    agent_name: str | None
    task_type: str | None
    created_at: str
    finished_at: str | None


@router.get("/{session_id}/sub-tasks", response_model=list[SubTaskSummary])
async def list_session_sub_tasks(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """列出指定主 session 下的所有 SubAgent 任务，按创建时间倒序。"""
    result = await db.execute(
        select(SubAgentTask)
        .where(SubAgentTask.parent_session_id == session_id)
        .order_by(SubAgentTask.created_at.desc())
    )
    tasks = result.scalars().all()
    return [
        SubTaskSummary(
            id=t.id,
            sub_session_id=t.sub_session_id,
            task_prompt=t.task_prompt[:120] + ("…" if len(t.task_prompt) > 120 else ""),
            status=t.status,
            agent_name=t.agent_name,
            task_type=t.task_type,
            created_at=utc_isoformat(t.created_at),
            finished_at=utc_isoformat(t.finished_at) if t.finished_at else None,
        )
        for t in tasks
    ]


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def get_session_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """加载指定 session 的历史消息（user/assistant 角色）。"""
    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)

    # 使用 memory 的 get_recent 方法（支持所有 memory backend）
    messages = await memory.get_recent(session_id, limit=1000)

    # 过滤只返回 user/assistant 角色
    return [
        MessageOut(
            role=str(m.role),
            content=m.content,
            created_at=m.created_at.isoformat() if m.created_at else None,
            meta=m.metadata or None,
        )
        for m in messages
        if str(m.role) in ("user", "assistant")
    ]


@router.delete("/{session_id}/memory")
async def clear_session_memory(session_id: str, db: AsyncSession = Depends(get_db_standalone)):
    """清除指定 session 的全部记忆。

    使用 get_db_standalone 避免与 SSE 流长事务冲突。
    """
    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)
    await memory.clear(session_id)
    await db.commit()
    return {"status": "cleared", "session_id": session_id}


@router.get("/{session_id}/events")
async def session_events(session_id: str, request: Request):
    """SSE 端点：订阅指定 session 的实时消息推送。

    通过 ZMQ EventSubscriber 接收跨进程事件（SubAgent/Cron 完成通知），
    同时保留 session_event_bus 作为进程内事件的补充来源。
    客户端断开时自动取消订阅。
    """
    # 尝试获取 ZMQ manager 用于跨进程事件订阅
    zmq_manager = getattr(request.app.state, "scheduler", None) or getattr(
        request.app.state, "zmq_manager", None
    )

    async def event_generator():
        subscriber = None
        queue = session_event_bus.subscribe(session_id)
        try:
            # 发送初始连接确认
            yield f"data: {json.dumps({'type': 'connected', 'session_id': session_id})}\n\n"

            # 创建 ZMQ 订阅者（如果 ZMQ 可用）
            if zmq_manager is not None:
                subscriber = zmq_manager.create_event_subscriber(
                    topic=f"session:{session_id}",
                    filter_msg_id=None,
                )
                await subscriber._ensure_socket()

            while True:
                try:
                    if subscriber is not None and subscriber._sub is not None:
                        # 同时监听 ZMQ 和 session_event_bus
                        # 优先检查 ZMQ（跨进程事件）
                        if await subscriber._sub.poll(timeout=0):
                            frames = await subscriber._sub.recv_multipart()
                            if len(frames) >= 2:
                                from agentpal.zmq_bus.protocol import Envelope as Env

                                envelope = Env.deserialize(frames[1])
                                event = envelope.payload
                                event_clean = {
                                    k: v
                                    for k, v in event.items()
                                    if not k.startswith("_")
                                }
                                yield f"data: {json.dumps(event_clean, ensure_ascii=False)}\n\n"
                                continue

                    # 检查 session_event_bus（进程内事件）
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                except asyncio.TimeoutError:
                    # 发送心跳保持连接
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            session_event_bus.unsubscribe(session_id, queue)
            if subscriber is not None:
                await subscriber.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class LLMCallItem(BaseModel):
    id: int
    model_name: str
    provider: str
    call_round: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    created_at: str


class SessionUsageResponse(BaseModel):
    session_id: str
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    llm_calls: int
    calls: list[LLMCallItem]


@router.get("/{session_id}/usage", response_model=SessionUsageResponse)
async def get_session_usage(session_id: str, db: AsyncSession = Depends(get_db)):
    """获取指定 session 的 LLM token 用量明细。"""
    result = await db.execute(
        select(LLMCallLog)
        .where(LLMCallLog.session_id == session_id)
        .order_by(LLMCallLog.created_at.asc())
    )
    logs = result.scalars().all()

    total_input = sum(r.input_tokens for r in logs)
    total_output = sum(r.output_tokens for r in logs)

    return SessionUsageResponse(
        session_id=session_id,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_tokens=total_input + total_output,
        llm_calls=len(logs),
        calls=[
            LLMCallItem(
                id=r.id,
                model_name=r.model_name,
                provider=r.provider,
                call_round=r.call_round,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                total_tokens=r.total_tokens,
                created_at=utc_isoformat(r.created_at),
            )
            for r in logs
        ],
    )
