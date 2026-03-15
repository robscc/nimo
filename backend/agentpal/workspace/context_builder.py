"""ContextBuilder — 每轮对话前动态构建 system prompt。

从 WorkspaceFiles 中读取各模块内容，按优先级截断后组装成结构化的 system prompt。
支持注入 prompt 型技能（SKILL.md）的内容。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class WorkspaceFiles:
    """工作空间文件内容快照。"""
    agents: str = ""
    identity: str = ""
    soul: str = ""
    user: str = ""
    memory: str = ""
    context: str = ""
    today_log: str = ""


class ContextBuilder:
    """将 WorkspaceFiles 组装为 system prompt 字符串。

    各部分字符限制（防止 context 过长）：
    - MEMORY:    最多 3000 字符（超出从末尾截断，保留最新记忆）
    - TODAY_LOG: 最多 1500 字符（保留最近日志）
    - CONTEXT:   最多 2000 字符
    - AGENTS:    最多 2000 字符
    - SKILL_PROMPT: 每个技能最多 4000 字符
    """

    MAX_MEMORY_CHARS = 3000
    MAX_DAILY_LOG_CHARS = 1500
    MAX_CONTEXT_CHARS = 2000
    MAX_AGENTS_CHARS = 2000
    MAX_SKILL_PROMPT_CHARS = 4000

    def build_system_prompt(
        self,
        ws: WorkspaceFiles,
        enabled_tools: list[str] | None = None,
        skill_prompts: list[dict[str, Any]] | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> str:
        """构建完整 system prompt。

        Args:
            ws:              工作空间文件内容
            enabled_tools:   当前已启用工具名称列表（可选，用于提示）
            skill_prompts:   prompt 型技能列表（可选），每项包含 name, content
            runtime_context: 运行时环境信息（session_id、OS、时区等）

        Returns:
            拼接好的 system prompt 字符串
        """
        now = datetime.now(timezone.utc).astimezone()
        sections: list[str] = []

        # 1. Agent 身份
        if ws.identity.strip():
            sections.append(f"# Agent Identity\n\n{ws.identity.strip()}")

        # 2. 性格与价值观
        if ws.soul.strip():
            sections.append(f"# Soul & Personality\n\n{ws.soul.strip()}")

        # 3. 用户画像
        if ws.user.strip():
            sections.append(f"# User Profile\n\n{ws.user.strip()}")

        # 4. Agent 路由配置
        if ws.agents.strip():
            agents_text = ws.agents.strip()
            if len(agents_text) > self.MAX_AGENTS_CHARS:
                agents_text = agents_text[: self.MAX_AGENTS_CHARS] + "\n\n[...已截断...]"
            sections.append(f"# Agent Configuration\n\n{agents_text}")

        # 5. 长期记忆（保留最新部分）
        if ws.memory.strip():
            mem = ws.memory.strip()
            if len(mem) > self.MAX_MEMORY_CHARS:
                mem = "...(较早的记忆已省略)...\n\n" + mem[-self.MAX_MEMORY_CHARS :]
            sections.append(f"# Long-term Memory\n\n{mem}")

        # 6. 当前上下文补充（可选）
        if ws.context.strip() and ws.context.strip() not in ("(暂无)", "(空)"):
            ctx = ws.context.strip()
            if len(ctx) > self.MAX_CONTEXT_CHARS:
                ctx = ctx[: self.MAX_CONTEXT_CHARS] + "\n\n[...已截断...]"
            sections.append(f"# Current Context\n\n{ctx}")

        # 7. 今日日志（保留最近部分）
        if ws.today_log.strip():
            log = ws.today_log.strip()
            if len(log) > self.MAX_DAILY_LOG_CHARS:
                log = "...(较早的日志已省略)...\n\n" + log[-self.MAX_DAILY_LOG_CHARS :]
            sections.append(f"# Today's Activity Log\n\n{log}")

        # 8. 运行时环境（时间、时区、OS、session_id 等）
        tz_name = now.strftime("%Z")
        tz_offset = now.strftime("%z")  # e.g. +0800
        env_lines = [
            f"- Current time: {now.strftime('%Y-%m-%d %H:%M:%S')} {tz_name} (UTC{tz_offset[:3]}:{tz_offset[3:]})",
            f"- Day of week: {now.strftime('%A')}",
            f"- Timezone: {tz_name} (UTC{tz_offset[:3]}:{tz_offset[3:]})",
        ]
        if runtime_context:
            if runtime_context.get("session_id"):
                env_lines.append(f"- Current session_id: `{runtime_context['session_id']}`")
                env_lines.append(
                    "  *(When creating a cron job that should notify the current conversation, "
                    "use this session_id as target_session_id)*"
                )
            if runtime_context.get("os"):
                env_lines.append(f"- Operating system: {runtime_context['os']}")
            if runtime_context.get("python_version"):
                env_lines.append(f"- Python version: {runtime_context['python_version']}")
            # 追加任意额外字段
            for key, val in runtime_context.items():
                if key not in ("session_id", "os", "python_version") and val:
                    env_lines.append(f"- {key}: {val}")
        sections.append("# Runtime Environment\n\n" + "\n".join(env_lines))

        # 9. 可用工具（简要提示）
        if enabled_tools:
            tools_line = ", ".join(f"`{t}`" for t in enabled_tools)
            sections.append(f"# Available Tools\n\n{tools_line}")

        # 10. 已安装的 prompt 型技能
        if skill_prompts:
            skill_parts: list[str] = []
            for sp in skill_prompts:
                name = sp.get("name", "unknown")
                content = sp.get("content", "")
                if len(content) > self.MAX_SKILL_PROMPT_CHARS:
                    content = content[: self.MAX_SKILL_PROMPT_CHARS] + "\n\n[...已截断...]"
                skill_parts.append(f"## Skill: {name}\n\n{content}")
            sections.append(
                "# Installed Skills\n\n" + "\n\n---\n\n".join(skill_parts)
            )

        return "\n\n---\n\n".join(sections)

