"""Tools 管理 API — 列出工具、启用/禁用、查看调用日志。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.database import get_db
from agentpal.tools.registry import (
    get_tool_logs,
    list_tool_configs,
    set_tool_enabled,
)

router = APIRouter()


class ToolInfo(BaseModel):
    name: str
    description: str
    icon: str
    dangerous: bool
    enabled: bool


class ToggleRequest(BaseModel):
    enabled: bool


@router.get("", response_model=list[ToolInfo])
async def list_tools(db: AsyncSession = Depends(get_db)):
    """列出所有内置工具及其启用状态。"""
    configs = await list_tool_configs(db)
    return [ToolInfo(**c) for c in configs]


@router.patch("/{name}")
async def toggle_tool(name: str, req: ToggleRequest, db: AsyncSession = Depends(get_db)):
    """启用或禁用指定工具。"""
    try:
        config = await set_tool_enabled(db, name, req.enabled)
        return {"name": config.name, "enabled": config.enabled}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/logs")
async def tool_logs(
    tool_name: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """查询工具调用日志（可按工具名筛选）。"""
    return await get_tool_logs(db, tool_name=tool_name, limit=min(limit, 200))
