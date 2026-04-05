"""PersonalAssistant — 主助手 Agent，支持工具调用 + SubAgent 派遣 + Plan Mode。"""

from __future__ import annotations

import asyncio
import json
import re as _re
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from time import time
from typing import Any

from agentpal.agents.base import BaseAgent
from agentpal.config import get_settings
from agentpal.memory.base import BaseMemory
from agentpal.memory.factory import MemoryFactory
from agentpal.models.cron import CronJob, CronJobExecution
from agentpal.models.llm_usage import LLMCallLog  # noqa: F401 — 触发 Base.metadata 注册
from agentpal.models.session import AgentMode, SessionRecord, SubAgentTask, TaskArtifact, TaskStatus
from agentpal.models.tool import ToolCallLog
from agentpal.plans.intent import IntentClassifier
from agentpal.plans.store import Plan, PlanStatus, PlanStep, PlanStore
from agentpal.workspace.context_builder import ContextBuilder
from agentpal.workspace.disclosure_engine import (
    DisclosureEngine,
    DisclosureSignals,
    SectionDecision,
)
from agentpal.workspace.manager import WorkspaceManager
from agentpal.workspace.memory_writer import MemoryWriter
from agentpal.workspace.prompt_sections import (
    DisclosureMode,
    PromptSection,
    SectionState,
    dump_section_states,
    hash_text,
    load_section_states,
)

MAX_TOOL_ROUNDS = 32  # 最大工具调用轮次，防止死循环
MAX_PLAN_TOOL_ROUNDS = 8  # 计划生成阶段最大工具调用轮次


