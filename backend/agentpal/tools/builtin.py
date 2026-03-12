"""内置工具定义 — 基于 agentscope.tool 实现，遵循 ToolResponse 协议。

每个工具：
- 接受 Python 原生类型参数
- 返回 agentscope.tool.ToolResponse（内含 TextBlock）
- 函数签名即文档，agentscope 自动生成 JSON Schema 供 LLM 调用
"""

from __future__ import annotations

import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

# ── 辅助函数 ──────────────────────────────────────────────


def _text_response(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


# ── 1. execute_shell_command ──────────────────────────────


def execute_shell_command(command: str, timeout: int = 30) -> ToolResponse:
    """执行 Shell 命令并返回输出结果。

    Args:
        command: 要执行的 shell 命令
        timeout: 超时秒数（默认 30 秒）

    Returns:
        包含 returncode、stdout、stderr 的执行结果
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (
            f"<returncode>{result.returncode}</returncode>\n"
            f"<stdout>{result.stdout.strip()}</stdout>\n"
            f"<stderr>{result.stderr.strip()}</stderr>"
        )
        return _text_response(output)
    except subprocess.TimeoutExpired:
        return _text_response(f"<error>命令超时（{timeout}秒）</error>")
    except Exception as e:
        return _text_response(f"<error>{e}</error>")


# ── 2. read_file ──────────────────────────────────────────


def read_file(file_path: str, start_line: int = 1, end_line: int | None = None) -> ToolResponse:
    """读取文件内容。

    Args:
        file_path: 文件路径（绝对路径或相对路径）
        start_line: 起始行号（从 1 开始，默认 1）
        end_line: 结束行号（默认读到文件末尾）

    Returns:
        文件内容文本
    """
    try:
        path = Path(file_path).expanduser()
        if not path.exists():
            return _text_response(f"<error>文件不存在: {file_path}</error>")
        if path.stat().st_size > 1024 * 1024:  # 1MB 限制
            return _text_response("<error>文件过大（超过 1MB），请指定行范围</error>")

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = lines[start_line - 1 : end_line]
        numbered = "\n".join(f"{start_line + i:4d}│ {line}" for i, line in enumerate(selected))
        return _text_response(f"# {file_path}\n```\n{numbered}\n```")
    except Exception as e:
        return _text_response(f"<error>{e}</error>")


# ── 3. write_file ─────────────────────────────────────────


def write_file(file_path: str, content: str) -> ToolResponse:
    """将内容写入文件（覆盖模式）。

    Args:
        file_path: 目标文件路径
        content: 要写入的文本内容

    Returns:
        操作结果
    """
    try:
        path = Path(file_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return _text_response(f"✅ 已写入 {path}（{len(content)} 字符）")
    except Exception as e:
        return _text_response(f"<error>{e}</error>")


# ── 4. edit_file ──────────────────────────────────────────


def edit_file(file_path: str, old_text: str, new_text: str) -> ToolResponse:
    """精确替换文件中的指定文本片段。

    Args:
        file_path: 目标文件路径
        old_text: 要替换的原始文本（必须在文件中唯一存在）
        new_text: 替换后的新文本

    Returns:
        操作结果
    """
    try:
        path = Path(file_path).expanduser()
        if not path.exists():
            return _text_response(f"<error>文件不存在: {file_path}</error>")

        original = path.read_text(encoding="utf-8")
        count = original.count(old_text)
        if count == 0:
            return _text_response("<error>未找到指定文本，请检查 old_text 是否准确</error>")
        if count > 1:
            return _text_response(f"<error>找到 {count} 处匹配，old_text 必须唯一，请提供更多上下文</error>")

        updated = original.replace(old_text, new_text, 1)
        path.write_text(updated, encoding="utf-8")
        return _text_response(f"✅ 已完成替换（{file_path}）")
    except Exception as e:
        return _text_response(f"<error>{e}</error>")


# ── 5. browser_use ────────────────────────────────────────


def browser_use(url: str, action: str = "get_text") -> ToolResponse:
    """访问网页并提取内容。

    Args:
        url: 要访问的网页 URL
        action: 操作类型，支持 "get_text"（提取文本）/ "get_title"（获取标题）

    Returns:
        网页内容或标题
    """
    try:
        import httpx

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AgentPal/0.1"
        }
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()

        if action == "get_title":
            import re
            match = re.search(r"<title>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
            title = match.group(1).strip() if match else "（无标题）"
            return _text_response(f"页面标题: {title}")

        # get_text: 简单去除 HTML 标签
        import re
        text = re.sub(r"<script[^>]*>.*?</script>", "", resp.text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s{3,}", "\n\n", text).strip()
        # 限制长度
        if len(text) > 3000:
            text = text[:3000] + "\n\n[...内容已截断...]"
        return _text_response(f"# {url}\n\n{text}")

    except Exception as e:
        return _text_response(f"<error>访问失败: {e}</error>")


# ── 6. get_current_time ───────────────────────────────────


def get_current_time(timezone_name: str = "Asia/Shanghai") -> ToolResponse:
    """获取当前时间。

    Args:
        timezone_name: 时区名称，默认 Asia/Shanghai（北京时间）

    Returns:
        当前日期和时间字符串
    """
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(timezone_name)
        now = datetime.now(tz)
        result = (
            f"当前时间（{timezone_name}）\n"
            f"日期: {now.strftime('%Y年%m月%d日 %A')}\n"
            f"时间: {now.strftime('%H:%M:%S')}\n"
            f"ISO: {now.isoformat()}"
        )
        return _text_response(result)
    except Exception as e:
        # 回退到 UTC
        now = datetime.now(timezone.utc)
        return _text_response(f"当前 UTC 时间: {now.isoformat()} （时区解析失败: {e}）")


# ── 7. skill_cli ─────────────────────────────────────────


def skill_cli(action: str, name: str = "", url: str = "") -> ToolResponse:
    """管理 AgentPal 技能包的命令行工具。

    支持的操作：
    - list: 列出所有已安装技能
    - enable <name>: 启用指定技能
    - disable <name>: 禁用指定技能
    - remove <name>: 卸载指定技能
    - install <url>: 从 URL 安装技能（支持 clawhub.ai / skills.sh）
    - search <name>: 搜索可用技能（暂未实现）

    Args:
        action: 操作类型，可选值: list, enable, disable, remove, install, search
        name: 技能名称（用于 enable/disable/remove）
        url: 技能包 URL（用于 install）

    Returns:
        操作结果文本
    """
    # 这个工具函数实际上是一个桥接器，真正的异步操作在 _skill_cli_async 中。
    # 由于 agentscope 的工具函数是同步的，我们使用 asyncio 来运行异步操作。
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 已有事件循环在运行（正常情况），创建 future
        import concurrent.futures
        # 在同步上下文中无法直接 await，返回提示信息
        return _text_response(
            f"skill_cli: action={action}, name={name}, url={url}\n"
            "注意：skill_cli 操作将通过 API 异步执行。\n"
            f"请使用以下 API 完成操作：\n"
            f"- 列出技能: GET /api/v1/skills\n"
            f"- 安装技能: POST /api/v1/skills/install/url {{\"url\": \"...\"}}\n"
            f"- 启用技能: PATCH /api/v1/skills/{{name}} {{\"enabled\": true}}\n"
            f"- 禁用技能: PATCH /api/v1/skills/{{name}} {{\"enabled\": false}}\n"
            f"- 卸载技能: DELETE /api/v1/skills/{{name}}"
        )
    else:
        return _text_response(
            f"skill_cli: action={action}, name={name}, url={url}\n"
            "操作已记录，请通过技能管理页面或 API 完成。"
        )


# ── 工具元数据注册表 ──────────────────────────────────────

BUILTIN_TOOLS: list[dict] = [
    {
        "name": "execute_shell_command",
        "func": execute_shell_command,
        "description": "执行 Shell 命令",
        "icon": "Terminal",
        "dangerous": True,
    },
    {
        "name": "read_file",
        "func": read_file,
        "description": "读取本地文件内容",
        "icon": "FileText",
        "dangerous": False,
    },
    {
        "name": "write_file",
        "func": write_file,
        "description": "写入文件（覆盖模式）",
        "icon": "FilePlus",
        "dangerous": True,
    },
    {
        "name": "edit_file",
        "func": edit_file,
        "description": "精确替换文件中的文本片段",
        "icon": "FileEdit",
        "dangerous": True,
    },
    {
        "name": "browser_use",
        "func": browser_use,
        "description": "访问网页并提取内容",
        "icon": "Globe",
        "dangerous": False,
    },
    {
        "name": "get_current_time",
        "func": get_current_time,
        "description": "获取当前时间",
        "icon": "Clock",
        "dangerous": False,
    },
    {
        "name": "skill_cli",
        "func": skill_cli,
        "description": "管理技能包（列出、安装、启用、禁用、卸载）",
        "icon": "Puzzle",
        "dangerous": False,
    },
]
