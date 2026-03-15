"""Skills 管理 API — 安装、卸载、启用、禁用、热重载、版本管理。"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.database import get_db
from agentpal.services.skill_event_bus import skill_event_bus
from agentpal.skills.manager import SkillManager

router = APIRouter()


# ── 请求/响应模型 ────────────────────────────────────────

class SkillInfo(BaseModel):
    name: str
    version: str
    description: str
    author: str
    source: str
    source_url: str | None = None
    enabled: bool
    tools: list[str]
    skill_type: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class InstallFromUrlRequest(BaseModel):
    url: str


class ToggleRequest(BaseModel):
    enabled: bool


class InstallResult(BaseModel):
    name: str
    version: str
    description: str
    tools: list[str]
    install_path: str
    skill_type: str | None = None


class RollbackRequest(BaseModel):
    index: int


class VersionInfo(BaseModel):
    index: int
    version: str
    backed_up_at: str | None = None


# ── 路由 ─────────────────────────────────────────────────

@router.get("", response_model=list[SkillInfo])
async def list_skills(db: AsyncSession = Depends(get_db)):
    """列出所有已安装的技能。"""
    mgr = SkillManager(db)
    skills = await mgr.list_skills()
    return [SkillInfo(**s) for s in skills]


@router.get("/events")
async def skill_events():
    """SSE 端点 — 订阅技能热重载事件。

    事件格式：
        data: {"type": "skill_reloaded", "name": "...", "version": "...", "action": "install"|"rollback"}
        data: {"type": "ping"}   (每 25 秒心跳，维持连接)
    """
    async def event_stream():
        queue = skill_event_bus.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 心跳包，防止代理/浏览器断开连接
                    yield 'data: {"type":"ping"}\n\n'
        except asyncio.CancelledError:
            pass
        finally:
            skill_event_bus.unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{name}/versions", response_model=list[VersionInfo])
async def get_skill_versions(name: str, db: AsyncSession = Depends(get_db)):
    """获取指定技能的历史版本列表（0=最近备份）。"""
    mgr = SkillManager(db)
    versions = await mgr.list_versions(name)
    return [VersionInfo(**v) for v in versions]


@router.post("/{name}/rollback", response_model=InstallResult)
async def rollback_skill(
    name: str,
    req: RollbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """回滚技能到指定历史版本。

    - `index=0`：恢复到上一个版本
    - `index=1`：恢复到更早版本
    """
    mgr = SkillManager(db)
    try:
        result = await mgr.rollback(name, req.index)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"回滚失败: {exc}")

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"技能 {name!r} 没有索引为 {req.index} 的历史版本",
        )
    await db.commit()
    return InstallResult(**result)


@router.get("/{name}")
async def get_skill(name: str, db: AsyncSession = Depends(get_db)):
    """获取单个技能的详细信息。"""
    mgr = SkillManager(db)
    skill = await mgr.get_skill(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"技能 {name!r} 不存在")
    return skill


@router.post("/install/zip", response_model=InstallResult)
async def install_from_zip(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """通过上传 ZIP 文件安装技能包。"""
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 .zip 文件")

    # 保存到临时文件
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name
        content = await file.read()
        tmp.write(content)

    try:
        mgr = SkillManager(db)
        result = await mgr.install_from_zip(tmp_path, source="upload")
        await db.commit()
        return InstallResult(**result)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"安装失败: {exc}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/install/url", response_model=InstallResult)
async def install_from_url(
    req: InstallFromUrlRequest,
    db: AsyncSession = Depends(get_db),
):
    """通过 URL 下载并安装技能包（支持 clawhub.ai / skills.sh）。"""
    try:
        mgr = SkillManager(db)
        result = await mgr.install_from_url(req.url)
        await db.commit()
        return InstallResult(**result)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"安装失败: {exc}")


@router.patch("/{name}", response_model=dict[str, Any])
async def toggle_skill(
    name: str,
    req: ToggleRequest,
    db: AsyncSession = Depends(get_db),
):
    """启用或禁用指定技能。"""
    mgr = SkillManager(db)
    ok = await mgr.enable(name) if req.enabled else await mgr.disable(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"技能 {name!r} 不存在")
    await db.commit()
    return {"name": name, "enabled": req.enabled}


@router.delete("/{name}")
async def uninstall_skill(name: str, db: AsyncSession = Depends(get_db)):
    """卸载指定技能。"""
    mgr = SkillManager(db)
    ok = await mgr.uninstall(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"技能 {name!r} 不存在")
    await db.commit()
    return {"message": f"技能 {name!r} 已卸载"}
