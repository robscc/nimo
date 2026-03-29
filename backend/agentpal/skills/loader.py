"""SkillLoader — 从已安装目录动态加载 Skill 的 Python 模块与工具函数。

支持两种技能格式：
1. Python 工具型（skill.json + __init__.py）
   - skill.json 元数据（name, version, tools[…]）
   - __init__.py 导出工具函数
2. Prompt 提示型（SKILL.md，skills.sh 生态标准格式）
   - YAML frontmatter 含 name, description
   - Markdown 正文作为提示/知识注入 system prompt

SkillLoader 负责：
1. 读取 skill.json 或 SKILL.md 中的元数据
2. Python 型：通过 importlib 动态导入 __init__.py，提取工具函数
3. Prompt 型：解析 SKILL.md，提供提示内容供 system prompt 注入
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from loguru import logger


class SkillLoader:
    """从磁盘加载单个 Skill 的工具函数或提示内容。"""

    @staticmethod
    def load_skill_meta(skill_dir: Path) -> dict[str, Any]:
        """读取并解析 skill.json。

        Returns:
            skill.json 的完整内容 dict
        Raises:
            FileNotFoundError: skill.json 不存在
            json.JSONDecodeError: JSON 格式错误
        """
        meta_path = skill_dir / "skill.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"skill.json 不存在: {meta_path}")
        return json.loads(meta_path.read_text(encoding="utf-8"))

    @staticmethod
    def load_skill_md_meta(skill_dir: Path) -> dict[str, Any]:
        """读取并解析 SKILL.md（skills.sh 格式）。

        SKILL.md 格式：
        ```
        ---
        name: skill-name
        description: A brief description
        ---
        # Skill Title

        Markdown content...
        ```

        Returns:
            dict 包含 name, description, skill_type="prompt", prompt_content
        Raises:
            FileNotFoundError: SKILL.md 不存在
        """
        md_path = skill_dir / "SKILL.md"
        if not md_path.exists():
            raise FileNotFoundError(f"SKILL.md 不存在: {md_path}")

        content = md_path.read_text(encoding="utf-8")
        frontmatter, body = SkillLoader._parse_frontmatter(content)

        name = frontmatter.get("name", skill_dir.name)
        description = frontmatter.get("description", "")
        version = frontmatter.get("version", "")

        # Fallback: read version from _meta.json (ClawhHub packages)
        if not version:
            version = SkillLoader._read_meta_json_version(skill_dir)

        return {
            "name": name,
            "description": description,
            "version": version or "0.0.0",
            "author": frontmatter.get("author", ""),
            "skill_type": "prompt",
            "prompt_content": body.strip(),
            "tools": [],  # prompt 型没有 Python 工具
        }

    @staticmethod
    def auto_load_meta(skill_dir: Path) -> dict[str, Any]:
        """自动检测并加载技能元数据（优先 skill.json，回退 SKILL.md）。

        Returns:
            技能元数据 dict
        Raises:
            FileNotFoundError: 既没有 skill.json 也没有 SKILL.md
        """
        if (skill_dir / "skill.json").exists():
            return SkillLoader.load_skill_meta(skill_dir)
        if (skill_dir / "SKILL.md").exists():
            return SkillLoader.load_skill_md_meta(skill_dir)
        raise FileNotFoundError(
            f"目录中既没有 skill.json 也没有 SKILL.md: {skill_dir}"
        )

    @staticmethod
    def _read_meta_json_version(skill_dir: Path) -> str:
        """Read version from _meta.json if it exists (ClawhHub packages).

        Returns:
            Version string, or empty string if not found.
        """
        meta_json = skill_dir / "_meta.json"
        if meta_json.exists():
            try:
                data = json.loads(meta_json.read_text(encoding="utf-8"))
                return data.get("version", "")
            except (json.JSONDecodeError, OSError):
                pass
        return ""

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
        """解析 YAML frontmatter。

        Returns:
            (frontmatter_dict, body_text)
        """
        if not content.strip():
            return {}, ""

        # 匹配 --- ... --- 块
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
        if not match:
            return {}, content

        fm_text = match.group(1)
        body = match.group(2)

        # 简单的 YAML 解析（避免依赖 PyYAML）：只支持 key: value 格式
        frontmatter: dict[str, str] = {}
        for line in fm_text.split("\n"):
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                key, _, value = line.partition(":")
                frontmatter[key.strip()] = value.strip()

        return frontmatter, body

    @staticmethod
    def load_tool_functions(skill_dir: Path, meta: dict[str, Any]) -> list[dict[str, Any]]:
        """动态导入 Skill 模块，提取工具函数。

        Args:
            skill_dir: 技能包解压后的目录
            meta: skill.json 解析后的 dict

        Returns:
            工具信息列表，每项包含 name, func, description
        """
        # prompt 型技能没有 Python 工具
        if meta.get("skill_type") == "prompt":
            logger.info(f"Skill [{meta.get('name')}] 是 prompt 型，无 Python 工具")
            return []

        init_path = skill_dir / "__init__.py"
        if not init_path.exists():
            logger.warning(f"Skill {meta.get('name', '?')}: __init__.py 不存在，跳过")
            return []

        module_name = f"agentpal_skill_{meta.get('name', 'unknown')}"

        # 如果之前已加载过同名模块，先移除（支持热重载）
        if module_name in sys.modules:
            del sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(module_name, str(init_path))
        if spec is None or spec.loader is None:
            logger.error(f"无法创建模块 spec: {init_path}")
            return []

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module

        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.error(f"加载 Skill 模块失败 ({init_path}): {exc}")
            del sys.modules[module_name]
            return []

        # 从 skill.json 的 tools 列表中获取声明的工具
        declared_tools: list[dict[str, Any]] = meta.get("tools", [])
        loaded: list[dict[str, Any]] = []

        for tool_def in declared_tools:
            func_name = tool_def.get("function", tool_def.get("name", ""))
            if not func_name:
                continue

            func: Callable[..., Any] | None = getattr(module, func_name, None)
            if func is None or not callable(func):
                logger.warning(
                    f"Skill {meta.get('name')}: 声明的工具函数 {func_name!r} 不存在或不可调用"
                )
                continue

            loaded.append({
                "name": tool_def.get("name", func_name),
                "func": func,
                "description": tool_def.get("description", func.__doc__ or ""),
                "skill_name": meta.get("name", ""),
            })

        logger.info(
            f"Skill [{meta.get('name')}] 加载了 {len(loaded)}/{len(declared_tools)} 个工具"
        )
        return loaded

    @staticmethod
    def unload_skill(skill_name: str) -> None:
        """卸载已加载的 Skill 模块。"""
        module_name = f"agentpal_skill_{skill_name}"
        if module_name in sys.modules:
            del sys.modules[module_name]
            logger.info(f"已卸载 Skill 模块: {module_name}")
