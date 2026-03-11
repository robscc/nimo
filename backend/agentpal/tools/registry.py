"""ToolRegistry — 管理工具的启用状态，构建 agentscope Toolkit。

设计：
- BUILTIN_TOOLS 定义所有可用工具及其元数据
- ToolConfig 表持久化 enabled 状态（首次启动时按默认值初始化）
- build_toolkit(enabled_names) 根据已启用的工具集构造 agentscope Toolkit
- 每次 chat 请求时从 DB 读取最新状态，动态构建 Toolkit
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from time import time
from typing import Any

from agentscope.tool import Toolkit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.models.tool import ToolCallLog, ToolConfig
from agentpal.tools.builtin import BUILTIN_TOOLS


# ── 工具目录（name → metadata）─────────────────────────────

TOOL_CATALOG: dict[str, dict] = {t["name"]: t for t in BUILTIN_TOOLS}


# ── 数据库操作 ────────────────────────────────────────────


async def ensure_tool_configs(db: AsyncSession) -> None:
    """首次启动时，为所有内置工具创建默认配置行。"""
    for tool in BUILTIN_TOOLS:
        existing = await db.get(ToolConfig, tool["name"])
        if existing is None:
            # 危险工具默认关闭，安全工具默认开启
            default_enabled = not tool.get("dangerous", False)
            db.add(ToolConfig(name=tool["name"], enabled=default_enabled))
    await db.flush()


async def get_enabled_tools(db: AsyncSession) -> list[str]:
    """从数据库读取当前已启用的工具名列表。"""
    result = await db.execute(
        select(ToolConfig).where(ToolConfig.enabled == True)  # noqa: E712
    )
    return [row.name for row in result.scalars().all()]


async def set_tool_enabled(db: AsyncSession, name: str, enabled: bool) -> ToolConfig:
    """启用/禁用指定工具。"""
    if name not in TOOL_CATALOG:
        raise ValueError(f"未知工具: {name!r}")
    config = await db.get(ToolConfig, name)
    if config is None:
        config = ToolConfig(name=name, enabled=enabled)
        db.add(config)
    else:
        config.enabled = enabled
        config.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return config


async def list_tool_configs(db: AsyncSession) -> list[dict]:
    """返回所有工具的完整信息（含启用状态）。"""
    await ensure_tool_configs(db)
    result = await db.execute(select(ToolConfig))
    configs: dict[str, bool] = {row.name: row.enabled for row in result.scalars().all()}

    return [
        {
            "name": t["name"],
            "description": t["description"],
            "icon": t["icon"],
            "dangerous": t.get("dangerous", False),
            "enabled": configs.get(t["name"], not t.get("dangerous", False)),
        }
        for t in BUILTIN_TOOLS
    ]


# ── Toolkit 构建 ──────────────────────────────────────────


def build_toolkit(enabled_names: list[str]) -> Toolkit | None:
    """根据已启用工具集构建 agentscope Toolkit。

    Args:
        enabled_names: 已启用工具名列表

    Returns:
        Toolkit 实例（无启用工具时返回 None）
    """
    if not enabled_names:
        return None

    toolkit = Toolkit()
    for name in enabled_names:
        meta = TOOL_CATALOG.get(name)
        if meta:
            toolkit.register_tool_function(
                meta["func"],
                func_name=name,
                func_description=meta["description"],
            )
    return toolkit


# ── 调用日志记录 ──────────────────────────────────────────


async def log_tool_call(
    db: AsyncSession,
    *,
    session_id: str,
    tool_name: str,
    input_data: dict[str, Any],
    output: str | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """写入一条工具调用日志。"""
    log = ToolCallLog(
        id=str(uuid.uuid4()),
        session_id=session_id,
        tool_name=tool_name,
        input=input_data,
        output=output,
        error=error,
        duration_ms=duration_ms,
    )
    db.add(log)
    await db.flush()


async def get_tool_logs(
    db: AsyncSession,
    tool_name: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """查询工具调用日志。"""
    stmt = select(ToolCallLog).order_by(ToolCallLog.created_at.desc()).limit(limit)
    if tool_name:
        stmt = stmt.where(ToolCallLog.tool_name == tool_name)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "session_id": r.session_id,
            "tool_name": r.tool_name,
            "input": r.input,
            "output": r.output[:300] + "..." if r.output and len(r.output) > 300 else r.output,
            "error": r.error,
            "duration_ms": r.duration_ms,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
