"""Skill 管理单元测试（URL 解析、目录查找等）。"""

from __future__ import annotations

import json
import zipfile
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.skills.manager import SkillManager


class TestSkillURLNormalization:
    """测试 URL 标准化逻辑。"""

    def test_clawhub_url_adds_download(self):
        url = "https://clawhub.ai/skills/my-skill"
        result = SkillManager._normalize_download_url(url, "clawhub")
        assert result == "https://clawhub.ai/skills/my-skill/download"

    def test_clawhub_url_with_download_unchanged(self):
        url = "https://clawhub.ai/skills/my-skill/download"
        result = SkillManager._normalize_download_url(url, "clawhub")
        assert result == url

    def test_skills_sh_url_to_github_zip(self):
        url = "https://skills.sh/vercel-labs/skills/find-skills"
        result = SkillManager._normalize_download_url(url, "skills.sh")
        assert result == "https://github.com/vercel-labs/skills/archive/refs/heads/main.zip"

    def test_skills_sh_url_with_trailing_slash(self):
        url = "https://skills.sh/vercel-labs/skills/find-skills/"
        result = SkillManager._normalize_download_url(url, "skills.sh")
        assert result == "https://github.com/vercel-labs/skills/archive/refs/heads/main.zip"

    def test_plain_url_unchanged(self):
        url = "https://example.com/skill.zip"
        result = SkillManager._normalize_download_url(url, "url")
        assert result == url


class TestSkillFindRoot:
    """测试 _find_skill_root 目录查找逻辑。"""

    def test_find_skill_root_direct(self, tmp_path: Path):
        """skill.json 在根目录。"""
        (tmp_path / "skill.json").write_text("{}")
        (tmp_path / "__init__.py").write_text("")
        result = SkillManager._find_skill_root(tmp_path)
        assert result == tmp_path

    def test_find_skill_root_subdir(self, tmp_path: Path):
        """skill.json 在子目录。"""
        sub = tmp_path / "my-skill"
        sub.mkdir()
        (sub / "skill.json").write_text("{}")
        (sub / "__init__.py").write_text("")
        result = SkillManager._find_skill_root(tmp_path)
        assert result == sub

    def test_find_skill_root_skips_macosx(self, tmp_path: Path):
        """跳过 __MACOSX 目录。"""
        macosx = tmp_path / "__MACOSX"
        macosx.mkdir()
        (macosx / "skill.json").write_text("{}")
        result = SkillManager._find_skill_root(tmp_path)
        assert result is None

    def test_find_skill_root_not_found(self, tmp_path: Path):
        """无 skill.json 时返回 None。"""
        (tmp_path / "README.md").write_text("hello")
        result = SkillManager._find_skill_root(tmp_path)
        assert result is None


