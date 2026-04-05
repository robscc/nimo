"""PersonalAssistant rollout stage 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentpal.agents.personal_assistant import PersonalAssistant
from agentpal.workspace.disclosure_engine import SectionDecision
from agentpal.workspace.prompt_sections import DisclosureMode


def _make_pa(stage: int) -> PersonalAssistant:
    with patch("agentpal.agents.personal_assistant.get_settings") as mock_settings, \
         patch("agentpal.agents.personal_assistant.WorkspaceManager"), \
         patch("agentpal.agents.personal_assistant.MemoryWriter"):
        settings = MagicMock()
        settings.workspace_dir = "/tmp/test"
        settings.prompt_disclosure_enabled = True
        settings.prompt_disclosure_force_legacy_builder = False
        settings.prompt_disclosure_max_full_sections_per_turn = 6
        settings.prompt_disclosure_default_ttl_turns = 8
        settings.prompt_disclosure_rollout_stage = stage
        settings.prompt_disclosure_debug = False
        mock_settings.return_value = settings

        pa = PersonalAssistant(
            session_id="s1",
            memory=MagicMock(),
            model_config={
                "provider": "compatible",
                "model_name": "test",
                "api_key": "k",
                "base_url": "http://localhost",
            },
            db=None,
        )
    return pa


def _decisions() -> dict[str, SectionDecision]:
    return {
        "identity": SectionDecision(mode=DisclosureMode.FULL, reason="always_on"),
        "memory": SectionDecision(mode=DisclosureMode.REMINDER, reason="ttl"),
        "user_profile": SectionDecision(mode=DisclosureMode.SUMMARY, reason="default"),
        "installed_skills": SectionDecision(mode=DisclosureMode.REMINDER, reason="ttl"),
    }


def test_stage0_shadow_forces_full():
    pa = _make_pa(stage=0)
    out = pa._apply_rollout_stage(_decisions())
    assert all(d.mode == DisclosureMode.FULL for d in out.values())


def test_stage1_only_low_risk_progressive():
    pa = _make_pa(stage=1)
    out = pa._apply_rollout_stage(_decisions())
    assert out["installed_skills"].mode == DisclosureMode.REMINDER
    assert out["memory"].mode == DisclosureMode.FULL
    assert out["user_profile"].mode == DisclosureMode.FULL


def test_stage3_full_progressive_kept():
    pa = _make_pa(stage=3)
    src = _decisions()
    out = pa._apply_rollout_stage(src)
    assert out["memory"].mode == src["memory"].mode
    assert out["user_profile"].mode == src["user_profile"].mode
