"""SkillLoader — 从已安装目录动态加载 Skill 的 Python 模块与工具函数。

每个 Skill 包含：
- skill.json  ← 元数据（name, version, tools[…]）
- __init__.py  ← 导出工具函数（函数签名 → 自动生成 JSON Schema）

SkillLoader 负责：
1. 读取 skill.json 中声明的 tools
2. 通过 importlib 动态导入 __init__.py
3. 从模块中提取工具函数引用，供 Toolkit 注册
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable

from loguru import logger


class SkillLoader:
    """从磁盘加载单个 Skill 的工具函数。"""

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
    def load_tool_functions(skill_dir: Path, meta: dict[str, Any]) -> list[dict[str, Any]]:
        """动态导入 Skill 模块，提取工具函数。

        Args:
            skill_dir: 技能包解压后的目录
            meta: skill.json 解析后的 dict

        Returns:
            工具信息列表，每项包含 name, func, description
        """
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
