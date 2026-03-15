"""SkillVersionManager — 技能版本快照管理。

版本目录结构：
    <skills_dir>/
      .nimo_versions/          ← 所有技能的历史版本根目录
        <skill_name>/
          0/                   ← 最近一次备份（安装前的版本）
            <skill files>
            .meta.json         ← {"version": "1.0.0", "backed_up_at": "..."}
          1/                   ← 次新备份
          2/                   ← 最旧备份

索引规则：
- 0 = 最近备份（上一个版本）
- 1、2 = 更旧版本
- 最多保留 MAX_VERSIONS = 3 个历史版本
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

MAX_VERSIONS = 3


class SkillVersionManager:
    """技能历史版本快照管理器。"""

    def __init__(self, skills_dir: Path) -> None:
        self._versions_root = skills_dir / ".nimo_versions"
        self._versions_root.mkdir(parents=True, exist_ok=True)

    # ── 备份 ───────────────────────────────────────────────

    def backup_version(
        self,
        skill_name: str,
        current_dir: Path,
        current_version: str,
    ) -> None:
        """在安装新版本前备份当前版本。

        Args:
            skill_name:      技能名称
            current_dir:     当前安装目录
            current_version: 当前版本号（来自 skill.json / SKILL.md）
        """
        if not current_dir.exists():
            logger.debug(f"Skill [{skill_name}] 尚无已安装版本，跳过备份")
            return

        skill_versions_dir = self._versions_root / skill_name
        skill_versions_dir.mkdir(parents=True, exist_ok=True)

        # 获取现有备份列表（按索引排序）
        existing = self._get_existing_indexes(skill_versions_dir)

        # 淘汰最旧备份，保证总数不超过 MAX_VERSIONS - 1（腾出一个位给新备份）
        while len(existing) >= MAX_VERSIONS:
            oldest_idx = max(existing)
            shutil.rmtree(skill_versions_dir / str(oldest_idx), ignore_errors=True)
            existing.remove(oldest_idx)
            logger.debug(f"Skill [{skill_name}] 删除最旧版本 v{oldest_idx}")

        # 将现有备份依次后移：n → n+1（逆序避免冲突）
        for idx in sorted(existing, reverse=True):
            src = skill_versions_dir / str(idx)
            dst = skill_versions_dir / str(idx + 1)
            src.rename(dst)

        # 将当前安装目录复制到 0（最新备份）
        dest = skill_versions_dir / "0"
        shutil.copytree(
            str(current_dir),
            str(dest),
            ignore=shutil.ignore_patterns(".nimo_versions*"),
        )

        # 写入版本元数据
        meta = {
            "version": current_version,
            "backed_up_at": datetime.now(timezone.utc).isoformat(),
        }
        (dest / ".meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        logger.info(
            f"Skill [{skill_name}] 版本 {current_version} 已备份至 {dest}"
        )

    # ── 查询 ───────────────────────────────────────────────

    def list_versions(self, skill_name: str) -> list[dict[str, Any]]:
        """列出指定技能的所有历史版本（0=最近）。

        Returns:
            [{"index": 0, "version": "1.0.0", "backed_up_at": "..."}, ...]
        """
        skill_versions_dir = self._versions_root / skill_name
        if not skill_versions_dir.exists():
            return []

        versions: list[dict[str, Any]] = []
        for idx in sorted(self._get_existing_indexes(skill_versions_dir)):
            version_dir = skill_versions_dir / str(idx)
            meta = self._read_meta(version_dir)
            versions.append(
                {
                    "index": idx,
                    "version": meta.get("version", "unknown"),
                    "backed_up_at": meta.get("backed_up_at"),
                }
            )
        return versions

    def get_version_dir(self, skill_name: str, index: int) -> Path | None:
        """获取指定历史版本的目录路径。

        Returns:
            Path 对象（如果版本存在），否则 None
        """
        d = self._versions_root / skill_name / str(index)
        return d if d.exists() else None

    # ── 恢复 ───────────────────────────────────────────────

    def restore_version(
        self,
        skill_name: str,
        index: int,
        install_dir: Path,
    ) -> dict[str, Any] | None:
        """恢复指定历史版本到安装目录。

        保留历史版本快照不变（回滚后历史仍可继续查看）。

        Args:
            skill_name:   技能名称
            index:        版本索引（0=最近）
            install_dir:  当前安装目录（将被替换）

        Returns:
            恢复版本的元数据 dict，如果版本不存在则返回 None
        """
        version_dir = self.get_version_dir(skill_name, index)
        if version_dir is None:
            return None

        meta = self._read_meta(version_dir)

        # 删除当前安装目录，从历史版本复制
        if install_dir.exists():
            shutil.rmtree(install_dir)
        shutil.copytree(
            str(version_dir),
            str(install_dir),
            ignore=shutil.ignore_patterns(".meta.json"),
        )

        logger.info(
            f"Skill [{skill_name}] 已回滚到版本 {meta.get('version', '?')}（索引 {index}）"
        )
        return meta

    # ── 清理 ───────────────────────────────────────────────

    def delete_all_versions(self, skill_name: str) -> None:
        """删除指定技能的所有历史版本（卸载时调用）。"""
        skill_versions_dir = self._versions_root / skill_name
        if skill_versions_dir.exists():
            shutil.rmtree(skill_versions_dir, ignore_errors=True)
            logger.info(f"Skill [{skill_name}] 所有历史版本已清除")

    # ── 内部辅助 ───────────────────────────────────────────

    @staticmethod
    def _get_existing_indexes(skill_versions_dir: Path) -> list[int]:
        """返回已存在的备份索引列表。"""
        indexes = []
        for d in skill_versions_dir.iterdir():
            if d.is_dir() and d.name.isdigit():
                indexes.append(int(d.name))
        return indexes

    @staticmethod
    def _read_meta(version_dir: Path) -> dict[str, Any]:
        """读取版本元数据文件（.meta.json）。"""
        meta_file = version_dir / ".meta.json"
        if meta_file.exists():
            try:
                return json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}