class TestSkillInstallFromZip:
    """测试从 ZIP 安装技能包。"""

    @pytest_asyncio.fixture
    async def skill_mgr(self, db_session: AsyncSession, tmp_path: Path) -> SkillManager:
        """创建 SkillManager 实例。"""
        import os
        os.environ["SKILLS_DIR"] = str(tmp_path / "skills")
        from unittest.mock import patch
        with patch("agentpal.skills.manager.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(skills_dir=str(tmp_path / "skills"))
            mgr = SkillManager(db_session)
        mgr._skills_dir = tmp_path / "skills"
        mgr._skills_dir.mkdir(parents=True, exist_ok=True)
        return mgr

    def _create_skill_zip(self, tmp_path: Path, skill_name: str = "test-skill") -> Path:
        """创建一个测试用的 skill ZIP 包。"""
        skill_dir = tmp_path / "skill_src" / skill_name
        skill_dir.mkdir(parents=True)
        meta = {
            "name": skill_name,
            "version": "1.0.0",
            "description": "A test skill",
            "author": "test",
            "tools": [
                {"name": "hello_world", "function": "hello_world", "description": "Says hello"}
            ],
        }
        (skill_dir / "skill.json").write_text(json.dumps(meta))
        (skill_dir / "__init__.py").write_text(
            "def hello_world(name: str = 'World') -> str:\n"
            "    return f'Hello, {name}!'\n"
        )

        zip_path = tmp_path / f"{skill_name}.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            for f in skill_dir.rglob("*"):
                zf.write(f, f.relative_to(skill_dir.parent))
        return zip_path

    @pytest.mark.asyncio
    async def test_install_from_zip_success(
        self, skill_mgr: SkillManager, tmp_path: Path
    ):
        """ZIP 安装成功。"""
        zip_path = self._create_skill_zip(tmp_path)
        result = await skill_mgr.install_from_zip(zip_path)
        assert result["name"] == "test-skill"
        assert result["version"] == "1.0.0"
        assert "hello_world" in result["tools"]

    @pytest.mark.asyncio
    async def test_install_from_zip_missing_skill_json(
        self, skill_mgr: SkillManager, tmp_path: Path
    ):
        """ZIP 中没有 skill.json 应报错。"""
        zip_path = tmp_path / "bad.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("README.md", "no skill here")
        with pytest.raises(ValueError, match="未找到 skill.json"):
            await skill_mgr.install_from_zip(zip_path)

    @pytest.mark.asyncio
    async def test_install_from_zip_missing_file(self, skill_mgr: SkillManager):
        """ZIP 文件不存在应报错。"""
        with pytest.raises(FileNotFoundError):
            await skill_mgr.install_from_zip("/nonexistent/path.zip")


class TestSkillSourceDetection:
    """测试 URL 来源自动检测。"""

    def test_detect_clawhub(self):
        url = "https://clawhub.ai/skills/some-skill"
        assert "clawhub" in url.lower()

    def test_detect_skills_sh(self):
        url = "https://skills.sh/vercel-labs/skills/find-skills"
        assert "skills.sh" in url


class TestSkillMdSupport:
    """测试 SKILL.md 格式支持（skills.sh 生态的标准格式）。"""

    @pytest_asyncio.fixture
    async def skill_mgr(self, db_session: AsyncSession, tmp_path: Path) -> SkillManager:
        """创建 SkillManager 实例。"""
        with patch("agentpal.skills.manager.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(skills_dir=str(tmp_path / "skills"))
            mgr = SkillManager(db_session)
        mgr._skills_dir = tmp_path / "skills"
        mgr._skills_dir.mkdir(parents=True, exist_ok=True)
        return mgr

    def test_find_skill_md_root_direct(self, tmp_path: Path):
        """SKILL.md 在根目录。"""
        (tmp_path / "SKILL.md").write_text("---\nname: test\n---\n# Test")
        result = SkillManager._find_skill_md_root(tmp_path)
        assert result == tmp_path

    def test_find_skill_md_root_subdir(self, tmp_path: Path):
        """SKILL.md 在子目录。"""
        sub = tmp_path / "my-skill"
        sub.mkdir()
        (sub / "SKILL.md").write_text("---\nname: test\n---\n# Test")
        result = SkillManager._find_skill_md_root(tmp_path)
        assert result == sub

    def test_find_skill_md_root_not_found(self, tmp_path: Path):
        """无 SKILL.md 时返回 None。"""
        (tmp_path / "README.md").write_text("hello")
        result = SkillManager._find_skill_md_root(tmp_path)
        assert result is None

    def test_parse_skill_md_with_frontmatter(self):
        """解析 SKILL.md 的 YAML frontmatter。"""
        content = "---\nname: find-skills\ndescription: Find and install skills\n---\n# Find Skills\n\nSome content."
        meta, body = SkillManager._parse_skill_md(content)
        assert meta["name"] == "find-skills"
        assert meta["description"] == "Find and install skills"
        assert "# Find Skills" in body
        assert "Some content." in body

    def test_parse_skill_md_without_frontmatter(self):
        """没有 frontmatter 的 SKILL.md。"""
        content = "# My Skill\n\nJust markdown."
        meta, body = SkillManager._parse_skill_md(content)
        assert meta == {}
        assert "# My Skill" in body

    def test_parse_skill_md_empty(self):
        """空 SKILL.md。"""
        meta, body = SkillManager._parse_skill_md("")
        assert meta == {}
        assert body == ""

    @pytest.mark.asyncio
    async def test_install_skill_md_from_zip(
        self, skill_mgr: SkillManager, tmp_path: Path
    ):
        """安装只含 SKILL.md 的技能（skills.sh 标准格式）。"""
        # 模拟 skills.sh 仓库结构: {repo}-main/skills/{skill-name}/SKILL.md
        repo_dir = tmp_path / "zip_content" / "skills-main" / "skills" / "find-skills"
        repo_dir.mkdir(parents=True)
        skill_md = "---\nname: find-skills\ndescription: Discover and install skills\n---\n# Find Skills\n\nThis skill helps discover skills."
        (repo_dir / "SKILL.md").write_text(skill_md)

        zip_path = tmp_path / "repo.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            for f in (tmp_path / "zip_content").rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(tmp_path / "zip_content"))

        result = await skill_mgr._install_from_skills_sh_zip(
            str(zip_path),
            "https://skills.sh/vercel-labs/skills/find-skills",
            "skills.sh",
        )
        assert result["name"] == "find-skills"
        assert result["description"] == "Discover and install skills"
        assert result["skill_type"] == "prompt"

    @pytest.mark.asyncio
    async def test_install_skill_md_creates_dir(
        self, skill_mgr: SkillManager, tmp_path: Path
    ):
        """SKILL.md 安装后应在 skills_dir 创建对应目录。"""
        repo_dir = tmp_path / "zip_content" / "skills-main" / "skills" / "my-prompt-skill"
        repo_dir.mkdir(parents=True)
        skill_md = "---\nname: my-prompt-skill\ndescription: A prompt skill\n---\n# My Prompt Skill\n\nContent here."
        (repo_dir / "SKILL.md").write_text(skill_md)

        zip_path = tmp_path / "repo.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            for f in (tmp_path / "zip_content").rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(tmp_path / "zip_content"))

        result = await skill_mgr._install_from_skills_sh_zip(
            str(zip_path),
            "https://skills.sh/org/repo/my-prompt-skill",
            "skills.sh",
        )
        install_dir = skill_mgr._skills_dir / "my-prompt-skill"
        assert install_dir.exists()
        assert (install_dir / "SKILL.md").exists()

    @pytest.mark.asyncio
    async def test_prompt_skill_content_stored_in_meta(
        self, skill_mgr: SkillManager, tmp_path: Path
    ):
        """SKILL.md 的 markdown 正文应存储在 meta.prompt_content 中。"""
        repo_dir = tmp_path / "zip_content" / "skills-main" / "skills" / "test-prompt"
        repo_dir.mkdir(parents=True)
        body = "# Test\n\nUse this skill when user asks about testing."
        skill_md = f"---\nname: test-prompt\ndescription: Test prompt skill\n---\n{body}"
        (repo_dir / "SKILL.md").write_text(skill_md)

        zip_path = tmp_path / "repo.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            for f in (tmp_path / "zip_content").rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(tmp_path / "zip_content"))

        result = await skill_mgr._install_from_skills_sh_zip(
            str(zip_path),
            "https://skills.sh/org/repo/test-prompt",
            "skills.sh",
        )
        # 验证 meta 中有 prompt_content
        skill = await skill_mgr.get_skill("test-prompt")
        assert skill is not None
        assert skill["meta"]["skill_type"] == "prompt"
        assert "# Test" in skill["meta"]["prompt_content"]

    @pytest.mark.asyncio
    async def test_get_prompt_skills(
        self, skill_mgr: SkillManager, tmp_path: Path
    ):
        """get_prompt_skills() 应返回所有已启用的 prompt 型技能的内容。"""
        repo_dir = tmp_path / "zip_content" / "skills-main" / "skills" / "prompt-skill-a"
        repo_dir.mkdir(parents=True)
        (repo_dir / "SKILL.md").write_text(
            "---\nname: prompt-skill-a\ndescription: Skill A\n---\n# Skill A\n\nDo A things."
        )

        zip_path = tmp_path / "repo.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            for f in (tmp_path / "zip_content").rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(tmp_path / "zip_content"))

        await skill_mgr._install_from_skills_sh_zip(
            str(zip_path),
            "https://skills.sh/org/repo/prompt-skill-a",
            "skills.sh",
        )
        prompts = await skill_mgr.get_prompt_skills()
        assert len(prompts) >= 1
        assert any(p["name"] == "prompt-skill-a" for p in prompts)
        assert any("# Skill A" in p["content"] for p in prompts)
