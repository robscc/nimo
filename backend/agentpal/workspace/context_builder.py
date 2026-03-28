"""ContextBuilder — 每轮对话前动态构建 system prompt。

从 WorkspaceFiles 中读取各模块内容，按优先级截断后组装成结构化的 system prompt。
支持注入 prompt 型技能（SKILL.md）的内容。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentpal.paths import get_workspace_dir


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
    bootstrap: str = ""
    heartbeat: str = ""


class ContextBuilder:
    """将 WorkspaceFiles 组装为 system prompt 字符串。

    各部分字符限制（防止 context 过长）：
    - MEMORY:    最多 3000 字符（超出从末尾截断，保留最新记忆）
    - TODAY_LOG: 最多 1500 字符（保留最近日志）
    - CONTEXT:   最多 2000 字符
    - AGENTS:    最多 4000 字符
    - SKILL_PROMPT: 每个技能最多 4000 字符
    """

    MAX_MEMORY_CHARS = 3000
    MAX_DAILY_LOG_CHARS = 1500
    MAX_CONTEXT_CHARS = 2000
    MAX_AGENTS_CHARS = 4000
    MAX_SKILL_PROMPT_CHARS = 4000

    def build_system_prompt(
        self,
        ws: WorkspaceFiles,
        enabled_tools: list[str] | None = None,
        skill_prompts: list[dict[str, Any]] | None = None,
        runtime_context: dict[str, Any] | None = None,
        sub_agent_roster: str | None = None,
    ) -> str:
        """构建完整 system prompt。

        Args:
            ws:              工作空间文件内容
            enabled_tools:   当前已启用工具名称列表（可选，用于提示）
            skill_prompts:   prompt 型技能列表（可选），每项包含 name, content
            runtime_context: 运行时环境信息（session_id、OS、时区等）
            sub_agent_roster: 动态 SubAgent roster（可选，由 SubAgentRegistry 生成）

        Returns:
            拼接好的 system prompt 字符串
        """
        now = datetime.now(timezone.utc).astimezone()
        sections: list[str] = []

        # 0. Bootstrap（首次引导 — 优先级最高，存在时直接注入）
        is_bootstrapping = bool(ws.bootstrap.strip()) and ws.bootstrap.strip() not in ("(暂无)", "(空)")
        if is_bootstrapping:
            sections.append(
                "# 🚀 Bootstrap — 首次引导（优先执行）\n\n"
                f"{ws.bootstrap.strip()}"
            )

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

        # 4.5 动态 SubAgent roster
        if sub_agent_roster and sub_agent_roster.strip():
            sections.append(f"# SubAgent Roster\n\n{sub_agent_roster.strip()}")

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

        # 7.5 Heartbeat 任务清单（如果有非注释内容，注入提示）
        if ws.heartbeat.strip():
            # 过滤掉纯注释行，看是否有实际任务
            active_lines = [
                line for line in ws.heartbeat.strip().splitlines()
                if line.strip() and not line.strip().startswith("#") and not line.strip().startswith(">")
            ]
            if active_lines:
                sections.append(
                    "# Heartbeat — 定期任务\n\n"
                    "以下是用户配置的定期检查任务，heartbeat 机制会自动执行：\n\n"
                    + "\n".join(active_lines)
                )

        # 8. 运行时环境（时间、时区、OS、session_id 等）
        ws_dir = str(get_workspace_dir())
        ws_display = f"`{ws_dir}/`" if not ws_dir.startswith("~") else f"`{ws_dir}/`"
        tz_name = now.strftime("%Z")
        tz_offset = now.strftime("%z")  # e.g. +0800
        env_lines = [
            f"- Current time: {now.strftime('%Y-%m-%d %H:%M:%S')} {tz_name} (UTC{tz_offset[:3]}:{tz_offset[3:]})",
            f"- Day of week: {now.strftime('%A')}",
            f"- Timezone: {tz_name} (UTC{tz_offset[:3]}:{tz_offset[3:]})",
            f"- Workspace directory: {ws_display} (所有记忆文件必须存放在此目录下，如 `{ws_dir}/USER.md`、`{ws_dir}/MEMORY.md` 等)",
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

        # 9.5 工具安全等级说明
        try:
            from agentpal.tools.tool_guard import ToolGuardManager

            guard = ToolGuardManager.get_instance()
            if guard.enabled:
                threshold = guard.default_threshold
                # 尝试从 runtime_context 获取 session 级阈值
                if runtime_context and runtime_context.get("tool_guard_threshold") is not None:
                    threshold = runtime_context["tool_guard_threshold"]
                sections.append(
                    "# Tool Security Levels\n\n"
                    "Your tools have security levels (0 = most dangerous, 4 = safest).\n"
                    f"Current session security threshold: {threshold}\n\n"
                    f"- Tools with level < {threshold} will require user confirmation before execution\n"
                    f"- Tools with level >= {threshold} execute immediately without confirmation\n\n"
                    "When a tool call is blocked for security review, the user will decide whether to proceed.\n"
                    "If cancelled, acknowledge the cancellation and suggest safer alternatives.\n\n"
                    "Exercise caution with low-level tools. Prefer safer approaches when possible."
                )
        except Exception:
            pass

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

