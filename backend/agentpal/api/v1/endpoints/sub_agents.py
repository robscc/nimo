"""SubAgent 管理 API 端点。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.agents.registry import SubAgentRegistry
from agentpal.database import get_db

router = APIRouter()


class SubAgentCreate(BaseModel):
    name: str
    display_name: str = ""
    role_prompt: str = ""
    accepted_task_types: list[str] = []
    model_name: str | None = None
    model_provider: str | None = None
    model_api_key: str | None = None
    model_base_url: str | None = None
    max_tool_rounds: int = 8
    timeout_seconds: int = 300
    enabled: bool = True


class SubAgentUpdate(BaseModel):
    display_name: str | None = None
    role_prompt: str | None = None
    accepted_task_types: list[str] | None = None
    model_name: str | None = None
    model_provider: str | None = None
    model_api_key: str | None = None
    model_base_url: str | None = None
    max_tool_rounds: int | None = None
    timeout_seconds: int | None = None
    enabled: bool | None = None


@router.get("")
async def list_sub_agents(db: AsyncSession = Depends(get_db)):
    """列出所有 SubAgent 定义。"""
    registry = SubAgentRegistry(db)
    await registry.ensure_defaults()
    await db.commit()
    return await registry.list_agents()


@router.get("/{name}")
async def get_sub_agent(name: str, db: AsyncSession = Depends(get_db)):
    """获取单个 SubAgent 定义。"""
    registry = SubAgentRegistry(db)
    agent = await registry.get_agent(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"SubAgent '{name}' 不存在")
    return agent


@router.post("", status_code=201)
async def create_sub_agent(data: SubAgentCreate, db: AsyncSession = Depends(get_db)):
    """创建新的 SubAgent。"""
    registry = SubAgentRegistry(db)
    try:
        result = await registry.create_agent(data.model_dump())
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{name}")
async def update_sub_agent(
    name: str, data: SubAgentUpdate, db: AsyncSession = Depends(get_db)
):
    """更新 SubAgent 配置。"""
    registry = SubAgentRegistry(db)
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    result = await registry.update_agent(name, update_data)
    if result is None:
        raise HTTPException(status_code=404, detail=f"SubAgent '{name}' 不存在")
    await db.commit()
    return result


@router.delete("/{name}", status_code=204)
async def delete_sub_agent(name: str, db: AsyncSession = Depends(get_db)):
    """删除 SubAgent。"""
    registry = SubAgentRegistry(db)
    ok = await registry.delete_agent(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"SubAgent '{name}' 不存在")
    await db.commit()
