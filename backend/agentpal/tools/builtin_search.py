"""搜索工具 — Glob 文件匹配 + Grep 内容搜索。

为 SubAgent（特别是 coder 和 ops-engineer）提供高效的代码库搜索能力。
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from agentscope.tool import ToolResponse


# ═══════════════════════════════════════════════════════════════════════════
# Glob — 文件名模式匹配
# ═══════════════════════════════════════════════════════════════════════════


async def glob_files(
    pattern: str,
    path: str | None = None,
    limit: int = 100,
) -> ToolResponse:
    """按 glob 模式查找文件。

    Args:
        pattern: Glob 模式，如 "**/*.py", "src/**/*.ts", "*.json"
        path:    搜索目录（默认当前工作目录）
        limit:   最多返回文件数（默认 100，防止结果过多）

    Returns:
        ToolResponse 包含匹配的文件路径列表

    Examples:
        # 查找所有 Python 文件
        glob_files("**/*.py")

        # 查找 src 目录下的 TypeScript 文件
        glob_files("src/**/*.ts")

        # 查找根目录的配置文件
        glob_files("*.json", path="/app")
    """
    try:
        import time
        start = time.time()

        search_dir = Path(path) if path else Path.cwd()
        if not search_dir.exists():
            return ToolResponse(
                status=1,
                content=f"Directory does not exist: {path}",
            )
        if not search_dir.is_dir():
            return ToolResponse(
                status=1,
                content=f"Path is not a directory: {path}",
            )

        # 使用 pathlib.Path.glob() 进行匹配
        matches = list(search_dir.glob(pattern))

        # 按修改时间排序（最新的在前）
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        # 限制结果数量
        truncated = len(matches) > limit
        matches = matches[:limit]

        # 转换为相对路径（节省 token）
        cwd = Path.cwd()
        filenames = []
        for p in matches:
            try:
                rel = p.relative_to(cwd)
                filenames.append(str(rel))
            except ValueError:
                # 无法转换为相对路径，使用绝对路径
                filenames.append(str(p))

        duration_ms = int((time.time() - start) * 1000)

        result = {
            "filenames": filenames,
            "num_files": len(filenames),
            "duration_ms": duration_ms,
            "truncated": truncated,
        }

        if len(filenames) == 0:
            content = "No files found"
        else:
            content = "\n".join(filenames)
            if truncated:
                content += f"\n\n(Results truncated to {limit} files. Use a more specific pattern.)"

        return ToolResponse(
            status=0,
            content=content,
            metadata=result,
        )

    except Exception as e:
        return ToolResponse(
            status=1,
            content=f"Glob error: {e}",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Grep — 内容搜索（基于 ripgrep 或 grep）
# ═══════════════════════════════════════════════════════════════════════════


async def grep_search(
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    file_type: str | None = None,
    output_mode: str = "files_with_matches",
    case_insensitive: bool = False,
    context_before: int = 0,
    context_after: int = 0,
    context: int = 0,
    show_line_numbers: bool = True,
    limit: int = 250,
    multiline: bool = False,
) -> ToolResponse:
    r"""在文件内容中搜索匹配的文本（基于 ripgrep）。

    Args:
        pattern:           正则表达式模式
        path:              搜索路径（文件或目录，默认当前目录）
        glob:              文件名过滤（如 "*.py", "*.{ts,tsx}"）
        file_type:         文件类型过滤（如 "py", "js", "rust"）
        output_mode:       输出模式：
                           - "files_with_matches": 只显示文件名（默认）
                           - "content": 显示匹配的行
                           - "count": 显示每个文件的匹配数
        case_insensitive:  是否忽略大小写
        context_before:    显示匹配行之前的 N 行
        context_after:     显示匹配行之后的 N 行
        context:           显示匹配行前后各 N 行（优先级高于 before/after）
        show_line_numbers: 是否显示行号（仅 content 模式）
        limit:             最多返回结果数（默认 250）
        multiline:         是否启用多行匹配模式

    Returns:
        ToolResponse 包含搜索结果

    Examples:
        # 查找包含 "TODO" 的文件
        grep_search("TODO")

        # 查找函数定义（显示内容 + 上下文）
        grep_search(r"def \w+\(", output_mode="content", context=2)

        # 查找 Python 文件中的 import 语句
        grep_search("^import ", file_type="py", output_mode="content")

        # 统计每个文件的匹配数
        grep_search("error", output_mode="count")
    """
    try:
        # 检查 ripgrep 是否可用
        rg_available = _check_ripgrep()

        if not rg_available:
            return ToolResponse(
                status=1,
                content="ripgrep (rg) not found. Please install: https://github.com/BurntSushi/ripgrep",
            )

        search_path = path or "."

        # 构建 ripgrep 命令
        args = ["rg", "--hidden"]

        # 排除 VCS 目录
        for vcs_dir in [".git", ".svn", ".hg", ".bzr"]:
            args.extend(["--glob", f"!{vcs_dir}"])

        # 限制行长度（防止 base64/minified 内容）
        args.extend(["--max-columns", "500"])

        # 多行模式
        if multiline:
            args.extend(["-U", "--multiline-dotall"])

        # 大小写
        if case_insensitive:
            args.append("-i")

        # 输出模式
        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "count":
            args.append("-c")

        # 行号
        if show_line_numbers and output_mode == "content":
            args.append("-n")

        # 上下文
        if output_mode == "content":
            if context > 0:
                args.extend(["-C", str(context)])
            else:
                if context_before > 0:
                    args.extend(["-B", str(context_before)])
                if context_after > 0:
                    args.extend(["-A", str(context_after)])

        # 模式（如果以 - 开头，需要用 -e 标记）
        if pattern.startswith("-"):
            args.extend(["-e", pattern])
        else:
            args.append(pattern)

        # 文件类型
        if file_type:
            args.extend(["--type", file_type])

        # Glob 过滤
        if glob:
            for g in glob.split(","):
                g = g.strip()
                if g:
                    args.extend(["--glob", g])

        # 搜索路径
        args.append(search_path)

        # 执行 ripgrep
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        # ripgrep 返回码：0=有匹配, 1=无匹配, 2=错误
        if proc.returncode == 2:
            return ToolResponse(
                status=1,
                content=f"ripgrep error: {stderr.decode('utf-8', errors='replace')}",
            )

        output = stdout.decode("utf-8", errors="replace")
        lines = output.strip().split("\n") if output.strip() else []

        # 应用 limit
        truncated = len(lines) > limit
        lines = lines[:limit]

        # 转换为相对路径
        cwd = Path.cwd()
        final_lines = []
        for line in lines:
            # 尝试提取路径并转换为相对路径
            if output_mode in ("files_with_matches", "count"):
                # 整行是路径
                try:
                    p = Path(line.split(":")[0])
                    rel = p.relative_to(cwd)
                    if output_mode == "count":
                        # 保留计数部分
                        parts = line.split(":")
                        final_lines.append(f"{rel}:{':'.join(parts[1:])}")
                    else:
                        final_lines.append(str(rel))
                except (ValueError, IndexError):
                    final_lines.append(line)
            else:
                # content 模式：路径:行号:内容
                colon_idx = line.find(":")
                if colon_idx > 0:
                    try:
                        p = Path(line[:colon_idx])
                        rel = p.relative_to(cwd)
                        final_lines.append(f"{rel}{line[colon_idx:]}")
                    except ValueError:
                        final_lines.append(line)
                else:
                    final_lines.append(line)

        # 构建结果
        result_content = "\n".join(final_lines)

        if output_mode == "count":
            # 统计总匹配数
            total_matches = 0
            file_count = 0
            for line in final_lines:
                parts = line.split(":")
                if len(parts) >= 2:
                    try:
                        count = int(parts[-1])
                        total_matches += count
                        file_count += 1
                    except ValueError:
                        pass
            result_content += f"\n\nFound {total_matches} total occurrences across {file_count} files."

        if truncated:
            result_content += f"\n\n[Results truncated to {limit} lines. Use offset or more specific pattern.]"

        if not result_content.strip():
            result_content = "No matches found"

        metadata = {
            "mode": output_mode,
            "num_results": len(final_lines),
            "truncated": truncated,
        }

        return ToolResponse(
            status=0,
            content=result_content,
            metadata=metadata,
        )

    except FileNotFoundError:
        return ToolResponse(
            status=1,
            content=f"Path does not exist: {path}",
        )
    except Exception as e:
        return ToolResponse(
            status=1,
            content=f"Grep error: {e}",
        )


def _check_ripgrep() -> bool:
    """检查 ripgrep 是否可用。"""
    try:
        subprocess.run(
            ["rg", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
