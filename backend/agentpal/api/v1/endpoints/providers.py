"""Providers API — LLM 供应商管理接口。

路由：
    GET    /api/v1/providers                  列出所有 Provider（api_key 打码）
    PATCH  /api/v1/providers/{id}             更新 Provider 配置（api_key / base_url）
    POST   /api/v1/providers/{id}/test        测试 Provider 连通性
    GET    /api/v1/providers/{id}/models      查看 Provider 模型列表
    POST   /api/v1/providers/{id}/models/fetch  从 API 动态拉取模型列表
    POST   /api/v1/providers                  添加自定义 Provider
    DELETE /api/v1/providers/{id}             删除自定义 Provider
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentpal.providers.manager import ProviderManager
from agentpal.providers.provider import ModelInfo, ProviderConfig

router = APIRouter()


# ── Request / Response 模型 ──────────────────────────────────────────────

class ProviderUpdateRequest(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    name: Optional[str] = None
    generate_kwargs: Optional[Dict[str, Any]] = None


class ProviderCreateRequest(BaseModel):
    id: str
    name: str
    base_url: str = ""
    api_key: str = ""
    generate_kwargs: Dict[str, Any] = {}


class ConnectionTestResponse(BaseModel):
    ok: bool
    message: str


class ModelsResponse(BaseModel):
    models: List[ModelInfo]
    extra_models: List[ModelInfo]


# ── 路由 ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[ProviderConfig])
async def list_providers():
    """列出所有 Provider（api_key 后 6 位打码）。"""
    mgr = ProviderManager.get_instance()
    return mgr.list_providers(masked=True)


@router.patch("/{provider_id}", response_model=ProviderConfig)
async def update_provider(provider_id: str, req: ProviderUpdateRequest):
    """更新 Provider 配置（api_key / base_url / generate_kwargs）。"""
    mgr = ProviderManager.get_instance()
    data = req.model_dump(exclude_none=True)
    ok = mgr.update_provider(provider_id, data)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' 不存在")
    provider = mgr.get_provider(provider_id)
    return provider.public_info()  # type: ignore[union-attr]


@router.post("/{provider_id}/test", response_model=ConnectionTestResponse)
async def test_provider(provider_id: str):
    """测试 Provider 连通性。"""
    mgr = ProviderManager.get_instance()
    ok, msg = await mgr.test_connection(provider_id)
    return ConnectionTestResponse(ok=ok, message=msg)


@router.get("/{provider_id}/models", response_model=ModelsResponse)
async def get_provider_models(provider_id: str):
    """返回 Provider 的预置模型 + 用户添加的模型。"""
    mgr = ProviderManager.get_instance()
    provider = mgr.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' 不存在")
    return ModelsResponse(models=provider.models, extra_models=provider.extra_models)


@router.post("/{provider_id}/models/fetch", response_model=List[ModelInfo])
async def fetch_provider_models(provider_id: str):
    """从 Provider API 动态拉取模型列表并更新缓存（仅支持 support_model_discovery=true 的 Provider）。"""
    mgr = ProviderManager.get_instance()
    provider = mgr.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' 不存在")
    if not provider.support_model_discovery:
        raise HTTPException(status_code=400, detail=f"Provider '{provider_id}' 不支持模型发现")
    return await mgr.fetch_provider_models(provider_id)


@router.post("", response_model=ProviderConfig, status_code=201)
async def create_custom_provider(req: ProviderCreateRequest):
    """添加自定义 Provider（OpenAI-compatible 端点）。"""
    mgr = ProviderManager.get_instance()
    config = ProviderConfig(
        id=req.id,
        name=req.name,
        base_url=req.base_url,
        api_key=req.api_key,
        generate_kwargs=req.generate_kwargs,
        is_custom=True,
    )
    return await mgr.add_custom_provider(config)


@router.delete("/{provider_id}", status_code=204)
async def delete_custom_provider(provider_id: str):
    """删除自定义 Provider（内置 Provider 不可删除）。"""
    mgr = ProviderManager.get_instance()
    ok = mgr.remove_custom_provider(provider_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"自定义 Provider '{provider_id}' 不存在（内置 Provider 不可删除）",
        )
