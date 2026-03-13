"""Cron 定时任务管理 API 端点。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.database import get_db
from agentpal.services.cron_scheduler import CronManager

router = APIRouter()


class CronJobCreate(BaseModel):
    name: str
    schedule: str  # cron 表达式，如 "0 9 * * *"
    task_prompt: str
    agent_name: str | None = None
    enabled: bool = True
    notify_main: bool = True


class CronJobUpdate(BaseModel):
    name: str | None = None
    schedule: str | None = None
    task_prompt: str | None = None
    agent_name: str | None = None
    enabled: bool | None = None
    notify_main: bool | None = None


class CronJobToggle(BaseModel):
    enabled: bool


# ── CRUD ──────────────────────────────────────────────────


@router.get("")
async def list_cron_jobs(db: AsyncSession = Depends(get_db)):
    """列出所有定时任务。"""
    mgr = CronManager(db)
    return await mgr.list_jobs()


@router.get("/{job_id}")
async def get_cron_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """获取单个定时任务。"""
    mgr = CronManager(db)
    job = await mgr.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return job


@router.post("", status_code=201)
async def create_cron_job(data: CronJobCreate, db: AsyncSession = Depends(get_db)):
    """创建定时任务。"""
    mgr = CronManager(db)
    try:
        result = await mgr.create_job(data.model_dump())
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{job_id}")
async def update_cron_job(
    job_id: str, data: CronJobUpdate, db: AsyncSession = Depends(get_db)
):
    """更新定时任务。"""
    mgr = CronManager(db)
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    try:
        result = await mgr.update_job(job_id, update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    await db.commit()
    return result


@router.delete("/{job_id}", status_code=204)
async def delete_cron_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """删除定时任务。"""
    mgr = CronManager(db)
    ok = await mgr.delete_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    await db.commit()


@router.patch("/{job_id}/toggle")
async def toggle_cron_job(
    job_id: str, data: CronJobToggle, db: AsyncSession = Depends(get_db)
):
    """启用/禁用定时任务。"""
    mgr = CronManager(db)
    result = await mgr.toggle_job(job_id, data.enabled)
    if result is None:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    await db.commit()
    return result


# ── 执行记录 ──────────────────────────────────────────────


@router.get("/{job_id}/executions")
async def list_cron_executions(
    job_id: str,
    limit: int = Query(default=20, le=100),
    db: AsyncSession = Depends(get_db),
):
    """列出定时任务的执行记录。"""
    mgr = CronManager(db)
    return await mgr.list_executions(cron_job_id=job_id, limit=limit)


@router.get("/executions/all")
async def list_all_executions(
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """列出所有执行记录。"""
    mgr = CronManager(db)
    return await mgr.list_executions(limit=limit)


@router.get("/executions/{execution_id}/detail")
async def get_execution_detail(
    execution_id: str, db: AsyncSession = Depends(get_db)
):
    """获取执行记录详情（含完整日志）。"""
    mgr = CronManager(db)
    result = await mgr.get_execution(execution_id)
    if result is None:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return result
