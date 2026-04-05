"""Prompt Sections — 渐进式揭露的数据结构与渲染辅助。"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class DisclosureMode(StrEnum):
    """Section 披露模式。"""

    FULL = "full"
    SUMMARY = "summary"
    REMINDER = "reminder"
    SKIP = "skip"


@dataclass
class PromptSection:
    """可渲染的 prompt section。"""

    section_id: str
    title: str
    content: str
    priority: int = 100
    always_on: bool = False
    summary: str | None = None
    reminder: str | None = None


@dataclass
class RenderedSection:
    """渲染后的 section 结果（供 debug / telemetry）。"""

    section_id: str
    mode: DisclosureMode
    reason: str
    text: str
    injected: bool


@dataclass
class SectionState:
    """session 级 section 状态（持久化到 SessionRecord.extra）。"""

    last_mode: str = DisclosureMode.SKIP
    last_turn: int = 0
    ttl_turns: int = 0
    last_hash: str = ""
    reason: str = ""


def normalize_mode(value: str | None) -> DisclosureMode:
    """将字符串安全转换为 DisclosureMode。"""
    if not value:
        return DisclosureMode.SKIP
    try:
        return DisclosureMode(value)
    except ValueError:
        return DisclosureMode.SKIP


def hash_text(text: str) -> str:
    """生成 section 内容哈希（用于内容变化检测）。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def pick_section_text(section: PromptSection, mode: DisclosureMode) -> str:
    """根据披露模式选择输出文本。"""
    if mode == DisclosureMode.FULL:
        return section.content

    if mode == DisclosureMode.SUMMARY:
        return section.summary or section.content

    if mode == DisclosureMode.REMINDER:
        if section.reminder:
            return section.reminder
        if section.summary:
            return section.summary
        return f"[{section.title}] 使用时按需展开。"

    return ""


def load_section_states(raw: dict[str, Any] | None) -> dict[str, SectionState]:
    """从 SessionRecord.extra.prompt_disclosure.sections 反序列化状态。"""
    if not raw:
        return {}

    out: dict[str, SectionState] = {}
    for section_id, item in raw.items():
        if not isinstance(item, dict):
            continue
        out[section_id] = SectionState(
            last_mode=str(item.get("last_mode", DisclosureMode.SKIP)),
            last_turn=int(item.get("last_turn", 0) or 0),
            ttl_turns=int(item.get("ttl_turns", 0) or 0),
            last_hash=str(item.get("last_hash", "")),
            reason=str(item.get("reason", "")),
        )
    return out


def dump_section_states(states: dict[str, SectionState]) -> dict[str, Any]:
    """序列化 section 状态（写入 SessionRecord.extra）。"""
    return {sid: asdict(state) for sid, state in states.items()}