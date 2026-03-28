"""Config API — 读写 ~/.nimo/config.yaml 服务配置。"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agentpal.config import get_settings
from agentpal.services.config_file import ConfigFileManager

router = APIRouter()


class ConfigResponse(BaseModel):
    config: dict[str, Any]
    path: str


class ConfigUpdateRequest(BaseModel):
    config: dict[str, Any]


async def _broadcast_config_reload(request: Request) -> None:
    """广播 CONFIG_RELOAD 到 Scheduler 及其所有子进程。"""
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler and hasattr(scheduler, "broadcast_config_reload"):
        with contextlib.suppress(Exception):
            await scheduler.broadcast_config_reload()


@router.get("", response_model=ConfigResponse)
async def get_config():
    """获取当前服务配置（从 ~/.nimo/config.yaml 读取）。"""
    settings = get_settings()
    mgr = ConfigFileManager(settings.workspace_dir)
    config = mgr.load()
    return ConfigResponse(config=config, path=str(mgr.config_path))


@router.put("", response_model=ConfigResponse)
async def update_config(req: ConfigUpdateRequest, request: Request):
    """更新服务配置（合并写入 ~/.nimo/config.yaml 并自动重载）。"""
    settings = get_settings()
    mgr = ConfigFileManager(settings.workspace_dir)
    try:
        merged = mgr.update(req.config)
        # 清除 lru_cache，使新配置立即生效
        get_settings.cache_clear()
        # 广播到所有子进程
        await _broadcast_config_reload(request)
        return ConfigResponse(config=merged, path=str(mgr.config_path))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"配置更新失败: {e}") from e


@router.post("/init", response_model=ConfigResponse)
async def init_config():
    """初始化默认配置文件（幂等，已存在则跳过）。"""
    settings = get_settings()
    mgr = ConfigFileManager(settings.workspace_dir)
    mgr.save_defaults()
    config = mgr.load()
    return ConfigResponse(config=config, path=str(mgr.config_path))


@router.post("/reload")
async def reload_config(request: Request):
    """热重载配置：清除 Settings 缓存，下次请求使用最新 config.yaml。"""
    get_settings.cache_clear()
    new_settings = get_settings()
    # 广播到所有子进程
    await _broadcast_config_reload(request)
    return {
        "message": "配置已重载（已同步至所有子进程）",
        "llm_model": new_settings.llm_model,
        "llm_provider": new_settings.llm_provider,
    }
