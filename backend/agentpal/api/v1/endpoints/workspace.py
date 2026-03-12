"""Workspace API — 工作空间文件的 CRUD 接口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from agentpal.config import get_settings
from agentpal.workspace.defaults import EDITABLE_FILES
from agentpal.workspace.manager import WorkspaceManager

router = APIRouter()


def _get_manager() -> WorkspaceManager:
    settings = get_settings()
    return WorkspaceManager(Path(settings.workspace_dir))


# ── Schemas ───────────────────────────────────────────────

class FileContent(BaseModel):
    name: str
    content: str


class FileUpdateRequest(BaseModel):
    content: str


class MemoryAppendRequest(BaseModel):
    text: str   # 要追加到 MEMORY.md 的内容（Markdown 格式）


class CanvasWriteRequest(BaseModel):
    content: str


# ── Workspace 文件 ────────────────────────────────────────

@router.get("/files", response_model=list[str])
async def list_files():
    """列出所有可编辑的 workspace 文件名。"""
    return EDITABLE_FILES


@router.get("/files/{name}", response_model=FileContent)
async def get_file(name: str):
    """读取指定 workspace 文件内容。"""
    if name not in EDITABLE_FILES:
        raise HTTPException(
            status_code=404,
            detail=f"文件 {name!r} 不存在，可用：{EDITABLE_FILES}",
        )
    mgr = _get_manager()
    content = await mgr.read_file(name)
    return FileContent(name=name, content=content)


@router.put("/files/{name}", response_model=FileContent)
async def update_file(name: str, body: FileUpdateRequest):
    """覆写指定 workspace 文件内容。"""
    if name not in EDITABLE_FILES:
        raise HTTPException(
            status_code=404,
            detail=f"文件 {name!r} 不存在，可用：{EDITABLE_FILES}",
        )
    mgr = _get_manager()
    await mgr.write_file(name, body.content)
    return FileContent(name=name, content=body.content)


# ── 长期记忆 ──────────────────────────────────────────────

@router.post("/memory", response_model=dict)
async def append_memory(body: MemoryAppendRequest):
    """向 MEMORY.md 末尾追加内容（带时间戳）。"""
    mgr = _get_manager()
    await mgr.append_memory(body.text)
    return {"status": "ok", "appended": body.text}


# ── 每日日志 ──────────────────────────────────────────────

@router.get("/memory/daily", response_model=FileContent)
async def get_daily_log(date: str | None = Query(default=None, description="YYYY-MM-DD，默认今天")):
    """读取指定日期的每日日志。"""
    mgr = _get_manager()
    content = await mgr.get_daily_log(date)
    label = date or "today"
    return FileContent(name=f"memory/{label}.md", content=content)


@router.get("/memory/daily/list", response_model=list[str])
async def list_daily_logs():
    """列出所有有日志的日期（倒序）。"""
    mgr = _get_manager()
    return await mgr.list_daily_logs()


# ── Canvas ────────────────────────────────────────────────

@router.get("/canvas", response_model=list[dict])
async def list_canvas():
    """列出 canvas 目录下的文件。"""
    mgr = _get_manager()
    return await mgr.list_canvas()


@router.get("/canvas/{filename}", response_model=FileContent)
async def get_canvas_file(filename: str):
    """读取 canvas 文件内容。"""
    mgr = _get_manager()
    try:
        content = await mgr.read_canvas(filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return FileContent(name=filename, content=content)


@router.put("/canvas/{filename}", response_model=FileContent)
async def write_canvas_file(filename: str, body: CanvasWriteRequest):
    """写入 canvas 文件。"""
    mgr = _get_manager()
    try:
        await mgr.write_canvas(filename, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return FileContent(name=filename, content=body.content)


# ── 工作空间元信息 ────────────────────────────────────────

@router.get("/info", response_model=dict)
async def workspace_info():
    """返回工作空间根目录路径及各文件是否存在。"""
    settings = get_settings()
    root = Path(settings.workspace_dir)
    files_status = {
        name: (root / name).exists()
        for name in EDITABLE_FILES
    }
    return {
        "workspace_dir": str(root),
        "exists": root.exists(),
        "bootstrapped": (root / ".bootstrapped").exists(),
        "files": files_status,
    }
