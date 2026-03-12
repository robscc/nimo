"""SkillLoader 单元测试 — 包含 SKILL.md 格式支持。"""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from agentpal.skills.loader import SkillLoader


class TestLoadSkillMeta:
    """测试 skill.json 加载。"""

    def test_load_valid_meta(self, tmp_path: Path):
        meta = {"name": "test", "version": "1.0.0", "tools": []}
        (tmp_path / "skill.json").write_text(json.dumps(meta))
        result = SkillLoader.load_skill_meta(tmp_path)
        assert result["name"] == "test"

    def test_load_meta_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            SkillLoader.load_skill_meta(tmp_path)


class TestLoadSkillMd:
    """测试 SKILL.md 元数据加载。"""

    def test_load_skill_md_meta(self, tmp_path: Path):
        """从 SKILL.md 加载元数据。"""
        skill_md = "---\nname: find-skills\ndescription: Find skills\n---\n# Content"
        (tmp_path / "SKILL.md").write_text(skill_md)
        result = SkillLoader.load_skill_md_meta(tmp_path)
        assert result["name"] == "find-skills"
        assert result["description"] == "Find skills"
        assert result["skill_type"] == "prompt"
        assert "# Content" in result["prompt_content"]

    def test_load_skill_md_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="SKILL.md"):
            SkillLoader.load_skill_md_meta(tmp_path)

    def test_load_skill_md_no_frontmatter(self, tmp_path: Path):
        """没有 frontmatter 时，使用目录名作为 name。"""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# My Skill\n\nContent here.")
        result = SkillLoader.load_skill_md_meta(skill_dir)
        assert result["name"] == "my-skill"
        assert result["skill_type"] == "prompt"

    def test_load_skill_md_prefers_skill_json(self, tmp_path: Path):
        """如果同时存在 skill.json 和 SKILL.md，load_skill_meta 优先用 skill.json。"""
        (tmp_path / "skill.json").write_text('{"name": "json-skill", "version": "1.0.0"}')
        (tmp_path / "SKILL.md").write_text("---\nname: md-skill\n---\n# Content")
        result = SkillLoader.load_skill_meta(tmp_path)
        assert result["name"] == "json-skill"


class TestSkillMetaAutoDetect:
    """测试自动检测 skill.json 或 SKILL.md。"""

    def test_auto_detect_json(self, tmp_path: Path):
        (tmp_path / "skill.json").write_text('{"name": "json-skill"}')
        result = SkillLoader.auto_load_meta(tmp_path)
        assert result["name"] == "json-skill"
        assert result.get("skill_type") != "prompt"

    def test_auto_detect_md(self, tmp_path: Path):
        (tmp_path / "SKILL.md").write_text("---\nname: md-skill\n---\n# Content")
        result = SkillLoader.auto_load_meta(tmp_path)
        assert result["name"] == "md-skill"
        assert result["skill_type"] == "prompt"

    def test_auto_detect_neither(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            SkillLoader.auto_load_meta(tmp_path)
