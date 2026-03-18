"""沙箱工具 — 通过闭包将 SandboxManager + container_id 绑定到工具函数。

提供与宿主机工具同名同签名的沙箱版本，命令在 Docker 容器内执行。
"""

from __future__ import annotations

from typing import Any

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from agentpal.sandbox.manager import SandboxManager


def _text_response(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def create_sandbox_tools(
    manager: SandboxManager,
    container_id: str,
) -> list[dict[str, Any]]:
    """创建绑定到特定容器的沙箱工具列表。

    Args:
        manager:      SandboxManager 实例
        container_id: 目标 Docker 容器 ID

    Returns:
        与 BUILTIN_TOOLS 格式兼容的工具定义列表
    """

    def execute_shell_command(command: str, timeout: int = 30) -> ToolResponse:
        """在 Docker 沙箱中执行 Shell 命令并返回输出结果。

        Args:
            command: 要执行的 shell 命令
            timeout: 超时秒数（默认 30 秒）

        Returns:
            包含 exit_code、stdout、stderr 的执行结果
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在已有 event loop 中执行
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        manager.exec_command(container_id, command, timeout=timeout),
                    )
                    result = future.result(timeout=timeout + 10)
            else:
                result = asyncio.run(
                    manager.exec_command(container_id, command, timeout=timeout)
                )
        except Exception as e:
            return _text_response(f"<error>{e}</error>")

        output = (
            f"<returncode>{result['exit_code']}</returncode>\n"
            f"<stdout>{result['stdout'].strip()}</stdout>\n"
            f"<stderr>{result['stderr'].strip()}</stderr>"
        )
        return _text_response(output)

    def read_file(file_path: str, start_line: int = 1, end_line: int | None = None) -> ToolResponse:
        """从 Docker 沙箱中读取文件内容。

        Args:
            file_path: 文件路径（容器内路径）
            start_line: 起始行号（从 1 开始，默认 1）
            end_line: 结束行号（默认读到文件末尾）

        Returns:
            文件内容文本
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        manager.read_file(container_id, file_path),
                    )
                    content = future.result(timeout=30)
            else:
                content = asyncio.run(manager.read_file(container_id, file_path))

            lines = content.splitlines()
            selected = lines[start_line - 1: end_line]
            numbered = "\n".join(
                f"{start_line + i:4d}| {line}" for i, line in enumerate(selected)
            )
            return _text_response(f"# {file_path} (sandbox)\n```\n{numbered}\n```")
        except Exception as e:
            return _text_response(f"<error>{e}</error>")

    def write_file(file_path: str, content: str) -> ToolResponse:
        """将内容写入 Docker 沙箱中的文件（覆盖模式）。

        Args:
            file_path: 目标文件路径（容器内路径）
            content: 要写入的文本内容

        Returns:
            操作结果
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        manager.write_file(container_id, file_path, content),
                    )
                    result = future.result(timeout=30)
            else:
                result = asyncio.run(
                    manager.write_file(container_id, file_path, content)
                )
            return _text_response(f"[sandbox] {result}")
        except Exception as e:
            return _text_response(f"<error>{e}</error>")

    def edit_file(file_path: str, old_text: str, new_text: str) -> ToolResponse:
        """精确替换 Docker 沙箱中文件的指定文本片段。

        Args:
            file_path: 目标文件路径（容器内路径）
            old_text: 要替换的原始文本（必须在文件中唯一存在）
            new_text: 替换后的新文本

        Returns:
            操作结果
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        manager.read_file(container_id, file_path),
                    )
                    original = future.result(timeout=30)
            else:
                original = asyncio.run(manager.read_file(container_id, file_path))

            count = original.count(old_text)
            if count == 0:
                return _text_response("<error>未找到指定文本，请检查 old_text 是否准确</error>")
            if count > 1:
                return _text_response(
                    f"<error>找到 {count} 处匹配，old_text 必须唯一，请提供更多上下文</error>"
                )

            updated = original.replace(old_text, new_text, 1)

            loop = asyncio.get_event_loop()
            if loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        manager.write_file(container_id, file_path, updated),
                    )
                    future.result(timeout=30)
            else:
                asyncio.run(manager.write_file(container_id, file_path, updated))

            return _text_response(f"[sandbox] 已完成替换（{file_path}）")
        except Exception as e:
            return _text_response(f"<error>{e}</error>")

    def execute_python_code(
        code: str,
        packages: list[str] | None = None,
        timeout: int = 30,
    ) -> ToolResponse:
        """在 Docker 沙箱中执行 Python 代码。

        Args:
            code: 要执行的 Python 代码（支持多行）
            packages: 执行前需要 pip install 的包名列表
            timeout: 代码执行的最长等待秒数（默认 30 秒）

        Returns:
            包含 returncode、stdout、stderr 的执行结果
        """
        import asyncio

        try:
            # 如果有依赖包，先安装
            if packages:
                pkg_cmd = f"pip install --quiet {' '.join(packages)}"
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(
                            asyncio.run,
                            manager.exec_command(container_id, pkg_cmd, timeout=120),
                        )
                        pip_result = future.result(timeout=130)
                else:
                    pip_result = asyncio.run(
                        manager.exec_command(container_id, pkg_cmd, timeout=120)
                    )
                if pip_result["exit_code"] != 0:
                    return _text_response(
                        f"<error>pip install 失败\n{pip_result['stderr'].strip()}</error>"
                    )

            # 转义代码中的单引号，写入临时文件并执行
            escaped_code = code.replace("'", "'\\''")
            cmd = f"python3 -c '{escaped_code}'"

            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        manager.exec_command(container_id, cmd, timeout=timeout),
                    )
                    result = future.result(timeout=timeout + 10)
            else:
                result = asyncio.run(
                    manager.exec_command(container_id, cmd, timeout=timeout)
                )

            output = (
                f"<returncode>{result['exit_code']}</returncode>\n"
                f"<stdout>{result['stdout'].strip()}</stdout>\n"
                f"<stderr>{result['stderr'].strip()}</stderr>"
            )
            return _text_response(output)
        except Exception as e:
            return _text_response(f"<error>{e}</error>")

    # get_current_time 复用宿主版本（无副作用）
    from agentpal.tools.builtin import get_current_time

    return [
        {
            "name": "execute_shell_command",
            "func": execute_shell_command,
            "description": "在 Docker 沙箱中执行 Shell 命令",
            "icon": "Terminal",
            "dangerous": False,  # 沙箱内执行，安全等级降低
        },
        {
            "name": "read_file",
            "func": read_file,
            "description": "从 Docker 沙箱中读取文件内容",
            "icon": "FileText",
            "dangerous": False,
        },
        {
            "name": "write_file",
            "func": write_file,
            "description": "在 Docker 沙箱中写入文件",
            "icon": "FilePlus",
            "dangerous": False,
        },
        {
            "name": "edit_file",
            "func": edit_file,
            "description": "精确替换 Docker 沙箱中文件的文本片段",
            "icon": "FileEdit",
            "dangerous": False,
        },
        {
            "name": "execute_python_code",
            "func": execute_python_code,
            "description": "在 Docker 沙箱中执行 Python 代码",
            "icon": "Code2",
            "dangerous": False,
        },
        {
            "name": "get_current_time",
            "func": get_current_time,
            "description": "获取当前时间",
            "icon": "Clock",
            "dangerous": False,
        },
    ]
