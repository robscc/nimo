"""Prompt sections 渲染单元测试。"""

from __future__ import annotations

from agentpal.workspace.prompt_sections import (
    DisclosureMode,
    PromptSection,
    normalize_mode,
    pick_section_text,
)


def test_normalize_mode_unknown_defaults_skip():
    assert normalize_mode("unknown") == DisclosureMode.SKIP


def test_pick_full_text():
    section = PromptSection(
        section_id="memory",
        title="Memory",
        content="FULL",
        summary="SUMMARY",
        reminder="REMINDER",
    )
    assert pick_section_text(section, DisclosureMode.FULL) == "FULL"


def test_pick_summary_text():
    section = PromptSection(
        section_id="memory",
        title="Memory",
        content="FULL",
        summary="SUMMARY",
    )
    assert pick_section_text(section, DisclosureMode.SUMMARY) == "SUMMARY"


def test_pick_reminder_fallback_summary():
    section = PromptSection(
        section_id="memory",
        title="Memory",
        content="FULL",
        summary="SUMMARY",
    )
    assert pick_section_text(section, DisclosureMode.REMINDER) == "SUMMARY"


def test_pick_skip_returns_empty():
    section = PromptSection(
        section_id="memory",
        title="Memory",
        content="FULL",
    )
    assert pick_section_text(section, DisclosureMode.SKIP) == ""
