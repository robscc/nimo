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
            if skill_root is None:
                raise ValueError("ZIP 包中未找到 skill.json，请检查技能包结构")

            # 读取元数据
            meta = SkillLoader.load_skill_meta(skill_root)
            skill_name = meta.get("name", "")
            if not skill_name:
                raise ValueError("skill.json 中缺少 name 字段")

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

        return {
            "name": skill_name,
            "version": meta.get("version", "0.0.0"),
            "description": meta.get("description", ""),
            "tools": [t["name"] for t in tool_funcs],
            "install_path": str(install_dir),
        }

    async def install_from_url(self, url: str) -> dict[str, Any]:
        """从 URL 下载并安装技能包。

        支持：
        - 直接 ZIP URL
        - clawhub.ai 技能链接
        - skills.sh 技能链接
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

            return await self.install_from_zip(tmp_zip, source=source, source_url=url)
        finally:
            Path(tmp_zip).unlink(missing_ok=True)

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

        meta = SkillLoader.load_skill_meta(install_path)
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
                meta = SkillLoader.load_skill_meta(install_path)
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
        # clawhub.ai: 如果是详情页链接，转为 ZIP 下载链接
        if source == "clawhub" and "/download" not in url:
            # clawhub.ai/skills/<name> → clawhub.ai/skills/<name>/download
            return url.rstrip("/") + "/download"

        # skills.sh: 如果是详情页链接，转为 ZIP 下载链接
        if source == "skills.sh" and "/download" not in url:
            return url.rstrip("/") + "/download"

        return url
