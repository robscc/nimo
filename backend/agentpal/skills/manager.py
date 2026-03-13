"""SkillManager — Skill 生命周期管理（安装、卸载、启用、禁用）。

职责：
- install_from_zip:   从 ZIP 文件安装技能包
- install_from_url:   从 URL（含 clawhub.ai / skills.sh）下载并安装
- uninstall:          删除已安装技能
- enable / disable:   启用/禁用技能
- list_skills:        列出所有已安装技能
- get_skill_tools:    获取某个技能的工具函数列表（已启用的）
- get_all_skill_tools: 获取所有已启用技能的工具函数
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.config import get_settings
from agentpal.models.skill import SkillRecord
from agentpal.skills.loader import SkillLoader


class SkillManager:
    """Skill 生命周期管理器。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._skills_dir = Path(get_settings().skills_dir).resolve()
        self._skills_dir.mkdir(parents=True, exist_ok=True)

    # ── 安装 ──────────────────────────────────────────────

    async def install_from_zip(
        self,
        zip_path: str | Path,
        source: str = "local",
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """从 ZIP 文件安装技能包。

        ZIP 结构要求：
        - 根目录或一级子目录下必须包含 skill.json + __init__.py
        - 或 SKILL.md（skills.sh 格式的 prompt 型技能）

        Returns:
            安装结果 dict，包含 name, version, tools 等信息
        """
        zip_path = Path(zip_path)
        if not zip_path.exists():
            raise FileNotFoundError(f"ZIP 文件不存在: {zip_path}")

        # 解压到临时目录
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                zf.extractall(tmp_path)

            # 查找 skill.json 所在目录
            skill_root = self._find_skill_root(tmp_path)
            is_prompt_skill = False
            if skill_root is None:
                # 回退：查找 SKILL.md
                skill_root = self._find_skill_md_root(tmp_path)
                if skill_root is not None:
                    is_prompt_skill = True
            if skill_root is None:
                raise ValueError("ZIP 包中未找到 skill.json 或 SKILL.md，请检查技能包结构")

            # 读取元数据
            if is_prompt_skill:
                meta = SkillLoader.load_skill_md_meta(skill_root)
            else:
                meta = SkillLoader.load_skill_meta(skill_root)

            skill_name = meta.get("name", "")
            if not skill_name:
                raise ValueError("技能元数据中缺少 name 字段")

            # 目标安装目录
            install_dir = self._skills_dir / skill_name
            if install_dir.exists():
                # 已安装：先卸载旧版本
                shutil.rmtree(install_dir)
                logger.info(f"已移除旧版 Skill: {skill_name}")

            # 复制到安装目录
            shutil.copytree(str(skill_root), str(install_dir))
            logger.info(f"Skill [{skill_name}] 已安装到 {install_dir}")

        # 验证加载
        tool_funcs = SkillLoader.load_tool_functions(install_dir, meta)

        # 写入数据库
        await self._upsert_record(
            name=skill_name,
            version=meta.get("version", "0.0.0"),
            description=meta.get("description", ""),
            author=meta.get("author", ""),
            source=source,
            source_url=source_url,
            install_path=str(install_dir),
            meta=meta,
        )

        result: dict[str, Any] = {
            "name": skill_name,
            "version": meta.get("version", "0.0.0"),
            "description": meta.get("description", ""),
            "tools": [t["name"] for t in tool_funcs],
            "install_path": str(install_dir),
        }
        if is_prompt_skill:
            result["skill_type"] = "prompt"
        return result

    async def install_from_url(self, url: str) -> dict[str, Any]:
        """从 URL 下载并安装技能包。

        支持：
        - 直接 ZIP URL
        - clawhub.ai 技能链接
        - skills.sh 技能链接（如 https://skills.sh/vercel-labs/skills/find-skills）

        skills.sh 的 URL 格式为 https://skills.sh/{org}/{repo}/{skill-name}
        对应 GitHub: https://github.com/{org}/{repo}/tree/main/skills/{skill-name}
        下载 ZIP: https://github.com/{org}/{repo}/archive/refs/heads/main.zip
        """
        import httpx

        # 解析来源
        source = "url"
        if "clawhub.ai" in url or "clawhub" in url:
            source = "clawhub"
        elif "skills.sh" in url:
            source = "skills.sh"

        # 标准化 URL（marketplace 适配）
        download_url = self._normalize_download_url(url, source)

        # 下载 ZIP
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_file:
            tmp_zip = tmp_file.name

        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(download_url)
                resp.raise_for_status()
                Path(tmp_zip).write_bytes(resp.content)

            # skills.sh 特殊处理：ZIP 是整个仓库，需提取特定 skill 子目录
            if source == "skills.sh":
                return await self._install_from_skills_sh_zip(
                    tmp_zip, url, source
                )

            return await self.install_from_zip(tmp_zip, source=source, source_url=url)
        finally:
            Path(tmp_zip).unlink(missing_ok=True)

    async def _install_from_skills_sh_zip(
        self, zip_path: str, original_url: str, source: str
    ) -> dict[str, Any]:
        """处理 skills.sh 下载的仓库 ZIP，提取特定 skill 子目录。

        skills.sh URL: https://skills.sh/{org}/{repo}/{skill-name}
        仓库 ZIP 结构:
        - Python 型: {repo}-main/skills/{skill-name}/skill.json
        - Prompt 型: {repo}-main/skills/{skill-name}/SKILL.md
        """
        import re

        # 从 URL 提取 skill-name
        match = re.match(r"https?://skills\.sh/([^/]+)/([^/]+)/([^/]+)/?", original_url)
        if not match:
            # 回退：尝试当作普通 ZIP 安装
            return await self.install_from_zip(zip_path, source=source, source_url=original_url)

        org, repo, skill_name = match.groups()
        skill_name = skill_name.strip("/")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp_path)

            # 查找 skill 子目录（可能在 {repo}-main/skills/{skill-name}/ 下）
            skill_root = None
            has_skill_json = False

            # 模式 1: {repo}-main/skills/{skill-name}/
            for sub in tmp_path.iterdir():
                if sub.is_dir():
                    candidate = sub / "skills" / skill_name
                    if candidate.exists():
                        if (candidate / "skill.json").exists():
                            skill_root = candidate
                            has_skill_json = True
                            break
                        if (candidate / "SKILL.md").exists():
                            skill_root = candidate
                            break
                    # 模式 2: {repo}-main/{skill-name}/
                    candidate2 = sub / skill_name
                    if candidate2.exists():
                        if (candidate2 / "skill.json").exists():
                            skill_root = candidate2
                            has_skill_json = True
                            break
                        if (candidate2 / "SKILL.md").exists():
                            skill_root = candidate2
                            break

            # 模式 3: 根目录直接查找（先找 skill.json，再找 SKILL.md）
            if skill_root is None:
                skill_root = self._find_skill_root(tmp_path)
                if skill_root is not None:
                    has_skill_json = True
                else:
                    skill_root = self._find_skill_md_root(tmp_path)

            if skill_root is None:
                raise ValueError(
                    f"在仓库中未找到技能 '{skill_name}'，"
                    f"请确认 URL 正确: {original_url}"
                )

            # 加载元数据
            if has_skill_json:
                meta = SkillLoader.load_skill_meta(skill_root)
            else:
                meta = SkillLoader.load_skill_md_meta(skill_root)

            actual_name = meta.get("name", skill_name)
            install_dir = self._skills_dir / actual_name
            if install_dir.exists():
                shutil.rmtree(install_dir)
            shutil.copytree(str(skill_root), str(install_dir))
            logger.info(f"Skill [{actual_name}] 从 skills.sh 安装到 {install_dir}")

        # 重新加载验证
        meta = SkillLoader.auto_load_meta(install_dir)
        is_prompt_skill = meta.get("skill_type") == "prompt"
        tool_funcs = SkillLoader.load_tool_functions(install_dir, meta)

        # 写入数据库
        await self._upsert_record(
            name=actual_name,
            version=meta.get("version", "0.0.0"),
            description=meta.get("description", ""),
            author=meta.get("author", ""),
            source=source,
            source_url=original_url,
            install_path=str(install_dir),
            meta=meta,
        )

        result: dict[str, Any] = {
            "name": actual_name,
            "version": meta.get("version", "0.0.0"),
            "description": meta.get("description", ""),
            "tools": [t["name"] for t in tool_funcs],
            "install_path": str(install_dir),
        }
        if is_prompt_skill:
            result["skill_type"] = "prompt"
        return result

    # ── 卸载 ──────────────────────────────────────────────

    async def uninstall(self, name: str) -> bool:
        """卸载已安装的技能。

        Returns:
            True 表示成功卸载，False 表示技能不存在
        """
        record = await self._db.get(SkillRecord, name)
        if record is None:
            return False

        # 卸载模块
        SkillLoader.unload_skill(name)

        # 删除文件
        install_path = Path(record.install_path)
        if install_path.exists():
            shutil.rmtree(install_path)
            logger.info(f"已删除 Skill 文件: {install_path}")

        # 删除数据库记录
        await self._db.delete(record)
        await self._db.flush()
        logger.info(f"已卸载 Skill: {name}")
        return True

    # ── 启用 / 禁用 ──────────────────────────────────────

    async def enable(self, name: str) -> bool:
        """启用技能。"""
        return await self._set_enabled(name, True)

    async def disable(self, name: str) -> bool:
        """禁用技能。"""
        return await self._set_enabled(name, False)

    async def _set_enabled(self, name: str, enabled: bool) -> bool:
        record = await self._db.get(SkillRecord, name)
        if record is None:
            return False
        record.enabled = enabled
        await self._db.flush()
        logger.info(f"Skill [{name}] {'启用' if enabled else '禁用'}")
        return True

    # ── 查询 ──────────────────────────────────────────────

    async def list_skills(self) -> list[dict[str, Any]]:
        """列出所有已安装的技能。"""
        result = await self._db.execute(
            select(SkillRecord).order_by(SkillRecord.created_at.desc())
        )
        records = result.scalars().all()
        return [
            {
                "name": r.name,
                "version": r.version,
                "description": r.description,
                "author": r.author,
                "source": r.source,
                "source_url": r.source_url,
                "enabled": r.enabled,
                "install_path": r.install_path,
                "tools": [t.get("name", "") for t in (r.meta or {}).get("tools", [])],
                "skill_type": (r.meta or {}).get("skill_type", "python"),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in records
        ]

    async def get_skill(self, name: str) -> dict[str, Any] | None:
        """获取单个技能详情。"""
        record = await self._db.get(SkillRecord, name)
        if record is None:
            return None
        return {
            "name": record.name,
            "version": record.version,
            "description": record.description,
            "author": record.author,
            "source": record.source,
            "source_url": record.source_url,
            "enabled": record.enabled,
            "install_path": record.install_path,
            "tools": [t.get("name", "") for t in (record.meta or {}).get("tools", [])],
            "meta": record.meta,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    # ── 工具加载 ──────────────────────────────────────────

    async def get_skill_tools(self, name: str) -> list[dict[str, Any]]:
        """获取指定技能的工具函数列表（仅已启用的技能）。"""
        record = await self._db.get(SkillRecord, name)
        if record is None or not record.enabled:
            return []

        install_path = Path(record.install_path)
        if not install_path.exists():
            logger.warning(f"Skill [{name}] 安装目录不存在: {install_path}")
            return []

        meta = SkillLoader.auto_load_meta(install_path)
        return SkillLoader.load_tool_functions(install_path, meta)

    async def get_all_skill_tools(self) -> list[dict[str, Any]]:
        """获取所有已启用技能的工具函数列表。"""
        result = await self._db.execute(
            select(SkillRecord).where(SkillRecord.enabled == True)  # noqa: E712
        )
        records = result.scalars().all()

        all_tools: list[dict[str, Any]] = []
        for record in records:
            install_path = Path(record.install_path)
            if not install_path.exists():
                continue
            try:
                meta = SkillLoader.auto_load_meta(install_path)
                tools = SkillLoader.load_tool_functions(install_path, meta)
                all_tools.extend(tools)
            except Exception as exc:
                logger.error(f"加载 Skill [{record.name}] 工具失败: {exc}")

        return all_tools

    # ── 内部辅助 ──────────────────────────────────────────

    async def _upsert_record(
        self,
        name: str,
        version: str,
        description: str,
        author: str,
        source: str,
        source_url: str | None,
        install_path: str,
        meta: dict[str, Any],
    ) -> None:
        """新增或更新 SkillRecord。"""
        existing = await self._db.get(SkillRecord, name)
        if existing:
            existing.version = version
            existing.description = description
            existing.author = author
            existing.source = source
            existing.source_url = source_url
            existing.install_path = install_path
            existing.meta = meta
        else:
            record = SkillRecord(
                name=name,
                version=version,
                description=description,
                author=author,
                source=source,
                source_url=source_url,
                enabled=True,
                install_path=install_path,
                meta=meta,
            )
            self._db.add(record)
        await self._db.flush()

    # ── Prompt 型技能（SKILL.md）────────────────────────────

    async def get_prompt_skills(
        self,
        session_skill_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """获取所有已启用的 prompt 型技能内容。

        Args:
            session_skill_names: session 级启用的技能名列表（可选），
                                 None 表示使用全局配置

        Returns:
            [{"name": ..., "description": ..., "content": ...}, ...]
        """
        result = await self._db.execute(
            select(SkillRecord).where(SkillRecord.enabled == True)  # noqa: E712
        )
        records = result.scalars().all()

        prompts: list[dict[str, Any]] = []
        for record in records:
            meta = record.meta or {}
            if meta.get("skill_type") != "prompt":
                continue

            # session 级过滤
            if session_skill_names is not None and record.name not in session_skill_names:
                continue

            content = meta.get("prompt_content", "")
            if not content:
                # 尝试从磁盘读取 SKILL.md
                install_path = Path(record.install_path)
                skill_md = install_path / "SKILL.md"
                if skill_md.exists():
                    _, content = self._parse_skill_md(
                        skill_md.read_text(encoding="utf-8")
                    )

            if content:
                prompts.append({
                    "name": record.name,
                    "description": record.description or "",
                    "content": content,
                })

        return prompts

    @staticmethod
    def _parse_skill_md(content: str) -> tuple[dict[str, str], str]:
        """解析 SKILL.md 的 YAML frontmatter。

        Returns:
            (frontmatter_dict, body_text)
        """
        return SkillLoader._parse_frontmatter(content)

    @staticmethod
    def _find_skill_md_root(extracted_dir: Path) -> Path | None:
        """在解压目录中查找 SKILL.md 所在目录。

        支持两种结构：
        1. 根目录直接包含 SKILL.md
        2. 一级子目录包含 SKILL.md（GitHub archive 风格）
        """
        if (extracted_dir / "SKILL.md").exists():
            return extracted_dir

        for sub in extracted_dir.iterdir():
            if sub.is_dir() and not sub.name.startswith((".", "__")):
                if (sub / "SKILL.md").exists():
                    return sub

        return None

    @staticmethod
    def _find_skill_root(extracted_dir: Path) -> Path | None:
        """在解压目录中查找 skill.json 所在目录。

        支持两种结构：
        1. 根目录直接包含 skill.json
        2. 一级子目录包含 skill.json（GitHub archive 风格）
        """
        if (extracted_dir / "skill.json").exists():
            return extracted_dir

        # 跳过 __MACOSX 等系统目录
        for sub in extracted_dir.iterdir():
            if sub.is_dir() and not sub.name.startswith((".", "__")):
                if (sub / "skill.json").exists():
                    return sub

        return None

    @staticmethod
    def _normalize_download_url(url: str, source: str) -> str:
        """将市场链接转换为可下载的 ZIP URL。"""
        import re

        # clawhub.ai: 如果是详情页链接，转为 ZIP 下载链接
        if source == "clawhub" and "/download" not in url:
            # clawhub.ai/skills/<name> → clawhub.ai/skills/<name>/download
            return url.rstrip("/") + "/download"

        # skills.sh: 转为 GitHub 仓库 ZIP 下载链接
        if source == "skills.sh":
            match = re.match(r"https?://skills\.sh/([^/]+)/([^/]+)/?", url)
            if match:
                org, repo = match.group(1), match.group(2)
                return f"https://github.com/{org}/{repo}/archive/refs/heads/main.zip"
            # 回退
            if "/download" not in url:
                return url.rstrip("/") + "/download"

        return url
