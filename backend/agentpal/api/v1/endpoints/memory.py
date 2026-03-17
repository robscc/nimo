"""Memory 跨 session 查询 API。

提供跨 session 的记忆搜索能力，支持基于权限范围的记忆检索。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.config import get_settings
from agentpal.database import get_db, utc_isoformat
from agentpal.memory.base import MemoryAccessLevel, MemoryScope
from agentpal.memory.factory import MemoryFactory

router = APIRouter()


# ── Request / Response Models ─────────────────────────────


class MemorySearchRequest(BaseModel):
    """跨 session 记忆搜索请求。"""

    query: str
    session_id: str | None = None
    user_id: str | None = None
    channel: str | None = None
    global_access: bool = False
    limit: int = 10
    memory_type: str | None = None  # 过滤记忆类型：conversation/personal/task/tool


class MemorySearchResult(BaseModel):
    """记忆搜索结果项。"""

    id: str | None
    session_id: str
    role: str
    content: str
    created_at: str | None
    user_id: str | None = None
    channel: str | None = None
    memory_type: str = "conversation"
    metadata: dict[str, Any] | None = None


class MemorySearchResponse(BaseModel):
    """记忆搜索响应。"""

    results: list[MemorySearchResult]
    total: int
    scope: str  # 实际使用的权限级别


# ── Endpoints ─────────────────────────────────────────────


@router.post("/search", response_model=MemorySearchResponse)
async def search_memory(
    req: MemorySearchRequest,
    db: AsyncSession = Depends(get_db),
):
    """跨 session 记忆搜索。

    根据 MemoryScope 权限范围搜索记忆：
    - 指定 session_id: 仅搜索该 session（SESSION 级别）
    - 指定 user_id: 搜索该用户所有 session（USER 级别）
    - 指定 channel: 搜索该渠道所有 session（CHANNEL 级别）
    - global_access=true: 全局搜索（GLOBAL 级别，需管理员权限）
    - 至少指定一个范围参数

    示例请求：
        POST /api/v1/memory/search
        {"query": "天气", "user_id": "user-123", "limit": 5}
    """
    # 构建 MemoryScope
    scope = MemoryScope(
        session_id=req.session_id,
        user_id=req.user_id,
        channel=req.channel,
        global_access=req.global_access,
    )

    try:
        scope.validate()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 权限检查：GLOBAL 级别需要特殊权限（当前简单限制）
    if scope.access_level == MemoryAccessLevel.GLOBAL and not req.global_access:
        raise HTTPException(status_code=403, detail="全局搜索需要显式指定 global_access=true")

    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)

    results = await memory.cross_session_search(scope, req.query, limit=req.limit)

    # 可选：按 memory_type 过滤
    if req.memory_type:
        results = [r for r in results if r.memory_type == req.memory_type]

    return MemorySearchResponse(
        results=[
            MemorySearchResult(
                id=msg.id,
                session_id=msg.session_id,
                role=str(msg.role),
                content=msg.content,
                created_at=utc_isoformat(msg.created_at),
                user_id=msg.user_id,
                channel=msg.channel,
                memory_type=msg.memory_type,
                metadata=msg.metadata if msg.metadata else None,
            )
            for msg in results
        ],
        total=len(results),
        scope=scope.access_level.value,
    )


@router.get("/sessions/{session_id}/search", response_model=MemorySearchResponse)
async def search_session_memory(
    session_id: str,
    query: str = Query(..., description="搜索关键词"),
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """单 session 内记忆搜索（GET 快捷接口）。

    相比 POST /search 更简单，适合前端直接调用。
    """
    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)

    results = await memory.search(session_id, query, limit=limit)

    return MemorySearchResponse(
        results=[
            MemorySearchResult(
                id=msg.id,
                session_id=msg.session_id,
                role=str(msg.role),
                content=msg.content,
                created_at=utc_isoformat(msg.created_at),
                user_id=msg.user_id,
                channel=msg.channel,
                memory_type=msg.memory_type,
                metadata=msg.metadata if msg.metadata else None,
            )
            for msg in results
        ],
        total=len(results),
        scope=MemoryAccessLevel.SESSION.value,
    )
