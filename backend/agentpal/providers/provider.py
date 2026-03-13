"""Provider 抽象基类与数据模型。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from agentscope.model import ChatModelBase


class ModelInfo(BaseModel):
    id: str = Field(..., description="模型 ID（用于 API 调用）")
    name: str = Field(..., description="模型展示名称")


class ProviderConfig(BaseModel):
    """Provider 配置（可序列化为 JSON 持久化）。"""

    id: str
    name: str
    base_url: str = ""
    api_key: str = ""
    models: List[ModelInfo] = Field(default_factory=list)
    extra_models: List[ModelInfo] = Field(default_factory=list)
    is_custom: bool = False
    require_api_key: bool = True
    freeze_url: bool = False
    support_model_discovery: bool = False
    generate_kwargs: Dict[str, Any] = Field(default_factory=dict)

    def all_models(self) -> List[ModelInfo]:
        return self.models + self.extra_models

    def has_model(self, model_id: str) -> bool:
        return any(m.id == model_id for m in self.all_models())

    def public_info(self) -> "ProviderConfig":
        """返回脱敏后的副本（api_key 打码）。"""
        data = self.model_dump()
        if data["api_key"]:
            visible = data["api_key"][:4]
            data["api_key"] = visible + "******"
        return ProviderConfig.model_validate(data)


class Provider(ProviderConfig, ABC):
    """Provider 实例（含连接测试 + 模型实例化逻辑）。"""

    @abstractmethod
    async def check_connection(self, timeout: float = 5) -> tuple[bool, str]:
        """测试 Provider 连通性，返回 (ok, error_msg)。"""

    @abstractmethod
    async def fetch_models(self, timeout: float = 5) -> List[ModelInfo]:
        """从 Provider API 拉取可用模型列表。"""

    @abstractmethod
    def get_chat_model_instance(
        self,
        model_id: str,
        stream: bool = False,
        api_key_override: str | None = None,
        base_url_override: str | None = None,
    ) -> ChatModelBase:
        """实例化 agentscope ChatModel。"""

    def update_config(self, data: Dict[str, Any]) -> None:
        """更新配置字段（api_key / base_url / name / generate_kwargs）。"""
        if not self.freeze_url and data.get("base_url") is not None:
            self.base_url = str(data["base_url"])
        if data.get("api_key") is not None:
            self.api_key = str(data["api_key"])
        if data.get("name") is not None:
            self.name = str(data["name"])
        if isinstance(data.get("generate_kwargs"), dict):
            self.generate_kwargs = data["generate_kwargs"]