class PersonalAssistant(BaseAgent):
    """主助手，与用户直接对话，支持多轮工具调用。

    Args:
        session_id:   会话 ID
        memory:       记忆后端实例
        model_config: 模型配置 dict
        db:           AsyncSession（读工具配置 + 写调用日志，可选）
    """

    def __init__(
        self,
        session_id: str,
        memory: BaseMemory,
        system_prompt: str = "",   # 保留参数兼容旧代码，实际由 workspace 动态构建
        model_config: dict[str, Any] | None = None,
        db: Any = None,
    ) -> None:
        super().__init__(session_id=session_id, memory=memory, system_prompt=system_prompt)
        self._model_config = model_config or _default_model_config()
        self._db = db
        settings = get_settings()
        self._ws_manager = WorkspaceManager(Path(settings.workspace_dir))
        self._context_builder = ContextBuilder()
        self._memory_writer = MemoryWriter()
        self._disclosure_engine = DisclosureEngine(
            enabled=bool(settings.prompt_disclosure_enabled)
            and not bool(settings.prompt_disclosure_force_legacy_builder),
            max_full_sections_per_turn=settings.prompt_disclosure_max_full_sections_per_turn,
            default_ttl_turns=settings.prompt_disclosure_default_ttl_turns,
        )
        self._disclosure_rollout_stage = int(settings.prompt_disclosure_rollout_stage)
        self._disclosure_debug = bool(settings.prompt_disclosure_debug)
        self._cancelled = False

    def cancel(self) -> None:
        """标记取消，reply_stream 工具循环将在下一个检查点退出。"""
        self._cancelled = True

    # ── @mention 确定性拦截 ─────────────────────────────────

    async def _extract_mention_hint(self, user_input: str) -> str | None:
        """检测 @mention 模式，返回 dispatch hint 或 None。

        当用户消息以 @<display_name|name> 开头时，查找匹配的已启用 SubAgent，
        生成强制派遣指令注入 system prompt 末尾，确保 LLM 调用 dispatch_sub_agent。
        """
        import re

        m = re.match(r"^@(\S+)\s*(.*)", user_input, re.DOTALL)
        if not m or self._db is None:
            return None
        mention_name = m.group(1)

        try:
            from agentpal.agents.registry import SubAgentRegistry

            registry = SubAgentRegistry(self._db)
            agents = await registry.get_enabled_agents()
            for agent in agents:
                if mention_name in (agent.display_name, agent.name):
                    return (
                        f"[DISPATCH DIRECTIVE] The user explicitly addressed "
                        f"@{agent.display_name} ({agent.name}). "
                        f"You MUST call `dispatch_sub_agent("
                        f'agent_name="{agent.name}", '
                        f"task_prompt=<the user's request>, "
                        f'parent_session_id="{self.session_id}", '
                        f"blocking=false)` "
                        f"to delegate this task. Do NOT answer the task yourself. "
                        f"Do NOT use blocking=true."
                    )
        except Exception:
            pass
        return None

    # ── System Prompt 动态构建 ────────────────────────────

    async def _build_system_prompt(
        self,
        enabled_tool_names: list[str] | None = None,
        skill_prompts: list[dict] | None = None,
        *,
        user_input: str = "",
        mode: str = AgentMode.NORMAL,
        mention_hint: str | None = None,
        async_task_results: list[dict[str, Any]] | None = None,
        plan_context_text: str | None = None,
    ) -> str:
        """从 workspace 读取文件，动态组装 system prompt（支持渐进式揭露）。"""
        import platform
        import sys

        settings = get_settings()
        ws = await self._ws_manager.load()
        runtime_context = {
            "session_id": self.session_id,
            "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "async_result_max_inject": settings.async_result_max_inject,
            "async_result_max_chars": settings.async_result_max_chars,
        }

        # 注入 tool_guard_threshold 供 ContextBuilder 构建安全等级说明
        try:
            threshold = await self._get_guard_threshold()
            if threshold is not None:
                runtime_context["tool_guard_threshold"] = threshold
        except Exception:
            pass

        # 动态生成 SubAgent roster
        roster_prompt = ""
        if self._db is not None:
            try:
                from agentpal.agents.registry import SubAgentRegistry

                registry = SubAgentRegistry(self._db)
                roster_prompt = await registry.build_roster_prompt()
            except Exception:
                pass

        # Legacy 直出（开关关闭 / 强制回滚）
        if not self._disclosure_engine.enabled:
            prompt = self._context_builder.build_system_prompt(
                ws,
                enabled_tool_names,
                skill_prompts=skill_prompts,
                runtime_context=runtime_context,
                sub_agent_roster=roster_prompt,
                async_task_results=async_task_results,
            )
            if mention_hint:
                prompt += f"\n\n---\n\n{mention_hint}"
            if plan_context_text:
                prompt += f"\n\n---\n\n{plan_context_text}"
            return prompt

        sections = self._context_builder.collect_sections(
            ws,
            enabled_tools=enabled_tool_names,
            skill_prompts=skill_prompts,
            runtime_context=runtime_context,
            sub_agent_roster=roster_prompt,
            async_task_results=async_task_results,
        )

        # 追加 mention / plan context 作为可控 section
        if mention_hint:
            sections.append(
                PromptSection(
                    section_id="mention_hint",
                    title="Mention Directive",
                    content=mention_hint,
                    priority=97,
                    summary="[Mention Directive] 用户显式 @mention 了某个 SubAgent。",
                    reminder="[Mention Directive] 本轮存在 @mention 路由指令。",
                )
            )
        if plan_context_text:
            sections.append(
                PromptSection(
                    section_id="plan_context",
                    title="Plan Context",
                    content=plan_context_text,
                    priority=96,
                    summary="[Plan Context] 当前处于计划执行上下文。",
                    reminder="[Plan Context] 计划上下文保持生效。",
                )
            )

        # 披露状态与轮次
        bundle = await self._load_prompt_disclosure_bundle()
        turn = int(bundle.get("turn", 0) or 0) + 1
        states = load_section_states(bundle.get("sections") if isinstance(bundle, dict) else None)

        signals = await self._build_disclosure_signals(
            user_input=user_input,
            mode=mode,
            mention_hint=mention_hint,
            has_plan_context=bool(plan_context_text),
            has_recent_async_results=bool(async_task_results),
        )

        decisions: dict[str, SectionDecision] = {}
        section_by_id = {s.section_id: s for s in sections}

        for section in sections:
            prev = states.get(section.section_id)
            decisions[section.section_id] = self._disclosure_engine.decide(
                section_id=section.section_id,
                section_text=section.content,
                turn=turn,
                signals=signals,
                prev_state=prev,
            )

        decisions = self._apply_rollout_stage(decisions)
        decisions = self._disclosure_engine.enforce_full_budget(
            decisions,
            critical_sections={"identity", "soul", "runtime_env", "tool_security", "available_tools", "mention_hint", "plan_context"},
        )

        disclosure_modes = {sid: decision.mode.value for sid, decision in decisions.items()}
        section_reasons = {sid: decision.reason for sid, decision in decisions.items()}

        prompt, rendered = self._context_builder.render_sections(
            sections,
            disclosure_modes=disclosure_modes,
            section_reasons=section_reasons,
        )

        # 更新并持久化 section state
        new_states = dict(states)
        for item in rendered:
            section = section_by_id.get(item.section_id)
            if section is None or not item.injected:
                continue
            state = new_states.get(item.section_id, SectionState())
            state.last_mode = item.mode.value
            state.last_turn = turn
            state.reason = item.reason
            ttl = decisions.get(item.section_id).ttl_turns if decisions.get(item.section_id) else 0
            if ttl > 0:
                state.ttl_turns = ttl
            state.last_hash = hash_text(section.content)
            new_states[item.section_id] = state

        new_bundle = {
            "turn": turn,
            "sections": dump_section_states(new_states),
        }
        await self._save_prompt_disclosure_bundle(new_bundle)

        if self._disclosure_debug:
            debug_lines = ["# Prompt Disclosure Debug", ""]
            for item in rendered:
                debug_lines.append(f"- {item.section_id}: {item.mode.value} ({item.reason})")
            prompt += "\n\n---\n\n" + "\n".join(debug_lines)

        return prompt

    async def _build_disclosure_signals(
        self,
        *,
        user_input: str,
        mode: str,
        mention_hint: str | None,
        has_plan_context: bool,
        has_recent_async_results: bool,
    ) -> DisclosureSignals:
        """构建本轮披露信号。"""
        tool_failures = await self._count_recent_tool_failures()
        force_full: set[str] = set()
        if mention_hint:
            force_full.add("mention_hint")
        if has_plan_context:
            force_full.add("plan_context")

        return DisclosureSignals(
            user_input=user_input,
            mode=mode,
            mention_hit=bool(mention_hint),
            tool_failures=tool_failures,
            has_plan_context=has_plan_context,
            has_recent_async_results=has_recent_async_results,
            force_full_sections=force_full,
        )

    def _apply_rollout_stage(
        self,
        decisions: dict[str, SectionDecision],
    ) -> dict[str, SectionDecision]:
        """按 rollout stage 限制渐进式揭露范围。"""
        stage = max(0, min(3, int(self._disclosure_rollout_stage)))

        # Stage 3: full progressive
        if stage >= 3:
            return decisions

        # Stage 2: expanded（在 Stage1 基础上加入 memory/user/agents）
        if stage == 2:
            allow_progressive = {
                "today_log",
                "current_context",
                "installed_skills",
                "async_task_results",
                "user_profile",
                "memory",
                "agent_config",
                "subagent_roster",
            }
            restricted = dict(decisions)
            for sid, decision in decisions.items():
                if sid not in allow_progressive and decision.mode != DisclosureMode.FULL:
                    restricted[sid] = SectionDecision(
                        mode=DisclosureMode.FULL,
                        reason="rollout_stage_2_force_full",
                        ttl_turns=decision.ttl_turns,
                    )
            return restricted

        # Stage 1: 仅低风险 section
        if stage == 1:
            allow_progressive = {
                "today_log",
                "current_context",
                "installed_skills",
                "async_task_results",
            }
            restricted = dict(decisions)
            for sid, decision in decisions.items():
                if sid not in allow_progressive and decision.mode != DisclosureMode.FULL:
                    restricted[sid] = SectionDecision(
                        mode=DisclosureMode.FULL,
                        reason="rollout_stage_1_force_full",
                        ttl_turns=decision.ttl_turns,
                    )
            return restricted

        # Stage 0: shadow（不改变输出）
        shadow = dict(decisions)
        non_full = 0
        for sid, decision in decisions.items():
            if decision.mode != DisclosureMode.FULL:
                non_full += 1
                shadow[sid] = SectionDecision(
                    mode=DisclosureMode.FULL,
                    reason="rollout_stage_0_shadow",
                    ttl_turns=decision.ttl_turns,
                )

        if non_full > 0:
            try:
                from loguru import logger

                logger.debug(
                    "PromptDisclosure shadow stage: session={} non_full_sections={} total_sections={}",
                    self.session_id,
                    non_full,
                    len(decisions),
                )
            except Exception:
                pass
        return shadow

    async def _count_recent_tool_failures(self, limit: int = 3) -> int:
        """统计最近 N 次工具调用中的失败数。"""
        if self._db is None:
            return 0
        try:
            from sqlalchemy import select

            result = await self._db.execute(
                select(ToolCallLog.error)
                .where(ToolCallLog.session_id == self.session_id)
                .order_by(ToolCallLog.created_at.desc())
                .limit(limit)
            )
            rows = result.all()
            return sum(1 for row in rows if row[0])
        except Exception:
            return 0

    async def _load_prompt_disclosure_bundle(self) -> dict[str, Any]:
        """读取 session.extra.prompt_disclosure。"""
        if self._db is None:
            return {}
        try:
            from sqlalchemy import select

            result = await self._db.execute(
                select(SessionRecord).where(SessionRecord.id == self.session_id)
            )
            session_record = result.scalar_one_or_none()
            if session_record is None:
                return {}
            extra = session_record.extra or {}
            pd = extra.get("prompt_disclosure")
            return pd if isinstance(pd, dict) else {}
        except Exception:
            return {}

    async def _save_prompt_disclosure_bundle(self, bundle: dict[str, Any]) -> None:
        """写回 session.extra.prompt_disclosure（轻量持久化）。"""
        if self._db is None:
            return
        try:
            from sqlalchemy import select

            result = await self._db.execute(
                select(SessionRecord).where(SessionRecord.id == self.session_id)
            )
            session_record = result.scalar_one_or_none()
            if session_record is None:
                return
            extra = dict(session_record.extra or {})
            extra["prompt_disclosure"] = bundle
            session_record.extra = extra
            await self._db.flush()
        except Exception:
            pass

    async def _load_async_task_results(self) -> list[dict[str, Any]]:
        """加载当前 session 最近已完成的 SubAgent/Cron 异步结果。"""
        if self._db is None:
            return []

        rows: list[dict[str, Any]] = []

        # SubAgent 任务结果
        try:
            from sqlalchemy import select

            sub_result = await self._db.execute(
                select(SubAgentTask)
                .where(
                    SubAgentTask.parent_session_id == self.session_id,
                    SubAgentTask.status.in_([TaskStatus.DONE, TaskStatus.FAILED]),
                )
                .order_by(SubAgentTask.finished_at.desc())
                .limit(20)
            )
            for task in sub_result.scalars().all():
                rows.append(
                    {
                        "source": "sub_agent",
                        "task_id": task.id,
                        "execution_id": None,
                        "agent_name": task.agent_name,
                        "task_prompt": task.task_prompt,
                        "status": str(task.status),
                        "result": task.result or "",
                        "error": task.error or "",
                        "finished_at": task.finished_at.isoformat() if task.finished_at else "",
                    }
                )
        except Exception:
            pass

        # Cron 执行结果（target_session_id 命中当前会话）
        try:
            from agentpal.models.cron import CronStatus
            from sqlalchemy import select

            cron_result = await self._db.execute(
                select(CronJobExecution, CronJob)
                .join(CronJob, CronJobExecution.cron_job_id == CronJob.id)
                .where(
                    CronJob.target_session_id == self.session_id,
                    CronJobExecution.status.in_([CronStatus.DONE, CronStatus.FAILED]),
                )
                .order_by(CronJobExecution.finished_at.desc())
                .limit(20)
            )
            for execution, job in cron_result.all():
                rows.append(
                    {
                        "source": "cron",
                        "task_id": None,
                        "execution_id": execution.id,
                        "agent_name": execution.agent_name,
                        "task_prompt": f"定时任务「{execution.cron_job_name or job.name}」",
                        "status": str(execution.status),
                        "result": execution.result or "",
                        "error": execution.error or "",
                        "finished_at": execution.finished_at.isoformat() if execution.finished_at else "",
                    }
                )
        except Exception:
            pass

        rows.sort(key=lambda x: str(x.get("finished_at") or ""), reverse=True)
        return rows

    async def _load_chat_attachments(
        self,
        file_ids: list[str] | None,
    ) -> list[TaskArtifact]:
        """加载当前 session 可访问的上传文件附件。"""
        if not file_ids or self._db is None:
            return []

        resolved_ids = [fid for fid in file_ids if fid]
        if not resolved_ids:
            return []

        from sqlalchemy import select

        result = await self._db.execute(
            select(TaskArtifact).where(
                TaskArtifact.id.in_(resolved_ids),
                TaskArtifact.artifact_type == "uploaded_file",
            )
        )
        artifacts = result.scalars().all()

        allowed: list[TaskArtifact] = []
        for artifact in artifacts:
            extra = artifact.extra or {}
            if extra.get("session_id") == self.session_id:
                allowed.append(artifact)

        artifact_map = {a.id: a for a in allowed}
        ordered = [artifact_map[fid] for fid in resolved_ids if fid in artifact_map]
        return ordered

    def _build_attachment_context(self, attachments: list[TaskArtifact]) -> str | None:
        """构建供模型使用的附件上下文摘要。"""
        if not attachments:
            return None

        lines = ["[附件上下文] 用户上传了以下文件，可按需分析："]
        for idx, artifact in enumerate(attachments, start=1):
            extra = artifact.extra or {}
            sha256 = str(extra.get("sha256") or "")
            lines.append(
                f"{idx}. file_id={artifact.id}; name={artifact.name}; "
                f"mime={artifact.mime_type or 'application/octet-stream'}; "
                f"size={artifact.size_bytes or 0}; sha256={sha256[:16]}"
            )
        lines.append(
            "如需读取内容，请优先使用 read_uploaded_file 工具并传入 file_id，不要臆测文件内容。"
        )
        return "\n".join(lines)

    # ── 核心对话（含工具调用循环）────────────────────────

    async def reply(
        self,
        user_input: str,
        images: list[str] | None = None,
        file_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        """处理用户输入，支持多轮工具调用后返回最终回复。"""
        meta: dict[str, Any] | None = None
        if images or file_ids:
            meta = {}
            if images:
                meta["images"] = images
            if file_ids:
                meta["file_ids"] = file_ids

        attachments = await self._load_chat_attachments(file_ids)
        attachment_context = self._build_attachment_context(attachments)
        if meta is not None and attachment_context:
            meta["attachment_context"] = attachment_context

        await self._remember_user(user_input, meta=meta)
        # 防御性 commit：确保 user 消息持久化（防止 StreamingResponse 生命周期 bug）
        if self._db is not None:
            await self._db.commit()
        history_with_meta = await self._get_history_with_meta(limit=20)
        toolkit = await self._build_active_toolkit()

        enabled_tool_names = _get_tool_names(toolkit)
        skill_prompts = await self._load_prompt_skills()
        mention_hint = await self._extract_mention_hint(user_input)
        async_task_results = await self._load_async_task_results()

        system_prompt = await self._build_system_prompt(
            enabled_tool_names or None,
            skill_prompts=skill_prompts or None,
            user_input=user_input,
            mode=AgentMode.NORMAL,
            mention_hint=mention_hint,
            async_task_results=async_task_results,
        )

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        # 重建历史（排除最后一条——即刚写入的 user 消息，下面手动追加多模态版本）
        for msg_dict, meta in history_with_meta[:-1]:
            messages.append(_rebuild_multimodal(msg_dict, meta))
        # 构建用户消息（支持多模态图片 + 上传附件上下文）
        user_message_text = (
            f"{user_input}\n\n{attachment_context}"
            if attachment_context else user_input
        )
        messages.append(_build_user_message(user_message_text, images))

        response = None
        usage_rounds: list[tuple[int, int, int]] = []  # (round, input, output)

        # ── 工具调用循环 ──────────────────────────────────
        for round_idx in range(MAX_TOOL_ROUNDS):
            tools_schema = toolkit.get_json_schemas() if toolkit else None
            model = _build_model(self._model_config)
            response = await model(messages, tools=tools_schema)

            # 记录本轮 token 用量
            if response.usage is not None:
                u = response.usage
                usage_rounds.append((round_idx + 1, u.input_tokens, u.output_tokens))

            tool_calls = [
                block for block in (response.content or [])
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]

            if not tool_calls:
                break  # 无工具调用，返回文本回复

            openai_tool_calls = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("input", {}), ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ]
            text_parts = [
                b["text"] for b in (response.content or [])
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            messages.append({
                "role": "assistant",
                "content": "".join(text_parts) or None,
                "tool_calls": openai_tool_calls,
            })

            for tool_call in tool_calls:
                # ── Tool Guard check (non-streaming) ──────
                from agentpal.tools.tool_guard import ToolGuardManager

                guard = ToolGuardManager.get_instance()
                session_threshold = await self._get_guard_threshold()
                guard_result = guard.check(
                    tool_call.get("name", ""),
                    tool_call.get("input", {}),
                    session_threshold,
                )
                if guard_result.needs_confirmation:
                    # 非流式场景：直接拒绝（channel 场景由 channel.py 拦截处理）
                    tc_id = tool_call.get("id", str(uuid.uuid4()))
                    cancel_msg = (
                        f"Tool '{tool_call.get('name', '')}' blocked by safety guard "
                        f"(level {guard_result.level}, threshold "
                        f"{session_threshold if session_threshold is not None else guard.default_threshold}). "
                        f"Rule: {guard_result.rule_name or 'default'}. "
                        f"Please confirm via the web interface or adjust the security threshold."
                    )
                    messages.append({"role": "tool", "tool_call_id": tc_id, "content": cancel_msg})
                    continue

                tool_msg = await self._execute_tool(toolkit, tool_call)
                messages.append(tool_msg)

        final_text = _extract_text(response)
        await self._remember_assistant(final_text)
        # 写 token 用量日志 + 更新 session.context_tokens
        await self._record_turn_usage(usage_rounds)
        # 防御性 commit：确保 assistant 消息 + 用量持久化
        if self._db is not None:
            await self._db.commit()
        # 触发记忆压缩（后台异步，不阻塞）
        await self._memory_writer.maybe_flush(
            self.session_id, self.memory, self._ws_manager, self._model_config
        )
        return final_text

    async def reply_stream(
        self,
        user_input: str,
        images: list[str] | None = None,
        file_ids: list[str] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式对话，yield SSE 事件 dict。

        事件类型::

            {"type": "thinking_delta", "delta": "..."}
            {"type": "tool_start", "id": "...", "name": "...", "input": {...}}
            {"type": "tool_done",  "id": "...", "name": "...", "output": "...",
             "error": null, "duration_ms": 3}
            {"type": "text_delta", "delta": "..."}
            {"type": "done"}
            {"type": "error",      "message": "..."}
        """
        try:
            # ── Plan Mode 状态路由 ──────────────────────────
            mode = await self._load_agent_mode()

            # 退出计划（任何非 normal 状态都可以退出）
            if IntentClassifier.is_exit_plan(user_input) and mode != AgentMode.NORMAL:
                async for event in self._handle_exit_plan():
                    yield event
                return

            # 确认阶段
            if mode == AgentMode.CONFIRMING:
                async for event in self._handle_confirming(user_input):
                    yield event
                return

            # 执行中用户插话 → 不中断步骤执行，fall through 到正常回复
            # (执行由 SubAgent 进行，PA 仍可正常聊天)

            # 触发新计划
            if mode == AgentMode.NORMAL and IntentClassifier.is_plan_trigger(user_input):
                async for event in self._handle_plan_trigger(user_input, images, file_ids):
                    yield event
                return

            # ── 正常对话流程 ─────────────────────────────────
            meta: dict[str, Any] | None = None
            if images or file_ids:
                meta = {}
                if images:
                    meta["images"] = images
                if file_ids:
                    meta["file_ids"] = file_ids

            attachments = await self._load_chat_attachments(file_ids)
            attachment_context = self._build_attachment_context(attachments)
            if meta is not None and attachment_context:
                meta["attachment_context"] = attachment_context

            await self._remember_user(user_input, meta=meta)
            # 防御性 commit：确保 user 消息持久化（防止 StreamingResponse 生命周期 bug）
            if self._db is not None:
                await self._db.commit()
            history_with_meta = await self._get_history_with_meta(limit=20)
            toolkit = await self._build_active_toolkit()

            enabled_tool_names = _get_tool_names(toolkit)
            skill_prompts = await self._load_prompt_skills()
            mention_hint = await self._extract_mention_hint(user_input)
            async_task_results = await self._load_async_task_results()

            # Plan Mode 执行阶段：构建计划进度上下文（由披露引擎决定注入粒度）
            plan_context_text: str | None = None
            if mode == AgentMode.EXECUTING:
                try:
                    from agentpal.plans.prompts import build_execution_context

                    plan = await self._get_active_plan()
                    if plan:
                        plan_context_text = build_execution_context(plan)
                except Exception:
                    plan_context_text = None

            system_prompt = await self._build_system_prompt(
                enabled_tool_names or None,
                skill_prompts=skill_prompts or None,
                user_input=user_input,
                mode=mode,
                mention_hint=mention_hint,
                async_task_results=async_task_results,
                plan_context_text=plan_context_text,
            )

            messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
            # 重建历史（排除最后一条——即刚写入的 user 消息，下面手动追加多模态版本）
            for msg_dict, meta in history_with_meta[:-1]:
                messages.append(_rebuild_multimodal(msg_dict, meta))
            # 构建用户消息（支持多模态图片 + 上传附件上下文）
            user_message_text = (
                f"{user_input}\n\n{attachment_context}"
                if attachment_context else user_input
            )
            messages.append(_build_user_message(user_message_text, images))

            final_text = ""
            accumulated_thinking = ""
            accumulated_tool_calls: list[dict[str, Any]] = []
            accumulated_files: list[dict[str, Any]] = []
            usage_rounds: list[tuple[int, int, int]] = []  # (round, input, output)

            # 重试事件列表：回调同步写入，主循环在 await 返回后 flush
            retry_events: list[dict[str, Any]] = []

            def _on_retry(attempt: int, max_attempts: int, error: str, delay: float) -> None:
                retry_events.append({
                    "type": "retry",
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "error": error,
                    "delay": round(delay, 1),
                })

            for round_idx in range(MAX_TOOL_ROUNDS):
                # ── 取消检查点 1：LLM 调用前 ──
                if self._cancelled:
                    break

                tools_schema = toolkit.get_json_schemas() if toolkit else None
                retry_events.clear()
                model = _build_model(self._model_config, stream=True, on_retry=_on_retry)

                # stream=True → model() 返回 AsyncGenerator[ChatResponse, None]
                # 重试期间 retry_events 会被回调填充，await 返回后统一 flush
                response_gen = await model(messages, tools=tools_schema)

                # 刷出在 await model() 阶段积累的重试事件
                for rev in retry_events:
                    yield rev
                retry_events.clear()

                prev_thinking_len = 0
                prev_text_len = 0
                final_response = None

                async for chunk in response_gen:
                    # ── 取消检查点 2：流式接收中 ──
                    if self._cancelled:
                        break

                    # 刷出流式期间积累的重试事件（mid-stream retry）
                    for rev in retry_events:
                        yield rev
                    retry_events.clear()

                    final_response = chunk
                    has_tool_calls = any(
                        isinstance(b, dict) and b.get("type") == "tool_use"
                        for b in (chunk.content or [])
                    )

                    for block in (chunk.content or []):
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")

                        if btype == "thinking":
                            full = block.get("thinking", "")
                            if len(full) > prev_thinking_len:
                                yield {"type": "thinking_delta", "delta": full[prev_thinking_len:]}
                                prev_thinking_len = len(full)
                            accumulated_thinking = full  # 记录最终完整 thinking

                        elif btype == "text" and not has_tool_calls:
                            full = block.get("text", "")
                            if len(full) > prev_text_len:
                                yield {"type": "text_delta", "delta": full[prev_text_len:]}
                                prev_text_len = len(full)

                if final_response is None:
                    break

                response = final_response
                # 记录本轮 token 用量
                if response.usage is not None:
                    u = response.usage
                    usage_rounds.append((round_idx + 1, u.input_tokens, u.output_tokens))

                tool_calls = [
                    b for b in (response.content or [])
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                ]

                if not tool_calls:
                    final_text = "".join(
                        b.get("text", "")
                        for b in (response.content or [])
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                    break

                openai_tool_calls = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(
                                tc.get("input", {}), ensure_ascii=False
                            ),
                        },
                    }
                    for tc in tool_calls
                ]
                text_parts = [
                    b["text"]
                    for b in (response.content or [])
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                messages.append(
                    {
                        "role": "assistant",
                        "content": "".join(text_parts) or None,
                        "tool_calls": openai_tool_calls,
                    }
                )

                for tool_call in tool_calls:
                    # ── 取消检查点 3：工具执行前 ──
                    if self._cancelled:
                        break

                    tc_id = tool_call.get("id", str(uuid.uuid4()))
                    tc_name = tool_call.get("name", "")
                    tc_input = tool_call.get("input", {})

                    # ── Tool Guard check ──────────────────────
                    from agentpal.tools.tool_guard import ToolGuardManager

                    guard = ToolGuardManager.get_instance()
                    session_threshold = await self._get_guard_threshold()
                    guard_result = guard.check(tc_name, tc_input, session_threshold)

                    if guard_result.needs_confirmation:
                        request_id = str(uuid.uuid4())
                        yield {
                            "type": "tool_guard_request",
                            "request_id": request_id,
                            "tool_name": tc_name,
                            "tool_input": tc_input,
                            "level": guard_result.level,
                            "rule": guard_result.rule_name,
                            "threshold": session_threshold if session_threshold is not None else guard.default_threshold,
                            "description": guard_result.description,
                        }

                        pending = guard.create_pending(request_id, tc_name, tc_input)
                        # 等待确认（5s 间隔发心跳，最长 5 分钟）
                        deadline = time() + 300
                        while time() < deadline:
                            try:
                                await asyncio.wait_for(pending.event.wait(), timeout=5.0)
                                break
                            except asyncio.TimeoutError:
                                yield {"type": "tool_guard_waiting", "request_id": request_id}

                        approved = pending.approved
                        guard.remove_pending(request_id)
                        yield {
                            "type": "tool_guard_resolved",
                            "request_id": request_id,
                            "approved": approved,
                        }

                        if not approved:
                            cancel_msg = "Due to insufficient security clearance, the tool call has been cancelled."
                            messages.append(
                                {"role": "tool", "tool_call_id": tc_id, "content": cancel_msg}
                            )
                            accumulated_tool_calls.append({
                                "id": tc_id,
                                "name": tc_name,
                                "input": tc_input,
                                "output": cancel_msg,
                                "error": None,
                                "duration_ms": 0,
                                "status": "cancelled",
                            })
                            continue  # 跳过此工具，继续处理其他工具调用

                    # ── 正常执行工具 ──────────────────────────
                    yield {"type": "tool_start", "id": tc_id, "name": tc_name, "input": tc_input}

                    output_text, error_text, duration_ms = await self._run_tool(toolkit, tool_call)

                    yield {
                        "type": "tool_done",
                        "id": tc_id,
                        "name": tc_name,
                        "output": output_text,
                        "error": error_text,
                        "duration_ms": duration_ms,
                    }
                    messages.append(
                        {"role": "tool", "tool_call_id": tc_id, "content": output_text}
                    )

                    # 收集工具调用记录
                    accumulated_tool_calls.append({
                        "id": tc_id,
                        "name": tc_name,
                        "input": tc_input,
                        "output": output_text,
                        "error": error_text,
                        "duration_ms": duration_ms,
                        "status": "done",
                    })

                    # 收集 send_file_to_user 产生的文件
                    if tc_name == "send_file_to_user" and not error_text:
                        try:
                            info = json.loads(output_text)
                            if info.get("status") == "sent":
                                accumulated_files.append({
                                    "url": info["url"],
                                    "name": info["filename"],
                                    "mime": info.get("mime", "application/octet-stream"),
                                })
                        except Exception:
                            pass

            # 构造 meta 并持久化
            meta: dict[str, Any] = {}
            if accumulated_thinking:
                meta["thinking"] = accumulated_thinking
            if accumulated_tool_calls:
                meta["tool_calls"] = accumulated_tool_calls
            if accumulated_files:
                meta["files"] = accumulated_files

            await self._remember_assistant(final_text, meta=meta or None)
            # 写 token 用量日志 + 更新 session.context_tokens
            await self._record_turn_usage(usage_rounds)
            # 防御性 commit：确保 assistant 消息 + 用量持久化（防止 StreamingResponse 生命周期 bug）
            if self._db is not None:
                await self._db.commit()
            yield {"type": "done"}

            # 触发记忆压缩（后台异步，不阻塞 SSE）
            await self._memory_writer.maybe_flush(
                self.session_id, self.memory, self._ws_manager, self._model_config
            )

        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断开连接，保存已生成的部分内容
            import logging
            logger = logging.getLogger(__name__)
            logger.info("reply_stream cancelled for session %s, saving partial content", self.session_id)
            self._cancelled = True
            _final = locals().get("final_text", "")
            _tools = locals().get("accumulated_tool_calls", [])
            _thinking = locals().get("accumulated_thinking", "")
            _usage = locals().get("usage_rounds", [])
            if _final or _tools:
                _meta: dict[str, Any] = {"cancelled": True}
                if _thinking:
                    _meta["thinking"] = _thinking
                if _tools:
                    _meta["tool_calls"] = _tools
                try:
                    await self._remember_assistant(_final or "(cancelled)", meta=_meta)
                    await self._record_turn_usage(_usage)
                    if self._db is not None:
                        await self._db.commit()
                except Exception:
                    pass
            return

        except Exception as exc:  # noqa: BLE001
            # 刷出未发送的重试事件（重试全部失败时）
            for rev in retry_events:
                yield rev
            yield {"type": "error", "message": str(exc)}

    # ── Plan Mode Handlers ─────────────────────────────────

    async def _handle_plan_trigger(
        self,
        user_input: str,
        images: list[str] | None = None,
        file_ids: list[str] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """处理计划触发：生成计划 → 进入确认阶段。"""
        from agentpal.plans.prompts import PLAN_GENERATION_PROMPT, build_confirm_context

        meta: dict[str, Any] | None = None
        if images or file_ids:
            meta = {}
            if images:
                meta["images"] = images
            if file_ids:
                meta["file_ids"] = file_ids

        await self._remember_user(user_input, meta=meta)
        if self._db is not None:
            await self._db.commit()

        await self._set_agent_mode(AgentMode.PLANNING)
        yield {"type": "plan_generating", "goal": user_input}

        # 构建 plan 生成 system prompt
        toolkit = await self._build_active_toolkit()
        enabled_tool_names = _get_tool_names(toolkit)
        skill_prompts = await self._load_prompt_skills()
        base_prompt = await self._build_system_prompt(
            enabled_tool_names or None,
            skill_prompts=skill_prompts or None,
            user_input=user_input,
            mode=AgentMode.PLANNING,
            plan_context_text=PLAN_GENERATION_PROMPT,
            async_task_results=await self._load_async_task_results(),
        )
        plan_prompt = base_prompt
        attachments = await self._load_chat_attachments(file_ids)
        attachment_context = self._build_attachment_context(attachments)
        user_message_text = (
            f"{user_input}\n\n{attachment_context}"
            if attachment_context else user_input
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": plan_prompt},
            _build_user_message(user_message_text, images),
        ]

        final_text = ""
        # LLM 工具循环（最多 MAX_PLAN_TOOL_ROUNDS 轮，允许收集信息）
        for round_idx in range(MAX_PLAN_TOOL_ROUNDS):
            tools_schema = toolkit.get_json_schemas() if toolkit else None
            model = _build_model(self._model_config, stream=True)
            response_gen = await model(messages, tools=tools_schema)

            prev_text_len = 0
            final_response = None

            async for chunk in response_gen:
                final_response = chunk
                for block in (chunk.content or []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "thinking":
                        pass  # 不推 thinking delta
                    elif block.get("type") == "text":
                        full = block.get("text", "")
                        # 不推 text_delta — plan JSON 由 plan_ready 事件结构化推送
                        prev_text_len = len(full)

            if final_response is None:
                break

            response = final_response
            tool_calls = [
                b for b in (response.content or [])
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]

            if not tool_calls:
                final_text = "".join(
                    b.get("text", "")
                    for b in (response.content or [])
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                break

            # 处理工具调用（允许收集信息）
            openai_tool_calls = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("input", {}), ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ]
            text_parts = [
                b["text"] for b in (response.content or [])
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            messages.append({
                "role": "assistant",
                "content": "".join(text_parts) or None,
                "tool_calls": openai_tool_calls,
            })
            for tool_call in tool_calls:
                tc_id = tool_call.get("id", str(uuid.uuid4()))
                tc_name = tool_call.get("name", "")
                yield {"type": "tool_start", "id": tc_id, "name": tc_name, "input": tool_call.get("input", {})}
                output_text, error_text, duration_ms = await self._run_tool(toolkit, tool_call)
                yield {"type": "tool_done", "id": tc_id, "name": tc_name, "output": output_text, "error": error_text, "duration_ms": duration_ms}
                messages.append({"role": "tool", "tool_call_id": tc_id, "content": output_text})

        # 解析 JSON 计划
        plan = self._parse_plan_from_text(final_text, user_input)
        if plan is None:
            await self._set_agent_mode(AgentMode.NORMAL)
            yield {"type": "error", "message": "无法从 LLM 输出中解析计划 JSON。"}
            return

        store = self._get_plan_store()
        await store.save(plan)
        await self._set_agent_mode(AgentMode.CONFIRMING)

        await self._remember_assistant(
            f"我已为你制定了执行计划：{plan.summary}\n共 {len(plan.steps)} 步。请确认是否开始执行？",
            meta={"plan_id": plan.id},
        )
        if self._db is not None:
            await self._db.commit()

        yield {"type": "plan_ready", "plan": plan.to_dict()}
        yield {"type": "done"}

    async def _handle_confirming(self, user_input: str) -> AsyncGenerator[dict[str, Any], None]:
        """处理确认阶段的用户输入。"""
        from agentpal.plans.prompts import build_confirm_context, build_revise_prompt

        await self._remember_user(user_input)
        if self._db is not None:
            await self._db.commit()

        intent = IntentClassifier.classify_confirm(user_input)
        plan = await self._get_active_plan()

        if plan is None:
            await self._set_agent_mode(AgentMode.NORMAL)
            yield {"type": "text_delta", "delta": "当前没有待确认的计划。"}
            yield {"type": "done"}
            return

        if intent == "cancel":
            plan.status = PlanStatus.CANCELLED
            store = self._get_plan_store()
            await store.save(plan)
            await self._set_agent_mode(AgentMode.NORMAL)
            await self._remember_assistant("好的，计划已取消。")
            if self._db is not None:
                await self._db.commit()
            yield {"type": "plan_cancelled"}
            yield {"type": "text_delta", "delta": "好的，计划已取消。"}
            yield {"type": "done"}

        elif intent == "approve":
            # 开始执行
            async for event in self._start_plan_execution(plan):
                yield event

        elif intent == "modify":
            # 回到 planning 模式修改计划
            await self._set_agent_mode(AgentMode.PLANNING)
            async for event in self._revise_plan(plan, user_input):
                yield event

        else:
            # unknown → 在计划上下文中正常回答
            confirm_ctx = build_confirm_context(plan)
            toolkit = await self._build_active_toolkit()
            enabled_tool_names = _get_tool_names(toolkit)
            base_prompt = await self._build_system_prompt(
                enabled_tool_names or None,
                user_input=user_input,
                mode=AgentMode.CONFIRMING,
                plan_context_text=confirm_ctx,
                async_task_results=await self._load_async_task_results(),
            )
            system_prompt = base_prompt

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ]
            model = _build_model(self._model_config, stream=True)
            response_gen = await model(messages)

            prev_text_len = 0
            final_text = ""
            async for chunk in response_gen:
                for block in (chunk.content or []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        full = block.get("text", "")
                        if len(full) > prev_text_len:
                            yield {"type": "text_delta", "delta": full[prev_text_len:]}
                            prev_text_len = len(full)
                        final_text = full

            await self._remember_assistant(final_text)
            if self._db is not None:
                await self._db.commit()
            yield {"type": "done"}

    async def _start_plan_execution(self, plan: Plan) -> AsyncGenerator[dict[str, Any], None]:
        """开始执行计划。"""
        plan.status = PlanStatus.EXECUTING
        store = self._get_plan_store()
        await store.save(plan)
        await self._set_agent_mode(AgentMode.EXECUTING)

        msg = f"计划开始执行！共 {len(plan.steps)} 步。"
        await self._remember_assistant(msg)
        if self._db is not None:
            await self._db.commit()
        yield {"type": "text_delta", "delta": msg}

        # 启动第一步
        async for event in self._dispatch_plan_step(plan, 0):
            yield event
        yield {"type": "done"}

    async def _dispatch_plan_step(
        self, plan: Plan, step_index: int,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """派遣计划的某一步到 SubAgent 执行。

        使用独立 DB session 创建 SubAgentTask + 查询 SubAgentDefinition，
        避免在主 streaming session 上持有 SQLite 写锁导致其他 endpoint 阻塞。
        """
        from agentpal.plans.prompts import build_step_prompt

        if step_index >= len(plan.steps):
            return

        step = plan.steps[step_index]
        task_prompt = build_step_prompt(plan, step)

        from agentpal.database import AsyncSessionLocal
        from agentpal.models.agent import SubAgentDefinition

        task_id = str(uuid.uuid4())
        sub_session_id = f"sub:{self.session_id}:{task_id}"

        # 使用独立短事务查询 SubAgentDefinition + 创建 SubAgentTask
        agent_name: str | None = None
        role_prompt = ""
        model_config = self._model_config
        max_tool_rounds = 8

        try:
            async with AsyncSessionLocal() as tmp_db:
                # 自动路由 SubAgent（基于步骤策略/工具）
                agent_def: SubAgentDefinition | None = None

                if step.tools:
                    code_tools = {"execute_python_code", "execute_shell_command", "write_file", "edit_file"}
                    if set(step.tools) & code_tools:
                        agent_def = await tmp_db.get(SubAgentDefinition, "coder")
                    elif "browser_use" in step.tools:
                        agent_def = await tmp_db.get(SubAgentDefinition, "researcher")

                if agent_def is None:
                    agent_def = await tmp_db.get(SubAgentDefinition, "researcher")

                if agent_def:
                    agent_name = agent_def.name
                    role_prompt = agent_def.role_prompt or ""
                    model_config = agent_def.get_model_config(model_config)
                    max_tool_rounds = agent_def.max_tool_rounds

                task = SubAgentTask(
                    id=task_id,
                    parent_session_id=self.session_id,
                    sub_session_id=sub_session_id,
                    task_prompt=task_prompt,
                    status=TaskStatus.PENDING,
                    agent_name=agent_name,
                    task_type="plan_step",
                    execution_log=[],
                    meta={
                        "plan_id": plan.id,
                        "step_index": step_index,
                    },
                )
                tmp_db.add(task)
                await tmp_db.commit()
        except Exception as exc:
            yield {"type": "error", "message": f"创建计划步骤任务失败: {exc}"}
            return

        # 更新计划步骤状态
        plan.mark_step_running(step_index, task_id)
        store = self._get_plan_store()
        await store.save(plan)

        yield {
            "type": "plan_step_start",
            "step_index": step_index,
            "step": {"title": step.title, "description": step.description},
            "total_steps": len(plan.steps),
        }

        # 使用 Scheduler 派遣 SubAgent
        from agentpal.tools.builtin import _get_scheduler
        scheduler = _get_scheduler()
        if scheduler is not None:
            await scheduler.dispatch_sub_agent(
                task_id=task_id,
                task_prompt=task_prompt,
                parent_session_id=self.session_id,
                agent_name=agent_name or "default",
                model_config=model_config,
                role_prompt=role_prompt,
                max_tool_rounds=max_tool_rounds,
            )
        else:
            # Fallback: in-process 执行
            from agentpal.agents.sub_agent import SubAgent
            from agentpal.database import AsyncSessionLocal

            _tid = task_id
            _ssid = sub_session_id
            _mc = model_config
            _rp = role_prompt
            _mtr = max_tool_rounds
            _pid = self.session_id
            _tp = task_prompt

            async def _run_bg() -> None:
                from loguru import logger as _l

                async with AsyncSessionLocal() as bg_db:
                    bg_task = await bg_db.get(SubAgentTask, _tid)
                    if bg_task is None:
                        return
                    sub_memory = MemoryFactory.create("buffer")
                    sub_agent = SubAgent(
                        session_id=_ssid,
                        memory=sub_memory,
                        task=bg_task,
                        db=bg_db,
                        model_config=_mc,
                        role_prompt=_rp,
                        max_tool_rounds=_mtr,
                        parent_session_id=_pid,
                    )
                    await sub_agent.run(_tp)
                    await bg_db.commit()

            asyncio.create_task(_run_bg())

    async def handle_plan_step_done(self, payload: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        """处理计划步骤完成事件（由 PA daemon 调用）。

        更新 plan step 结果 → 推进下一步或完成。
        """
        plan_id = payload.get("plan_id", "")
        step_index = payload.get("step_index", 0)
        status = payload.get("status", "")
        result = payload.get("result", "")
        task_id = payload.get("task_id", "")

        store = self._get_plan_store()
        plan = await store.load(self.session_id, plan_id)
        if plan is None:
            yield {"type": "error", "message": f"计划不存在: {plan_id}"}
            return

        if status == "done":
            plan.mark_step_done(step_index, result)
            yield {
                "type": "plan_step_done",
                "step_index": step_index,
                "result": result[:500],
            }
        else:
            error = payload.get("error", result or "步骤执行失败")
            plan.mark_step_failed(step_index, error)
            yield {
                "type": "plan_step_done",
                "step_index": step_index,
                "result": f"失败: {error[:500]}",
            }
            # 步骤失败 → 中止计划
            plan.status = PlanStatus.FAILED
            await store.save(plan)
            await self._set_agent_mode(AgentMode.NORMAL)
            yield {"type": "text_delta", "delta": f"\n计划执行失败（步骤 {step_index + 1}）: {error[:200]}"}
            yield {"type": "done"}
            return

        await store.save(plan)

        # 检查是否还有下一步
        if plan.all_done():
            plan.status = PlanStatus.COMPLETED
            await store.save(plan)
            await self._set_agent_mode(AgentMode.NORMAL)

            summary = f"计划执行完成！共 {len(plan.steps)} 步全部完成。"
            await self._remember_assistant(summary, meta={"plan_id": plan.id})
            if self._db is not None:
                await self._db.commit()

            yield {"type": "plan_completed", "plan": plan.to_dict()}
            yield {"type": "text_delta", "delta": summary}
            yield {"type": "done"}
        elif plan.auto_proceed:
            # 自动推进下一步
            next_step = plan.next_pending_step()
            if next_step:
                async for event in self._dispatch_plan_step(plan, next_step.index):
                    yield event

    async def _revise_plan(self, plan: Plan, user_feedback: str) -> AsyncGenerator[dict[str, Any], None]:
        """根据用户反馈修改计划。"""
        from agentpal.plans.prompts import build_revise_prompt

        yield {"type": "plan_generating", "goal": plan.goal}

        revise_prompt_text = build_revise_prompt(plan, user_feedback)
        toolkit = await self._build_active_toolkit()
        enabled_tool_names = _get_tool_names(toolkit)
        base_prompt = await self._build_system_prompt(
            enabled_tool_names or None,
            user_input=user_feedback,
            mode=AgentMode.PLANNING,
            plan_context_text=revise_prompt_text,
            async_task_results=await self._load_async_task_results(),
        )
        system_prompt = base_prompt

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_feedback},
        ]

        model = _build_model(self._model_config, stream=True)
        response_gen = await model(messages)

        prev_text_len = 0
        final_text = ""
        async for chunk in response_gen:
            for block in (chunk.content or []):
                if isinstance(block, dict) and block.get("type") == "text":
                    full = block.get("text", "")
                    # 不推 text_delta — plan JSON 由 plan_ready 事件结构化推送
                    prev_text_len = len(full)
                    final_text = full

        # 尝试解析修改后的计划
        new_plan = self._parse_plan_from_text(final_text, plan.goal)
        if new_plan is not None:
            new_plan.id = plan.id  # 保持 ID 不变
            new_plan.session_id = plan.session_id
            new_plan.created_at = plan.created_at
            store = self._get_plan_store()
            await store.save(new_plan)
            await self._set_agent_mode(AgentMode.CONFIRMING)
            yield {"type": "plan_ready", "plan": new_plan.to_dict()}
        else:
            # 解析失败，保持原计划
            await self._set_agent_mode(AgentMode.CONFIRMING)

        await self._remember_assistant(final_text)
        if self._db is not None:
            await self._db.commit()
        yield {"type": "done"}

    async def _handle_exit_plan(self) -> AsyncGenerator[dict[str, Any], None]:
        """退出计划模式。"""
        plan = await self._get_active_plan()
        if plan:
            plan.status = PlanStatus.CANCELLED
            store = self._get_plan_store()
            await store.save(plan)
        await self._set_agent_mode(AgentMode.NORMAL)
        await self._remember_assistant("好的，计划已取消。")
        if self._db is not None:
            await self._db.commit()
        yield {"type": "plan_cancelled"}
        yield {"type": "text_delta", "delta": "好的，计划已取消。"}
        yield {"type": "done"}

    # ── Plan Mode 辅助方法 ─────────────────────────────────

    async def _load_agent_mode(self) -> str:
        """从 DB 读取 agent_mode（独立短事务，不污染主 session）。

        使用独立 AsyncSession 避免在 reply_stream 主 db session 上开启
        长时间 SELECT 事务，防止与其他写操作竞争 SQLite 锁。
        """
        if self._db is None:
            return AgentMode.NORMAL
        try:
            from agentpal.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as tmp_db:
                result = await tmp_db.execute(
                    text("SELECT agent_mode FROM sessions WHERE id = :sid"),
                    {"sid": self.session_id},
                )
                row = result.first()
                return (row[0] if row and row[0] else None) or AgentMode.NORMAL
        except Exception:
            return AgentMode.NORMAL

    async def _set_agent_mode(self, mode: str) -> None:
        """更新 DB agent_mode（独立短事务，立即提交释放锁）。

        使用独立 AsyncSession + 立即 commit，避免 flush-without-commit
        在主 session 上持有 SQLite 写锁导致其他 endpoint "database is locked"。
        """
        try:
            from agentpal.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as tmp_db:
                await tmp_db.execute(
                    text("UPDATE sessions SET agent_mode = :mode WHERE id = :sid"),
                    {"mode": mode, "sid": self.session_id},
                )
                await tmp_db.commit()
        except Exception:
            pass

    def _get_plan_store(self) -> PlanStore:
        """懒初始化 PlanStore。"""
        if not hasattr(self, "_plan_store"):
            settings = get_settings()
            self._plan_store = PlanStore(settings.plans_dir)
        return self._plan_store

    async def _get_active_plan(self) -> Plan | None:
        """获取当前 session 的活跃计划。"""
        store = self._get_plan_store()
        return await store.get_active(self.session_id)

    def _parse_plan_from_text(self, text: str, goal: str) -> Plan | None:
        """从 LLM 输出中解析 JSON 计划。"""
        # 尝试提取 ```json ... ``` 代码块
        json_match = _re.search(r"```json\s*\n(.*?)\n```", text, _re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接解析整个文本
            json_str = text

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict) or "steps" not in data:
            return None

        plan_id = str(uuid.uuid4())
        steps = [
            PlanStep(
                index=i,
                title=s.get("title", f"步骤 {i + 1}"),
                description=s.get("description", ""),
                strategy=s.get("strategy", ""),
                tools=s.get("tools", []),
            )
            for i, s in enumerate(data["steps"])
        ]

        return Plan(
            id=plan_id,
            session_id=self.session_id,
            goal=data.get("goal", goal),
            summary=data.get("summary", ""),
            status=PlanStatus.CONFIRMING,
            steps=steps,
        )

    # ── SubAgent 派遣 ─────────────────────────────────────

    async def dispatch_sub_agent(
        self,
        task_prompt: str,
        db: Any,
        context: dict[str, Any] | None = None,
        task_type: str | None = None,
        agent_name: str | None = None,
        priority: int = 5,
        max_retries: int = 3,
    ) -> SubAgentTask:
        """创建并异步启动一个 SubAgent 任务。

        支持角色路由：
        - 如果指定 agent_name，直接使用该 SubAgent
        - 如果指定 task_type，自动匹配合适的 SubAgent
        - 都未指定，使用默认配置

        Args:
            task_prompt:  任务描述
            db:           AsyncSession
            context:      附加上下文
            task_type:    任务类型（用于自动路由）
            agent_name:   指定 SubAgent 名称
            priority:     优先级 1-10（10 最高），默认 5
            max_retries:  最大重试次数 0-10，默认 3
        """
        from agentpal.agents.registry import SubAgentRegistry
        from agentpal.agents.sub_agent import SubAgent
        from agentpal.models.agent import SubAgentDefinition

        task_id = str(uuid.uuid4())
        sub_session_id = f"sub:{self.session_id}:{task_id}"

        # 查找合适的 SubAgent 定义
        registry = SubAgentRegistry(db)
        agent_def: SubAgentDefinition | None = None
        role_prompt = ""
        model_config = self._model_config
        max_tool_rounds = 8

        if agent_name:
            agent_def = await db.get(SubAgentDefinition, agent_name)
        elif task_type:
            agent_def = await registry.find_agent_for_task(task_type)

        if agent_def:
            agent_name = agent_def.name
            role_prompt = agent_def.role_prompt or ""
            model_config = agent_def.get_model_config(self._model_config)
            max_tool_rounds = agent_def.max_tool_rounds

        # Clamp priority and max_retries
        priority = max(1, min(10, priority))
        max_retries = max(0, min(10, max_retries))

        task = SubAgentTask(
            id=task_id,
            parent_session_id=self.session_id,
            sub_session_id=sub_session_id,
            task_prompt=task_prompt,
            status=TaskStatus.PENDING,
            agent_name=agent_name,
            task_type=task_type,
            execution_log=[],
            meta=context or {},
            priority=priority,
            max_retries=max_retries,
        )
        db.add(task)
        await db.commit()

        # 将需要传入后台任务的参数提前快照，避免闭包引用请求级 db
        _sub_session_id = sub_session_id
        _task_id = task_id
        _model_config = model_config
        _role_prompt = role_prompt
        _max_tool_rounds = max_tool_rounds
        _parent_session_id = self.session_id

        async def _run_sub_agent() -> None:
            """在独立 AsyncSession 中运行 SubAgent，避免复用请求级 session。

            请求级 session 会在 HTTP 响应返回后被 get_db 关闭，
            而 SubAgent 后台任务可能还在执行 → 用已关闭的 session 会 crash 或锁死。
            """
            import os

            from loguru import logger as _logger

            from agentpal.database import AsyncSessionLocal

            # 设置环境变量，供 produce_artifact 工具获取当前任务 ID
            os.environ["AGENTPAL_CURRENT_TASK_ID"] = _task_id

            async with AsyncSessionLocal() as bg_db:
                # 重新加载 task 到新 session 的 identity map 中
                bg_task = await bg_db.get(SubAgentTask, _task_id)
                if bg_task is None:
                    _logger.error(f"SubAgent 后台任务找不到 task record: {_task_id}")
                    return

                sub_memory = MemoryFactory.create("buffer")
                sub_agent = SubAgent(
                    session_id=_sub_session_id,
                    memory=sub_memory,
                    task=bg_task,
                    db=bg_db,
                    model_config=_model_config,
                    role_prompt=_role_prompt,
                    max_tool_rounds=_max_tool_rounds,
                    parent_session_id=_parent_session_id,
                )
                await sub_agent.run(task_prompt)
                await bg_db.commit()

        task_handle = asyncio.create_task(_run_sub_agent())

        def _on_bg_done(fut: asyncio.Task) -> None:  # type: ignore[type-arg]
            from loguru import logger as _bg_logger

            if fut.cancelled():
                _bg_logger.warning(f"SubAgent bg task {_task_id} was cancelled")
            elif exc := fut.exception():
                _bg_logger.error(f"SubAgent bg task {_task_id} unhandled error: {exc}")

        task_handle.add_done_callback(_on_bg_done)
        return task

    # ── Tool Guard 辅助 ────────────────────────────────────

    async def _get_guard_threshold(self) -> int | None:
        """读取 session 级 tool_guard_threshold，null 回退全局默认。"""
        if self._db is None:
            return None
        try:
            from sqlalchemy import select

            result = await self._db.execute(
                select(SessionRecord.tool_guard_threshold).where(
                    SessionRecord.id == self.session_id
                )
            )
            return result.scalar_one_or_none()
        except Exception:
            return None

    # ── 工具执行 ──────────────────────────────────────────

    async def _run_tool(
        self, toolkit: Any, tool_call: dict[str, Any]
    ) -> tuple[str, str | None, int]:
        """执行单个工具，返回 (output_text, error_text, duration_ms)，并写调用日志。"""
        tool_name = tool_call.get("name", "")
        tool_input = tool_call.get("input", {})
        start_ms = int(time() * 1000)
        output_text = ""
        error_text: str | None = None

        # 释放 SQLite 写锁：先提交已有的事务（memory writes 等），
        # 这样 skill_cli 等工具创建新 session 写入时不会被 "database is locked" 阻塞。
        if self._db is not None:
            try:
                await self._db.commit()
            except Exception:
                pass

        try:
            tool_response = None
            async for chunk in await toolkit.call_tool_function(tool_call):
                tool_response = chunk
            if tool_response:
                output_text = "".join(
                    b.get("text", "")
                    for b in (tool_response.content or [])
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            else:
                output_text = "（无输出）"
        except Exception as exc:
            error_text = str(exc)
            output_text = f"<error>{exc}</error>"

        duration_ms = int(time() * 1000) - start_ms

        if self._db is not None:
            from agentpal.tools.registry import log_tool_call
            try:
                await log_tool_call(
                    self._db,
                    session_id=self.session_id,
                    tool_name=tool_name,
                    input_data=tool_input,
                    output=output_text,
                    error=error_text,
                    duration_ms=duration_ms,
                )
            except Exception:
                pass

        return output_text, error_text, duration_ms

    async def _execute_tool(self, toolkit: Any, tool_call: dict[str, Any]) -> dict[str, Any]:
        """执行单个工具调用，返回 OpenAI tool 消息（供非流式 reply() 使用）。"""
        tool_id = tool_call.get("id", str(uuid.uuid4()))
        output_text, _, _ = await self._run_tool(toolkit, tool_call)
        return {"role": "tool", "tool_call_id": tool_id, "content": output_text}

    # ── Token 用量记录 ────────────────────────────────────────

    async def _record_turn_usage(
        self,
        usage_rounds: list[tuple[int, int, int]],  # [(call_round, input_tokens, output_tokens)]
    ) -> None:
        """将本轮对话的 LLM 用量写入 llm_call_logs，并累加 session.context_tokens。

        每个工具调用轮次记一条 LLMCallLog；最后统一更新 SessionRecord.context_tokens。
        在 reply() / reply_stream() 完成后调用，不阻塞 SSE 流。
        """
        if not usage_rounds or self._db is None:
            return

        from loguru import logger
        from sqlalchemy import text

        model_name = self._model_config.get("model_name", "")
        provider = self._model_config.get("provider", "")
        total_turn_tokens = 0

        for call_round, input_tokens, output_tokens in usage_rounds:
            total = input_tokens + output_tokens
            total_turn_tokens += total
            self._db.add(LLMCallLog(
                session_id=self.session_id,
                model_name=model_name,
                provider=provider,
                call_round=call_round,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total,
            ))

        await self._db.flush()

        if total_turn_tokens == 0:
            return

        # 用原生 SQL UPDATE 直接累加 context_tokens（绕过 ORM identity map，支持 NULL → 0 初始化）
        try:
            await self._db.execute(
                text(
                    "UPDATE sessions SET context_tokens = COALESCE(context_tokens, 0) + :tokens"
                    " WHERE id = :sid"
                ),
                {"tokens": total_turn_tokens, "sid": self.session_id},
            )
            await self._db.flush()
        except Exception as exc:
            logger.warning("_record_turn_usage: 更新 context_tokens 失败 session={} err={}", self.session_id, exc)

        # 读回 context_tokens，触发 token 压缩检查
        try:
            result = await self._db.execute(
                text("SELECT context_tokens FROM sessions WHERE id = :sid"),
                {"sid": self.session_id},
            )
            row = result.first()
            if row and row[0]:
                context_tokens = int(row[0])
                settings = get_settings()
                await self._memory_writer.maybe_compress(
                    session_id=self.session_id,
                    memory=self.memory,
                    ws_manager=self._ws_manager,
                    model_config=self._model_config,
                    context_tokens=context_tokens,
                    context_window=settings.llm_context_window,
                    db=self._db,
                )
        except Exception as exc:
            logger.warning("_record_turn_usage: maybe_compress 检查失败 session={} err={}", self.session_id, exc)

    async def _load_prompt_skills(self) -> list[dict[str, Any]]:
        """加载已启用的 prompt 型技能内容，用于注入 system prompt。"""
        if self._db is None:
            return []
        try:
            from agentpal.skills.manager import SkillManager
            from agentpal.models.session import SessionRecord
            from sqlalchemy import select

            # 查询 session 级 skill 配置
            session_skill_names: list[str] | None = None
            result = await self._db.execute(
                select(SessionRecord).where(SessionRecord.id == self.session_id)
            )
            session_record = result.scalar_one_or_none()
            if session_record and session_record.enabled_skills is not None:
                session_skill_names = session_record.enabled_skills

            mgr = SkillManager(self._db)
            return await mgr.get_prompt_skills(session_skill_names)
        except Exception:
            return []

    async def _build_active_toolkit(self) -> Any:
        """从 DB 读取已启用工具 + 已启用 Skill 工具，构建 agentscope Toolkit。

        支持 session 级别的工具/技能覆盖：
        - session.enabled_tools 非 null → 使用 session 配置与全局启用工具的交集
        - session.enabled_skills 非 null → 使用 session 配置与全局启用技能的交集
        - 如果 session 配置了全局未启用的工具/技能，待全局启用后自动生效
        """
        if self._db is None:
            return None
        from sqlalchemy import select

        from agentpal.models.session import SessionRecord
        from agentpal.tools.registry import build_toolkit, ensure_tool_configs, get_enabled_tools

        await ensure_tool_configs(self._db)
        global_enabled = await get_enabled_tools(self._db)

        # 查询 session 级配置
        session_tools: list[str] | None = None
        session_skills: list[str] | None = None
        result = await self._db.execute(
            select(SessionRecord).where(SessionRecord.id == self.session_id)
        )
        session_record = result.scalar_one_or_none()
        if session_record:
            session_tools = session_record.enabled_tools
            session_skills = session_record.enabled_skills

        # 计算最终启用的内置工具
        if session_tools is not None:
            # session 级配置：取 session 配置与全局启用的交集
            enabled = [t for t in session_tools if t in global_enabled]
        else:
            enabled = global_enabled

        # 加载 skill 工具
        skill_tools: list[dict] = []
        try:
            from agentpal.skills.manager import SkillManager

            mgr = SkillManager(self._db)
            all_skill_tools = await mgr.get_all_skill_tools()

            if session_skills is not None:
                # session 级 skill 过滤
                enabled_skill_names = set(session_skills)
                # 获取全局启用的技能名
                all_skills = await mgr.list_skills()
                global_enabled_skill_names = {
                    s["name"] for s in all_skills if s["enabled"]
                }
                # 交集：session 配置 ∩ 全局启用
                active_skill_names = enabled_skill_names & global_enabled_skill_names
                skill_tools = [
                    t for t in all_skill_tools
                    if t.get("skill_name", "") in active_skill_names
                ]
            else:
                skill_tools = all_skill_tools
        except Exception:
            pass

        return build_toolkit(enabled, extra_tools=skill_tools or None)


# ── 辅助函数 — 从 _llm_helpers.py re-export（保持向后兼容） ──

from agentpal.agents._llm_helpers import (  # noqa: E402, F401
    _build_model,
    _build_user_message,
    _default_model_config,
    _extract_text,
    _extract_thinking,
    _get_tool_names,
    _rebuild_multimodal,
)
