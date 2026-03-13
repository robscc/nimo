"""SubAgent Registry — SubAgent 角色定义的 CRUD 和生命周期管理。"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.models.agent import SubAgentDefinition


# 默认 SubAgent 角色（首次启动时创建）
DEFAULT_SUB_AGENTS: list[dict[str, Any]] = [
    {
        "name": "researcher",
        "display_name": "调研员",
        "role_prompt": (
            "你是一个专注的调研员。你的职责是：\n"
            "- 深度调研和资料整理\n"
            "- 信息搜集和汇总\n"
            "- 对比分析和报告撰写\n\n"
            "工作原则：直接给出结果，用结构化格式整理信息，注明来源。"
        ),
        "accepted_task_types": ["research", "summarize", "analyze", "report"],
        "max_tool_rounds": 8,
        "timeout_seconds": 300,
    },
    {
        "name": "coder",
        "display_name": "编码员",
        "role_prompt": (
            "你是一个专注的编码员。你的职责是：\n"
            "- 编写和调试代码\n"
            "- 执行脚本和命令\n"
            "- 技术实现和验证\n\n"
            "工作原则：优先用工具验证结果，不要只给理论答案。代码需经过测试。"
        ),
        "accepted_task_types": ["code", "debug", "script", "implement", "test"],
        "max_tool_rounds": 8,
        "timeout_seconds": 120,
    },
]


class SubAgentRegistry:
    """SubAgent 角色注册和生命周期管理。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def ensure_defaults(self) -> None:
        """确保默认 SubAgent 角色存在（幂等）。"""
        for defn in DEFAULT_SUB_AGENTS:
            existing = await self._db.get(SubAgentDefinition, defn["name"])
            if existing is None:
                record = SubAgentDefinition(**defn)
                self._db.add(record)
                logger.info(f"创建默认 SubAgent 定义: {defn['name']}")
        await self._db.flush()

    async def list_agents(self) -> list[dict[str, Any]]:
        """列出所有 SubAgent 定义。"""
        result = await self._db.execute(
            select(SubAgentDefinition).order_by(SubAgentDefinition.created_at)
        )
        records = result.scalars().all()
        return [self._to_dict(r) for r in records]

    async def get_agent(self, name: str) -> dict[str, Any] | None:
        """获取单个 SubAgent 定义。"""
        record = await self._db.get(SubAgentDefinition, name)
        return self._to_dict(record) if record else None

    async def create_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建新的 SubAgent 定义。"""
        name = data.get("name", "")
        if not name:
            raise ValueError("SubAgent 名称不能为空")

        existing = await self._db.get(SubAgentDefinition, name)
        if existing:
            raise ValueError(f"SubAgent '{name}' 已存在")

        record = SubAgentDefinition(
            name=name,
            display_name=data.get("display_name", name),
            role_prompt=data.get("role_prompt", ""),
            accepted_task_types=data.get("accepted_task_types", []),
            model_name=data.get("model_name"),
            model_provider=data.get("model_provider"),
            model_api_key=data.get("model_api_key"),
            model_base_url=data.get("model_base_url"),
            max_tool_rounds=data.get("max_tool_rounds", 8),
            timeout_seconds=data.get("timeout_seconds", 300),
            enabled=data.get("enabled", True),
        )
        self._db.add(record)
        await self._db.flush()
        logger.info(f"创建 SubAgent: {name}")
        return self._to_dict(record)

    async def update_agent(self, name: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """更新 SubAgent 定义。"""
        record = await self._db.get(SubAgentDefinition, name)
        if record is None:
            return None

        updatable = [
            "display_name", "role_prompt", "accepted_task_types",
            "model_name", "model_provider", "model_api_key", "model_base_url",
            "max_tool_rounds", "timeout_seconds", "enabled",
        ]
        for key in updatable:
            if key in data:
                setattr(record, key, data[key])

        await self._db.flush()
        logger.info(f"更新 SubAgent: {name}")
        return self._to_dict(record)

    async def delete_agent(self, name: str) -> bool:
        """删除 SubAgent 定义。"""
        record = await self._db.get(SubAgentDefinition, name)
        if record is None:
            return False
        await self._db.delete(record)
        await self._db.flush()
        logger.info(f"删除 SubAgent: {name}")
        return True

    async def find_agent_for_task(
        self, task_type: str
    ) -> SubAgentDefinition | None:
        """根据任务类型找到合适的 SubAgent。

        遍历所有已启用的 SubAgent，检查其 accepted_task_types 是否包含
        给定的 task_type。返回第一个匹配的。
        """
        result = await self._db.execute(
            select(SubAgentDefinition).where(SubAgentDefinition.enabled == True)  # noqa: E712
        )
        records = result.scalars().all()

        for record in records:
            if task_type in (record.accepted_task_types or []):
                return record
        return None

    async def get_enabled_agents(self) -> list[SubAgentDefinition]:
        """获取所有已启用的 SubAgent 定义。"""
        result = await self._db.execute(
            select(SubAgentDefinition).where(SubAgentDefinition.enabled == True)  # noqa: E712
        )
        return list(result.scalars().all())

    @staticmethod
    def _to_dict(record: SubAgentDefinition) -> dict[str, Any]:
        return {
            "name": record.name,
            "display_name": record.display_name,
            "role_prompt": record.role_prompt,
            "accepted_task_types": record.accepted_task_types or [],
            "model_name": record.model_name,
            "model_provider": record.model_provider,
            "model_base_url": record.model_base_url,
            "has_custom_model": bool(record.model_name),
            "max_tool_rounds": record.max_tool_rounds,
            "timeout_seconds": record.timeout_seconds,
            "enabled": record.enabled,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }
