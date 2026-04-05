"""ContextBuilder — 每轮对话前动态构建 system prompt。

支持：
- 基础 section 收集与拼接
- 渐进式揭露模式（full/summary/reminder/skip）
- 异步任务结果注入（SubAgent/Cron）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agentpal.paths import get_workspace_dir
from agentpal.workspace.prompt_sections import (
    DisclosureMode,
    PromptSection,
    RenderedSection,
    normalize_mode,
    pick_section_text,
)


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
    """将 WorkspaceFiles 组装为 system prompt。"""

    MAX_MEMORY_CHARS = 3000
    MAX_DAILY_LOG_CHARS = 1500
    MAX_CONTEXT_CHARS = 2000
    MAX_AGENTS_CHARS = 4000
    MAX_SKILL_PROMPT_CHARS = 4000

    # async task 注入默认限制
    MAX_ASYNC_RESULT_CHARS = 500
    MAX_ASYNC_RESULT_INJECT = 5

    def build_system_prompt(
        self,
        ws: WorkspaceFiles,
        enabled_tools: list[str] | None = None,
        skill_prompts: list[dict[str, Any]] | None = None,
        runtime_context: dict[str, Any] | None = None,
        sub_agent_roster: str | None = None,
        async_task_results: list[dict[str, Any]] | None = None,
        disclosure_modes: dict[str, str] | None = None,
        section_reasons: dict[str, str] | None = None,
    ) -> str:
        """构建完整 system prompt。

        disclosure_modes:
            可选 section -> mode 映射（full/summary/reminder/skip）。
            不提供时默认全部 full（与旧行为兼容）。
        """
        sections = self.collect_sections(
            ws,
            enabled_tools=enabled_tools,
            skill_prompts=skill_prompts,
            runtime_context=runtime_context,
            sub_agent_roster=sub_agent_roster,
            async_task_results=async_task_results,
        )
        prompt, _ = self.render_sections(
            sections,
            disclosure_modes=disclosure_modes,
            section_reasons=section_reasons,
        )
        return prompt

    def collect_sections(
        self,
        ws: WorkspaceFiles,
        *,
        enabled_tools: list[str] | None = None,
        skill_prompts: list[dict[str, Any]] | None = None,
        runtime_context: dict[str, Any] | None = None,
        sub_agent_roster: str | None = None,
        async_task_results: list[dict[str, Any]] | None = None,
    ) -> list[PromptSection]:
        """收集可渲染的 section 列表（不应用披露策略）。"""
        now = datetime.now(timezone.utc).astimezone()
        sections: list[PromptSection] = []

        # 0. Bootstrap（首次引导 — 优先级最高）
        is_bootstrapping = bool(ws.bootstrap.strip()) and ws.bootstrap.strip() not in ("(暂无)", "(空)")
        if is_bootstrapping:
            text = "# 🚀 Bootstrap — 首次引导（优先执行）\n\n" + ws.bootstrap.strip()
            sections.append(
                PromptSection(
                    section_id="bootstrap",
                    title="Bootstrap",
                    content=text,
                    priority=0,
                    always_on=True,
                    summary="# 🚀 Bootstrap\n\n处于首次引导阶段。",
                    reminder="# 🚀 Bootstrap\n\n首次引导信息已加载。",
                )
            )

        # 1. Agent 身份
        if ws.identity.strip():
            text = f"# Agent Identity\n\n{ws.identity.strip()}"
            sections.append(
                PromptSection(
                    section_id="identity",
                    title="Agent Identity",
                    content=text,
                    priority=10,
                    always_on=True,
                )
            )

        # 2. 性格与价值观
        if ws.soul.strip():
            text = f"# Soul & Personality\n\n{ws.soul.strip()}"
            sections.append(
                PromptSection(
                    section_id="soul",
                    title="Soul & Personality",
                    content=text,
                    priority=20,
                    always_on=True,
                )
            )

        # 3. 用户画像
        if ws.user.strip():
            text = f"# User Profile\n\n{ws.user.strip()}"
            sections.append(
                PromptSection(
                    section_id="user_profile",
                    title="User Profile",
                    content=text,
                    priority=30,
                    summary="# User Profile\n\n用户画像已加载，可按需引用。",
                    reminder="# User Profile\n\n用户画像信息保持可用。",
                )
            )

        # 4. Agent 路由配置
        if ws.agents.strip():
            agents_text = ws.agents.strip()
            if len(agents_text) > self.MAX_AGENTS_CHARS:
                agents_text = agents_text[: self.MAX_AGENTS_CHARS] + "\n\n[...已截断...]"
            text = f"# Agent Configuration\n\n{agents_text}"
            sections.append(
                PromptSection(
                    section_id="agent_config",
                    title="Agent Configuration",
                    content=text,
                    priority=40,
                    summary="# Agent Configuration\n\nAgent 路由规则已加载。",
                    reminder="# Agent Configuration\n\n路由规则可按需展开。",
                )
            )

        # 4.5 动态 SubAgent roster
        if sub_agent_roster and sub_agent_roster.strip():
            text = f"# SubAgent Roster\n\n{sub_agent_roster.strip()}"
            sections.append(
                PromptSection(
                    section_id="subagent_roster",
                    title="SubAgent Roster",
                    content=text,
                    priority=45,
                    summary="# SubAgent Roster\n\nSubAgent 花名册已加载。",
                    reminder="# SubAgent Roster\n\n可按需查看可用 SubAgent。",
                )
            )

        # 5. 长期记忆（保留最新部分）
        if ws.memory.strip():
            mem = ws.memory.strip()
            if len(mem) > self.MAX_MEMORY_CHARS:
                mem = "...(较早的记忆已省略)...\n\n" + mem[-self.MAX_MEMORY_CHARS :]
            text = f"# Long-term Memory\n\n{mem}"
            sections.append(
                PromptSection(
                    section_id="memory",
                    title="Long-term Memory",
                    content=text,
                    priority=50,
                    summary="# Long-term Memory\n\n长期记忆已加载（摘要模式）。",
                    reminder="# Long-term Memory\n\n长期记忆可按需展开。",
                )
            )

        # 6. 当前上下文补充
        if ws.context.strip() and ws.context.strip() not in ("(暂无)", "(空)"):
            ctx = ws.context.strip()
            if len(ctx) > self.MAX_CONTEXT_CHARS:
                ctx = ctx[: self.MAX_CONTEXT_CHARS] + "\n\n[...已截断...]"
            text = f"# Current Context\n\n{ctx}"
            sections.append(
                PromptSection(
                    section_id="current_context",
                    title="Current Context",
                    content=text,
                    priority=60,
                    summary="# Current Context\n\n当前上下文补充信息已加载。",
                    reminder="# Current Context\n\n当前上下文可按需展开。",
                )
            )

        # 7. 今日日志
        if ws.today_log.strip():
            log = ws.today_log.strip()
            if len(log) > self.MAX_DAILY_LOG_CHARS:
                log = "...(较早的日志已省略)...\n\n" + log[-self.MAX_DAILY_LOG_CHARS :]
            text = f"# Today's Activity Log\n\n{log}"
            sections.append(
                PromptSection(
                    section_id="today_log",
                    title="Today's Activity Log",
                    content=text,
                    priority=70,
                    summary="# Today's Activity Log\n\n今日活动日志已加载（摘要）。",
                    reminder="# Today's Activity Log\n\n今日日志可按需展开。",
                )
            )

        # 7.5 Heartbeat
        if ws.heartbeat.strip():
            active_lines = [
                line
                for line in ws.heartbeat.strip().splitlines()
                if line.strip() and not line.strip().startswith("#") and not line.strip().startswith(">")
            ]
            if active_lines:
                text = (
                    "# Heartbeat — 定期任务\n\n"
                    "以下是用户配置的定期检查任务，heartbeat 机制会自动执行：\n\n"
                    + "\n".join(active_lines)
                )
                sections.append(
                    PromptSection(
                        section_id="heartbeat",
                        title="Heartbeat",
                        content=text,
                        priority=75,
                        summary="# Heartbeat\n\n存在定期任务配置。",
                        reminder="# Heartbeat\n\n定期任务配置保持可用。",
                    )
                )

        # 8. 运行时环境
        ws_dir = str(get_workspace_dir())
        ws_display = f"`{ws_dir}/`" if not ws_dir.startswith("~") else f"`{ws_dir}/`"
        tz_name = now.strftime("%Z")
        tz_offset = now.strftime("%z")
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
            for key, val in runtime_context.items():
                if key not in ("session_id", "os", "python_version") and val is not None:
                    env_lines.append(f"- {key}: {val}")

        sections.append(
            PromptSection(
                section_id="runtime_env",
                title="Runtime Environment",
                content="# Runtime Environment\n\n" + "\n".join(env_lines),
                priority=80,
                always_on=True,
            )
        )

        # 9. 可用工具
        if enabled_tools:
            tools_line = ", ".join(f"`{t}`" for t in enabled_tools)
            sections.append(
                PromptSection(
                    section_id="available_tools",
                    title="Available Tools",
                    content=f"# Available Tools\n\n{tools_line}",
                    priority=90,
                    always_on=True,
                )
            )

        # 9.5 工具安全等级
        try:
            from agentpal.tools.tool_guard import ToolGuardManager

            guard = ToolGuardManager.get_instance()
            if guard.enabled:
                threshold = guard.default_threshold
                if runtime_context and runtime_context.get("tool_guard_threshold") is not None:
                    threshold = runtime_context["tool_guard_threshold"]
                text = (
                    "# Tool Security Levels\n\n"
                    "Your tools have security levels (0 = most dangerous, 4 = safest).\n"
                    f"Current session security threshold: {threshold}\n\n"
                    f"- Tools with level < {threshold} will require user confirmation before execution\n"
                    f"- Tools with level >= {threshold} execute immediately without confirmation\n\n"
                    "When a tool call is blocked for security review, the user will decide whether to proceed.\n"
                    "If cancelled, acknowledge the cancellation and suggest safer alternatives.\n\n"
                    "Exercise caution with low-level tools. Prefer safer approaches when possible."
                )
                sections.append(
                    PromptSection(
                        section_id="tool_security",
                        title="Tool Security Levels",
                        content=text,
                        priority=95,
                        always_on=True,
                        summary="# Tool Security Levels\n\n当前工具安全阈值已加载。",
                        reminder="# Tool Security Levels\n\n工具安全策略保持生效。",
                    )
                )
        except Exception:
            pass

        # 9.8 异步任务结果（位于 skills 之前）
        async_section = self._build_async_task_results_section(async_task_results, runtime_context)
        if async_section is not None:
            async_section.priority = 98
            sections.append(async_section)

        # 10. prompt 型技能
        if skill_prompts:
            skill_parts: list[str] = []
            for sp in skill_prompts:
                name = sp.get("name", "unknown")
                content = sp.get("content", "")
                if len(content) > self.MAX_SKILL_PROMPT_CHARS:
                    content = content[: self.MAX_SKILL_PROMPT_CHARS] + "\n\n[...已截断...]"
                skill_parts.append(f"## Skill: {name}\n\n{content}")
            sections.append(
                PromptSection(
                    section_id="installed_skills",
                    title="Installed Skills",
                    content="# Installed Skills\n\n" + "\n\n---\n\n".join(skill_parts),
                    priority=100,
                    summary="# Installed Skills\n\n已安装技能信息已加载（摘要）。",
                    reminder="# Installed Skills\n\n技能信息可按需展开。",
                )
            )

        sections.sort(key=lambda s: s.priority)
        return sections

    def render_sections(
        self,
        sections: list[PromptSection],
        *,
        disclosure_modes: dict[str, str] | None = None,
        section_reasons: dict[str, str] | None = None,
    ) -> tuple[str, list[RenderedSection]]:
        """根据披露模式渲染 sections。"""
        modes = disclosure_modes or {}
        reasons = section_reasons or {}

        rendered_chunks: list[str] = []
        rendered_meta: list[RenderedSection] = []

        for section in sections:
            mode = normalize_mode(modes.get(section.section_id))
            if not modes:
                mode = DisclosureMode.FULL

            if section.always_on and mode == DisclosureMode.SKIP:
                mode = DisclosureMode.FULL

            text = pick_section_text(section, mode)
            injected = bool(text.strip())
            if injected:
                rendered_chunks.append(text.strip())

            rendered_meta.append(
                RenderedSection(
                    section_id=section.section_id,
                    mode=mode,
                    reason=reasons.get(section.section_id, "default"),
                    text=text,
                    injected=injected,
                )
            )

        return "\n\n---\n\n".join(rendered_chunks), rendered_meta

    def _build_async_task_results_section(
        self,
        async_task_results: list[dict[str, Any]] | None,
        runtime_context: dict[str, Any] | None,
    ) -> PromptSection | None:
        """构建 Async Task Results section。"""
        if not async_task_results:
            return None

        max_chars = self.MAX_ASYNC_RESULT_CHARS
        max_inject = self.MAX_ASYNC_RESULT_INJECT
        if runtime_context:
            try:
                if runtime_context.get("async_result_max_chars") is not None:
                    max_chars = int(runtime_context["async_result_max_chars"])
                if runtime_context.get("async_result_max_inject") is not None:
                    max_inject = int(runtime_context["async_result_max_inject"])
            except Exception:
                pass

        def _ts(item: dict[str, Any]) -> str:
            return str(item.get("finished_at") or "")

        sorted_results = sorted(async_task_results, key=_ts, reverse=True)

        lines: list[str] = ["# Async Task Results", ""]
        summary_lines: list[str] = ["# Async Task Results", ""]

        for idx, item in enumerate(sorted_results):
            source = str(item.get("source") or "unknown")
            status = str(item.get("status") or "pending")
            task_id = item.get("task_id")
            exec_id = item.get("execution_id")
            agent_name = str(item.get("agent_name") or "-")
            task_prompt = str(item.get("task_prompt") or "")
            result = str(item.get("result") or "")
            error = str(item.get("error") or "")

            icon = "✅" if status == "done" else "❌" if status == "failed" else "⏳"
            identity = task_id or exec_id or f"item-{idx}"

            if idx < max_inject:
                text = result if result else error
                truncated = False
                if len(text) > max_chars:
                    text = text[:max_chars] + "...[已截断]"
                    truncated = True

                lines.extend(
                    [
                        f"## {icon} [{source}] {agent_name}",
                        f"- ID: {identity}",
                        f"- Task: {task_prompt}",
                        f"- Status: {status}",
                        f"- Result: {text or '(无)'}",
                        "",
                    ]
                )

                short = text[:120] + ("..." if len(text) > 120 else "")
                summary_lines.append(f"- {icon} {source} {identity}: {short or status}")
                if truncated:
                    summary_lines.append("  (结果较长，已截断)")
            else:
                lines.extend(
                    [
                        f"## ⏳ [{source}] {agent_name}",
                        f"- ID: {identity}",
                        "- 状态: 已过期（仅保留摘要）",
                        "",
                    ]
                )
                summary_lines.append(f"- ⏳ {source} {identity}: 已过期")

        content = "\n".join(lines).strip()
        summary = "\n".join(summary_lines).strip()
        reminder = "# Async Task Results\n\n有新的异步任务结果可按需展开查看。"

        return PromptSection(
            section_id="async_task_results",
            title="Async Task Results",
            content=content,
            priority=98,
            summary=summary,
            reminder=reminder,
        )
