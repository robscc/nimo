"""DisclosureEngine 单元测试。"""

from __future__ import annotations

from agentpal.workspace.disclosure_engine import DisclosureEngine, DisclosureSignals
from agentpal.workspace.prompt_sections import DisclosureMode, SectionState, hash_text


class TestDisclosureEngine:
    def test_disabled_returns_full(self):
        engine = DisclosureEngine(enabled=False)
        decision = engine.decide(
            section_id="memory",
            section_text="memory content",
            turn=1,
            signals=DisclosureSignals(user_input="hi"),
            prev_state=None,
        )
        assert decision.mode == DisclosureMode.FULL
        assert decision.reason == "legacy_disabled"

    def test_always_on_returns_full(self):
        engine = DisclosureEngine(enabled=True)
        decision = engine.decide(
            section_id="identity",
            section_text="identity",
            turn=1,
            signals=DisclosureSignals(user_input="hi"),
            prev_state=None,
        )
        assert decision.mode == DisclosureMode.FULL
        assert decision.reason == "always_on"

    def test_memory_query_forces_full(self):
        engine = DisclosureEngine(enabled=True)
        decision = engine.decide(
            section_id="memory",
            section_text="memory",
            turn=3,
            signals=DisclosureSignals(user_input="你记得我上次说什么吗"),
            prev_state=SectionState(last_mode="summary", last_turn=1, ttl_turns=5),
        )
        assert decision.mode == DisclosureMode.FULL
        assert decision.reason == "memory_query"

    def test_ttl_after_full_downgrade_to_reminder(self):
        engine = DisclosureEngine(enabled=True, default_ttl_turns=8)
        text = "same content"
        decision = engine.decide(
            section_id="memory",
            section_text=text,
            turn=6,
            signals=DisclosureSignals(user_input="普通问题"),
            prev_state=SectionState(
                last_mode="full",
                last_turn=3,
                ttl_turns=8,
                last_hash=hash_text(text),
            ),
        )
        assert decision.mode == DisclosureMode.REMINDER
        assert decision.reason == "within_ttl_after_full"

    def test_content_changed_to_summary(self):
        engine = DisclosureEngine(enabled=True)
        decision = engine.decide(
            section_id="memory",
            section_text="new content",
            turn=10,
            signals=DisclosureSignals(user_input="普通问题"),
            prev_state=SectionState(
                last_mode="full",
                last_turn=9,
                ttl_turns=8,
                last_hash="old-hash",
            ),
        )
        assert decision.mode == DisclosureMode.SUMMARY
        assert decision.reason == "content_changed"

    def test_budget_downgrade(self):
        engine = DisclosureEngine(enabled=True, max_full_sections_per_turn=2)
        decisions = {
            "identity": engine.decide(
                section_id="identity",
                section_text="a",
                turn=1,
                signals=DisclosureSignals(),
                prev_state=None,
            ),
            "memory": engine.decide(
                section_id="memory",
                section_text="b",
                turn=1,
                signals=DisclosureSignals(user_input="你记得吗"),
                prev_state=None,
            ),
            "user_profile": engine.decide(
                section_id="user_profile",
                section_text="c",
                turn=1,
                signals=DisclosureSignals(user_input="你记得吗"),
                prev_state=None,
            ),
        }
        limited = engine.enforce_full_budget(
            decisions,
            critical_sections={"identity"},
        )
        full_count = sum(1 for d in limited.values() if d.mode == DisclosureMode.FULL)
        assert full_count <= 2
