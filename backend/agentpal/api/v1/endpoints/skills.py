"""Skills 管理 API — 安装、卸载、启用、禁用、列表。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.database import get_db
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


# ── 路由 ─────────────────────────────────────────────────

@router.get("", response_model=list[SkillInfo])
async def list_skills(db: AsyncSession = Depends(get_db)):
    """列出所有已安装的技能。"""
    mgr = SkillManager(db)
    skills = await mgr.list_skills()
    return [SkillInfo(**s) for s in skills]


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
    return {"name": name, "enabled": req.enabled}


@router.delete("/{name}")
async def uninstall_skill(name: str, db: AsyncSession = Depends(get_db)):
    """卸载指定技能。"""
    mgr = SkillManager(db)
    ok = await mgr.uninstall(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"技能 {name!r} 不存在")
    return {"message": f"技能 {name!r} 已卸载"}
