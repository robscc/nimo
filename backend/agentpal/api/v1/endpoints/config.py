"""Config API — 读写 ~/.nimo/config.yaml 服务配置。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentpal.config import get_settings
from agentpal.services.config_file import ConfigFileManager

router = APIRouter()


class ConfigResponse(BaseModel):
    config: dict[str, Any]
    path: str


class ConfigUpdateRequest(BaseModel):
    config: dict[str, Any]


@router.get("", response_model=ConfigResponse)
async def get_config():
    """获取当前服务配置（从 ~/.nimo/config.yaml 读取）。"""
    settings = get_settings()
    mgr = ConfigFileManager(settings.workspace_dir)
    config = mgr.load()
    return ConfigResponse(config=config, path=str(mgr.config_path))


@router.put("", response_model=ConfigResponse)
async def update_config(req: ConfigUpdateRequest):
    """更新服务配置（合并写入 ~/.nimo/config.yaml）。"""
    settings = get_settings()
    mgr = ConfigFileManager(settings.workspace_dir)
    try:
        merged = mgr.update(req.config)
        return ConfigResponse(config=merged, path=str(mgr.config_path))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"配置更新失败: {e}")


@router.post("/init", response_model=ConfigResponse)
async def init_config():
    """初始化默认配置文件（幂等，已存在则跳过）。"""
    settings = get_settings()
    mgr = ConfigFileManager(settings.workspace_dir)
    created = mgr.save_defaults()
    config = mgr.load()
    return ConfigResponse(config=config, path=str(mgr.config_path))
