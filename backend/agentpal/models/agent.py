"""SubAgent 定义模型 — 角色、模型配置、任务类型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from agentpal.database import Base


class SubAgentDefinition(Base):
    """SubAgent 角色定义。

    每个 SubAgent 有独立的角色定位、模型配置和可接受的任务类型。
    主 Agent 根据任务类型匹配合适的 SubAgent。

    Attributes:
        name:                唯一标识（如 "researcher", "coder"）
        display_name:        显示名称
        role_prompt:         角色系统提示词
        accepted_task_types: 可接受的任务类型列表 JSON（如 ["research", "summarize"]）
        model_name:          独立模型名（null 则继承主 Agent 的模型）
        model_provider:      模型提供商
        model_api_key:       模型 API Key
        model_base_url:      模型 Base URL
        max_tool_rounds:     最大工具调用轮次
        timeout_seconds:     任务超时秒数
        enabled:             是否启用
    """

    __tablename__ = "sub_agent_definitions"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    role_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    accepted_task_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_api_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    model_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    max_tool_rounds: Mapped[int] = mapped_column(nullable=False, default=8)
    timeout_seconds: Mapped[int] = mapped_column(nullable=False, default=300)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def get_model_config(self, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        """获取此 SubAgent 的模型配置，缺失字段从 fallback 继承。"""
        fb = fallback or {}
        return {
            "provider": self.model_provider or fb.get("provider", "compatible"),
            "model_name": self.model_name or fb.get("model_name", ""),
            "api_key": self.model_api_key or fb.get("api_key", ""),
            "base_url": self.model_base_url or fb.get("base_url", ""),
        }
