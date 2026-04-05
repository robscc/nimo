"""文件 / Shell 相关内置工具。"""

from __future__ import annotations

import mimetypes
import subprocess
from pathlib import Path

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from agentpal.config import get_settings
from agentpal.database import get_sync_db
from agentpal.models.session import TaskArtifact


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
    settings = get_settings()
    workspace = Path(settings.workspace_dir).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workspace,
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


# ── 5. read_uploaded_file ──────────────────────────────────


def read_uploaded_file(file_id: str, max_chars: int = 4000) -> ToolResponse:
    """读取聊天上传文件（仅限 workspace/uploads/chat 目录）。

    Args:
        file_id: 上传文件对应的 artifact id
        max_chars: 最大返回字符数，默认 4000

    Returns:
        包含文件元信息和文本片段的结果
    """
    settings = get_settings()
    upload_root = (Path(settings.workspace_dir).expanduser() / "uploads" / "chat").resolve()

    try:
        with get_sync_db() as db:
            artifact = db.get(TaskArtifact, file_id)

        if artifact is None or artifact.artifact_type != "uploaded_file":
            return _text_response(f"<error>未找到上传文件: {file_id}</error>")

        if not artifact.file_path:
            return _text_response("<error>文件路径缺失</error>")

        file_path = Path(artifact.file_path).expanduser().resolve()
        if upload_root not in file_path.parents:
            return _text_response("<error>拒绝访问非上传目录文件</error>")

        if not file_path.exists():
            return _text_response(f"<error>文件不存在: {file_path}</error>")

        guessed_mime = artifact.mime_type or mimetypes.guess_type(artifact.name)[0] or "application/octet-stream"
        size = file_path.stat().st_size
        text_mime = guessed_mime.startswith("text/") or guessed_mime in {
            "application/json",
            "application/xml",
            "application/javascript",
        }

        if not text_mime:
            return _text_response(
                "\n".join(
                    [
                        "[uploaded_file]",
                        f"file_id={artifact.id}",
                        f"name={artifact.name}",
                        f"mime={guessed_mime}",
                        f"size_bytes={size}",
                        "snippet=<binary file omitted>",
                    ]
                )
            )

        content = file_path.read_text(encoding="utf-8", errors="replace")
        snippet = content[:max(1, max_chars)]
        if len(content) > len(snippet):
            snippet += f"\n\n[... 已截断，总长度 {len(content)} 字符]"

        return _text_response(
            "\n".join(
                [
                    "[uploaded_file]",
                    f"file_id={artifact.id}",
                    f"name={artifact.name}",
                    f"mime={guessed_mime}",
                    f"size_bytes={size}",
                    "snippet:",
                    snippet,
                ]
            )
        )
    except Exception as e:
        return _text_response(f"<error>{e}</error>")
