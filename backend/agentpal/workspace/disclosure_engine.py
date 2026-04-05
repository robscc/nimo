"""DisclosureEngine — 上下文渐进式揭露决策器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentpal.workspace.prompt_sections import (
    DisclosureMode,
    SectionState,
    hash_text,
    normalize_mode,
)


@dataclass
class SectionDecision:
    mode: DisclosureMode
    reason: str
    ttl_turns: int = 0


@dataclass
class DisclosureSignals:
    """当前轮的触发信号。"""

    user_input: str = ""
    mode: str = "normal"  # AgentMode string
    mention_hit: bool = False
    tool_failures: int = 0
    has_plan_context: bool = False
    has_recent_async_results: bool = False
    force_full_sections: set[str] = field(default_factory=set)


class DisclosureEngine:
    """规则引擎：决定各 section 在当前轮如何披露。"""

    ALWAYS_ON_SECTIONS = {
        "identity",
        "soul",
        "runtime_env",
        "tool_security",
        "available_tools",
    }

    DEFAULT_REMINDER_TTL = 8
    DEFAULT_SUMMARY_TTL = 4

    def __init__(
        self,
        *,
        enabled: bool = False,
        max_full_sections_per_turn: int = 6,
        default_ttl_turns: int = 8,
    ) -> None:
        self.enabled = enabled
        self.max_full_sections_per_turn = max(1, max_full_sections_per_turn)
        self.default_ttl_turns = max(1, default_ttl_turns)

    def decide(
        self,
        *,
        section_id: str,
        section_text: str,
        turn: int,
        signals: DisclosureSignals,
        prev_state: SectionState | None,
    ) -> SectionDecision:
        """计算某个 section 的披露决策。"""
        if not self.enabled:
            return SectionDecision(mode=DisclosureMode.FULL, reason="legacy_disabled")

        if section_id in self.ALWAYS_ON_SECTIONS:
            return SectionDecision(mode=DisclosureMode.FULL, reason="always_on")

        if section_id in signals.force_full_sections:
            return SectionDecision(
                mode=DisclosureMode.FULL,
                reason="forced_by_signal",
                ttl_turns=self.default_ttl_turns,
            )

        # mode-specific 强制
        if signals.mode in {"planning", "confirming", "executing"}:
            if section_id in {"plan_context", "async_task_results"}:
                return SectionDecision(
                    mode=DisclosureMode.FULL,
                    reason=f"agent_mode:{signals.mode}",
                    ttl_turns=self.default_ttl_turns,
                )

        # mention 触发：仅强制 subagent_roster
        if signals.mention_hit and section_id == "subagent_roster":
            return SectionDecision(
                mode=DisclosureMode.FULL,
                reason="mention_route",
                ttl_turns=self.default_ttl_turns,
            )

        # 工具连续失败时给安全提醒
        if signals.tool_failures >= 2 and section_id == "tool_security":
            return SectionDecision(
                mode=DisclosureMode.SUMMARY,
                reason="tool_failures",
                ttl_turns=self.DEFAULT_SUMMARY_TTL,
            )

        # 计划相关上下文
        if signals.has_plan_context and section_id in {"plan_context"}:
            return SectionDecision(
                mode=DisclosureMode.FULL,
                reason="plan_context_present",
                ttl_turns=self.default_ttl_turns,
            )

        # 异步结果提醒
        if signals.has_recent_async_results and section_id == "async_task_results":
            return SectionDecision(
                mode=DisclosureMode.SUMMARY,
                reason="recent_async_results",
                ttl_turns=self.DEFAULT_SUMMARY_TTL,
            )

        # 用户明确问“记得/之前/上次/历史”→ memory/user/context full
        normalized = (signals.user_input or "").lower()
        memory_keywords = (
            "记得", "之前", "上次", "历史", "回顾", "你知道我", "偏好",
            "remember", "previous", "history", "recap",
        )
        if any(k in normalized for k in memory_keywords):
            if section_id in {"user_profile", "memory", "current_context", "today_log"}:
                return SectionDecision(
                    mode=DisclosureMode.FULL,
                    reason="memory_query",
                    ttl_turns=self.default_ttl_turns,
                )

        # 默认策略：曾经 full 且 TTL 内则 reminder；否则 summary
        prev = prev_state or SectionState()
        prev_mode = normalize_mode(prev.last_mode)
        content_hash = hash_text(section_text)
        content_changed = bool(prev.last_hash and prev.last_hash != content_hash)

        if content_changed:
            return SectionDecision(
                mode=DisclosureMode.SUMMARY,
                reason="content_changed",
                ttl_turns=self.DEFAULT_SUMMARY_TTL,
            )

        ttl = prev.ttl_turns or self.default_ttl_turns
        within_ttl = prev.last_turn > 0 and (turn - prev.last_turn) <= ttl

        if within_ttl and prev_mode == DisclosureMode.FULL:
            return SectionDecision(
                mode=DisclosureMode.REMINDER,
                reason="within_ttl_after_full",
                ttl_turns=ttl,
            )

        if within_ttl and prev_mode == DisclosureMode.SUMMARY:
            return SectionDecision(
                mode=DisclosureMode.REMINDER,
                reason="within_ttl_after_summary",
                ttl_turns=ttl,
            )

        return SectionDecision(
            mode=DisclosureMode.SUMMARY,
            reason="default_summary",
            ttl_turns=self.DEFAULT_SUMMARY_TTL,
        )

    def enforce_full_budget(
        self,
        decisions: dict[str, SectionDecision],
        *,
        critical_sections: set[str] | None = None,
    ) -> dict[str, SectionDecision]:
        """限制本轮 FULL section 数量，防止 prompt 膨胀。"""
        if not self.enabled:
            return decisions

        critical = critical_sections or set()
        full_ids = [sid for sid, d in decisions.items() if d.mode == DisclosureMode.FULL]
        if len(full_ids) <= self.max_full_sections_per_turn:
            return decisions

        # 先保留关键 section
        keep: set[str] = {sid for sid in full_ids if sid in critical}

        # 再按插入顺序补满 budget
        for sid in full_ids:
            if len(keep) >= self.max_full_sections_per_turn:
                break
            keep.add(sid)

        for sid in full_ids:
            if sid not in keep:
                decisions[sid] = SectionDecision(
                    mode=DisclosureMode.SUMMARY,
                    reason="downgraded_by_budget",
                    ttl_turns=self.DEFAULT_SUMMARY_TTL,
                )

        return decisions
