"""SandboxManager — 封装 Docker 容器生命周期管理。

所有 Docker SDK 调用通过 run_in_executor 异步化，避免阻塞 asyncio event loop。
"""

from __future__ import annotations

import asyncio
import io
import tarfile
import time
from typing import Any

from loguru import logger


class SandboxManager:
    """管理 Docker 沙箱容器的创建、执行和清理。

    容器特点：
    - 镜像：python:3.11-slim（可配置）
    - 命令：sleep infinity（保持容器存活）
    - 卷：named volume 持久化 /workspace
    - 标签：agentpal.sandbox=true, agentpal.created_at=<timestamp>
    """

    LABEL_SANDBOX = "agentpal.sandbox"
    LABEL_CREATED_AT = "agentpal.created_at"
    CONTAINER_PREFIX = "agentpal-sandbox-"
    VOLUME_PREFIX = "agentpal-sandbox-data-"
    WORKSPACE_DIR = "/workspace"
    CONTEXT_DIR = "/workspace/context"

    def __init__(
        self,
        image: str = "python:3.11-slim",
        memory_limit: str = "512m",
    ) -> None:
        self._image = image
        self._memory_limit = memory_limit
        self._client: Any = None

    def _get_client(self) -> Any:
        """惰性获取 Docker client（避免在 import 时就要求 Docker daemon）。"""
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    async def create_or_get(
        self,
        sandbox_id: str,
        workspace_files: dict[str, str] | None = None,
    ) -> str:
        """创建或复用沙箱容器，返回 container_id。

        Args:
            sandbox_id: 沙箱标识（用于命名容器和卷）
            workspace_files: 要拷贝到容器 /workspace/context/ 的文件
                             key=文件名, value=文件内容

        Returns:
            Docker 容器 ID
        """
        container_name = f"{self.CONTAINER_PREFIX}{sandbox_id}"
        loop = asyncio.get_event_loop()

        # 尝试复用已有容器
        existing = await loop.run_in_executor(
            None, self._find_container, container_name
        )
        if existing is not None:
            # 确保容器在运行
            if existing.status != "running":
                await loop.run_in_executor(None, existing.start)
                logger.info(f"沙箱容器已重启: {container_name}")
            return existing.id

        # 创建新容器
        container_id = await loop.run_in_executor(
            None, self._create_container, container_name, sandbox_id
        )
        logger.info(f"沙箱容器已创建: {container_name} ({container_id[:12]})")

        # 拷贝 workspace 文件
        if workspace_files:
            await loop.run_in_executor(
                None, self._copy_files_to_container, container_id, workspace_files
            )
            logger.info(f"已拷贝 {len(workspace_files)} 个文件到容器 {self.CONTEXT_DIR}")

        return container_id

    async def exec_command(
        self,
        container_id: str,
        command: str,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """在容器内执行命令。

        Args:
            container_id: Docker 容器 ID
            command:       Shell 命令
            timeout:       超时秒数

        Returns:
            {"exit_code": int, "stdout": str, "stderr": str}
        """
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None, self._exec_sync, container_id, command
                ),
                timeout=timeout,
            )
            return result
        except asyncio.TimeoutError:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"命令超时（{timeout}秒）",
            }

    async def read_file(self, container_id: str, path: str) -> str:
        """从容器中读取文件内容。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._read_file_sync, container_id, path
        )

    async def write_file(self, container_id: str, path: str, content: str) -> str:
        """向容器中写入文件。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._write_file_sync, container_id, path, content
        )

    async def list_containers(self) -> list[dict[str, Any]]:
        """列出所有 agentpal-sandbox-* 容器。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._list_containers_sync)

    async def cleanup_stale(self, max_age_hours: int = 72) -> int:
        """清理超过指定时长的过期容器，返回清理数量。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._cleanup_stale_sync, max_age_hours
        )

    async def remove_container(self, container_id: str) -> bool:
        """删除指定容器（强制停止并删除）。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._remove_container_sync, container_id
        )

    # ── 同步实现（在 executor 中执行）────────────────────────

    def _find_container(self, container_name: str) -> Any:
        """查找已有容器。"""
        client = self._get_client()
        try:
            return client.containers.get(container_name)
        except Exception:
            return None

    def _create_container(self, container_name: str, sandbox_id: str) -> str:
        """创建新的沙箱容器。"""
        client = self._get_client()
        volume_name = f"{self.VOLUME_PREFIX}{sandbox_id}"

        container = client.containers.run(
            self._image,
            command="sleep infinity",
            name=container_name,
            detach=True,
            mem_limit=self._memory_limit,
            volumes={volume_name: {"bind": self.WORKSPACE_DIR, "mode": "rw"}},
            labels={
                self.LABEL_SANDBOX: "true",
                self.LABEL_CREATED_AT: str(int(time.time())),
            },
            working_dir=self.WORKSPACE_DIR,
        )
        return container.id

    def _copy_files_to_container(
        self, container_id: str, files: dict[str, str]
    ) -> None:
        """将文件打包为 tar 并拷贝到容器的 /workspace/context/ 目录。"""
        client = self._get_client()
        container = client.containers.get(container_id)

        # 先创建目标目录
        container.exec_run(f"mkdir -p {self.CONTEXT_DIR}")

        # 构建 tar archive
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            for filename, content in files.items():
                data = content.encode("utf-8")
                info = tarfile.TarInfo(name=filename)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

        tar_stream.seek(0)
        container.put_archive(self.CONTEXT_DIR, tar_stream)

    def _exec_sync(self, container_id: str, command: str) -> dict[str, Any]:
        """同步执行容器命令。"""
        client = self._get_client()
        container = client.containers.get(container_id)

        exit_code, output = container.exec_run(
            cmd=["sh", "-c", command],
            workdir=self.WORKSPACE_DIR,
            demux=True,
        )

        stdout = ""
        stderr = ""
        if output:
            if isinstance(output, tuple):
                stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
                stderr = output[1].decode("utf-8", errors="replace") if output[1] else ""
            else:
                stdout = output.decode("utf-8", errors="replace") if output else ""

        return {"exit_code": exit_code, "stdout": stdout, "stderr": stderr}

    def _read_file_sync(self, container_id: str, path: str) -> str:
        """从容器读文件（通过 cat 命令）。"""
        client = self._get_client()
        container = client.containers.get(container_id)

        exit_code, output = container.exec_run(
            cmd=["cat", path],
            workdir=self.WORKSPACE_DIR,
        )

        if exit_code != 0:
            error = output.decode("utf-8", errors="replace") if output else "未知错误"
            raise FileNotFoundError(f"容器内文件不存在或无法读取: {path} ({error})")

        return output.decode("utf-8", errors="replace") if output else ""

    def _write_file_sync(self, container_id: str, path: str, content: str) -> str:
        """向容器写文件（通过 tar put_archive）。"""
        client = self._get_client()
        container = client.containers.get(container_id)

        # 确保父目录存在
        import posixpath

        parent = posixpath.dirname(path)
        if parent:
            container.exec_run(f"mkdir -p {parent}")

        # 构建 tar 并写入
        filename = posixpath.basename(path)
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=filename)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        tar_stream.seek(0)
        container.put_archive(parent or "/", tar_stream)
        return f"已写入 {path}（{len(content)} 字符）"

    def _list_containers_sync(self) -> list[dict[str, Any]]:
        """列出所有沙箱容器。"""
        client = self._get_client()
        containers = client.containers.list(
            all=True,
            filters={"label": f"{self.LABEL_SANDBOX}=true"},
        )
        result = []
        for c in containers:
            created_at = c.labels.get(self.LABEL_CREATED_AT, "0")
            result.append({
                "id": c.id,
                "short_id": c.short_id,
                "name": c.name,
                "status": c.status,
                "created_at": int(created_at),
            })
        return result

    def _cleanup_stale_sync(self, max_age_hours: int) -> int:
        """清理过期容器。"""
        client = self._get_client()
        containers = client.containers.list(
            all=True,
            filters={"label": f"{self.LABEL_SANDBOX}=true"},
        )

        now = int(time.time())
        max_age_seconds = max_age_hours * 3600
        cleaned = 0

        for c in containers:
            created_at = int(c.labels.get(self.LABEL_CREATED_AT, "0"))
            age = now - created_at
            if age > max_age_seconds:
                try:
                    c.stop(timeout=5)
                except Exception:
                    pass
                try:
                    c.remove(force=True)
                    cleaned += 1
                    logger.info(f"已清理过期沙箱容器: {c.name} (age={age // 3600}h)")
                except Exception as e:
                    logger.warning(f"清理沙箱容器失败: {c.name} — {e}")

        return cleaned

    def _remove_container_sync(self, container_id: str) -> bool:
        """删除指定容器。"""
        client = self._get_client()
        try:
            container = client.containers.get(container_id)
            try:
                container.stop(timeout=5)
            except Exception:
                pass
            container.remove(force=True)
            return True
        except Exception as e:
            logger.warning(f"删除沙箱容器失败: {container_id} — {e}")
            return False
