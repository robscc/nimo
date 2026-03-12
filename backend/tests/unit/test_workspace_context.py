"""ContextBuilder 单元测试 — 包含 skill prompt 注入。"""

from __future__ import annotations

from agentpal.workspace.context_builder import ContextBuilder, WorkspaceFiles


class TestContextBuilderSkillPrompts:
    """测试 ContextBuilder 对 skill prompt 的支持。"""

    def test_build_with_no_skill_prompts(self):
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am an assistant.")
        prompt = cb.build_system_prompt(ws)
        assert "Agent Identity" in prompt
        assert "Skill:" not in prompt

    def test_build_with_skill_prompts(self):
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am an assistant.")
        skill_prompts = [
            {"name": "find-skills", "content": "# Find Skills\n\nHelp users find skills."},
        ]
        prompt = cb.build_system_prompt(ws, skill_prompts=skill_prompts)
        assert "Installed Skills" in prompt
        assert "find-skills" in prompt
        assert "Help users find skills." in prompt

    def test_build_with_multiple_skill_prompts(self):
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am an assistant.")
        skill_prompts = [
            {"name": "skill-a", "content": "# A\n\nDo A."},
            {"name": "skill-b", "content": "# B\n\nDo B."},
        ]
        prompt = cb.build_system_prompt(ws, skill_prompts=skill_prompts)
        assert "skill-a" in prompt
        assert "skill-b" in prompt
        assert "Do A." in prompt
        assert "Do B." in prompt

    def test_skill_prompts_truncated_when_too_long(self):
        cb = ContextBuilder()
        ws = WorkspaceFiles()
        # Create a very long skill prompt
        long_content = "x" * 10000
        skill_prompts = [
            {"name": "long-skill", "content": long_content},
        ]
        prompt = cb.build_system_prompt(ws, skill_prompts=skill_prompts)
        # Should be truncated
        assert len(prompt) < len(long_content) + 1000
