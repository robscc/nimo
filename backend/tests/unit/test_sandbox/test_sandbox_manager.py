"""SandboxManager 单元测试。

所有 Docker SDK 调用全部 mock，覆盖容器创建/复用、命令执行、
文件读写、容器列表、过期清理、容器删除等。
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from agentpal.sandbox.manager import SandboxManager


@pytest.fixture
def mock_docker_client():
    """Mock Docker client。"""
    client = MagicMock()
    return client


@pytest.fixture
def manager(mock_docker_client):
    """带 mock Docker client 的 SandboxManager。"""
    mgr = SandboxManager(image="python:3.11-slim", memory_limit="512m")
    mgr._client = mock_docker_client
    return mgr


# ── 容器创建与复用 ────────────────────────────────────────────


class TestCreateOrGet:
    @pytest.mark.asyncio
    async def test_create_new_container(self, manager, mock_docker_client):
        """不存在同名容器时，应创建新容器。"""
        mock_docker_client.containers.get.side_effect = Exception("Not Found")
        mock_container = MagicMock()
        mock_container.id = "container-abc123"
        mock_docker_client.containers.run.return_value = mock_container

        container_id = await manager.create_or_get("test-sandbox-1")

        assert container_id == "container-abc123"
        mock_docker_client.containers.run.assert_called_once()
        call_kwargs = mock_docker_client.containers.run.call_args
        assert call_kwargs[0][0] == "python:3.11-slim"
        assert call_kwargs[1]["command"] == "sleep infinity"
        assert call_kwargs[1]["detach"] is True

    @pytest.mark.asyncio
    async def test_reuse_running_container(self, manager, mock_docker_client):
        """已有运行中容器时，应直接复用。"""
        mock_container = MagicMock()
        mock_container.id = "existing-container-456"
        mock_container.status = "running"
        mock_docker_client.containers.get.return_value = mock_container

        container_id = await manager.create_or_get("test-sandbox-2")

        assert container_id == "existing-container-456"
        mock_docker_client.containers.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_stopped_container(self, manager, mock_docker_client):
        """已有但已停止的容器，应重启。"""
        mock_container = MagicMock()
        mock_container.id = "stopped-container-789"
        mock_container.status = "exited"
        mock_docker_client.containers.get.return_value = mock_container

        container_id = await manager.create_or_get("test-sandbox-3")

        assert container_id == "stopped-container-789"
        mock_container.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_with_workspace_files(self, manager, mock_docker_client):
        """创建容器时拷贝 workspace 文件。"""
        mock_docker_client.containers.get.side_effect = [
            Exception("Not Found"),  # _find_container
            MagicMock(exec_run=MagicMock(), put_archive=MagicMock()),  # _copy_files
        ]
        mock_container = MagicMock()
        mock_container.id = "new-with-files"
        mock_docker_client.containers.run.return_value = mock_container

        files = {"SOUL.md": "# Soul\nBe helpful", "AGENTS.md": "# Agents\n..."}
        container_id = await manager.create_or_get("sandbox-files", workspace_files=files)

        assert container_id == "new-with-files"

    @pytest.mark.asyncio
    async def test_container_name_format(self, manager, mock_docker_client):
        """容器名称格式为 agentpal-sandbox-{id}。"""
        mock_docker_client.containers.get.side_effect = Exception("Not Found")
        mock_container = MagicMock()
        mock_container.id = "c-123"
        mock_docker_client.containers.run.return_value = mock_container

        await manager.create_or_get("my-task-id")

        call_kwargs = mock_docker_client.containers.run.call_args[1]
        assert call_kwargs["name"] == "agentpal-sandbox-my-task-id"

    @pytest.mark.asyncio
    async def test_container_labels(self, manager, mock_docker_client):
        """容器应带有 agentpal.sandbox=true 标签。"""
        mock_docker_client.containers.get.side_effect = Exception("Not Found")
        mock_container = MagicMock()
        mock_container.id = "c-456"
        mock_docker_client.containers.run.return_value = mock_container

        await manager.create_or_get("label-test")

        call_kwargs = mock_docker_client.containers.run.call_args[1]
        labels = call_kwargs["labels"]
        assert labels["agentpal.sandbox"] == "true"
        assert "agentpal.created_at" in labels

    @pytest.mark.asyncio
    async def test_container_volume_naming(self, manager, mock_docker_client):
        """Named volume 命名为 agentpal-sandbox-data-{id}。"""
        mock_docker_client.containers.get.side_effect = Exception("Not Found")
        mock_container = MagicMock()
        mock_container.id = "c-vol"
        mock_docker_client.containers.run.return_value = mock_container

        await manager.create_or_get("vol-test")

        call_kwargs = mock_docker_client.containers.run.call_args[1]
        volumes = call_kwargs["volumes"]
        assert "agentpal-sandbox-data-vol-test" in volumes


# ── 命令执行 ────────────────────────────────────────────────


class TestExecCommand:
    @pytest.mark.asyncio
    async def test_exec_command_success(self, manager, mock_docker_client):
        """成功执行命令返回正确的 exit_code/stdout/stderr。"""
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (
            0,
            (b"hello world\n", b""),
        )
        mock_docker_client.containers.get.return_value = mock_container

        result = await manager.exec_command("c-123", "echo hello world")

        assert result["exit_code"] == 0
        assert "hello world" in result["stdout"]
        assert result["stderr"] == ""

    @pytest.mark.asyncio
    async def test_exec_command_failure(self, manager, mock_docker_client):
        """命令失败返回非零 exit_code。"""
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (
            1,
            (b"", b"command not found\n"),
        )
        mock_docker_client.containers.get.return_value = mock_container

        result = await manager.exec_command("c-123", "nonexistent_cmd")

        assert result["exit_code"] == 1
        assert "command not found" in result["stderr"]

    @pytest.mark.asyncio
    async def test_exec_command_timeout(self, manager, mock_docker_client):
        """命令超时返回特殊错误。"""
        import time as time_mod

        mock_container = MagicMock()

        def slow_exec(*args, **kwargs):
            time_mod.sleep(5)
            return (0, b"late")

        mock_container.exec_run.side_effect = slow_exec
        mock_docker_client.containers.get.return_value = mock_container

        result = await manager.exec_command("c-123", "sleep 100", timeout=1)

        assert result["exit_code"] == -1
        assert "超时" in result["stderr"]


# ── 文件读写 ────────────────────────────────────────────────


class TestFileOperations:
    @pytest.mark.asyncio
    async def test_read_file_success(self, manager, mock_docker_client):
        """从容器读取文件内容。"""
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (0, b"file content here")
        mock_docker_client.containers.get.return_value = mock_container

        content = await manager.read_file("c-123", "/workspace/test.txt")

        assert content == "file content here"

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, manager, mock_docker_client):
        """读取不存在的文件应抛 FileNotFoundError。"""
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (1, b"No such file or directory")
        mock_docker_client.containers.get.return_value = mock_container

        with pytest.raises(FileNotFoundError):
            await manager.read_file("c-123", "/nonexistent")

    @pytest.mark.asyncio
    async def test_write_file_success(self, manager, mock_docker_client):
        """向容器写入文件。"""
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (0, b"")
        mock_container.put_archive.return_value = True
        mock_docker_client.containers.get.return_value = mock_container

        result = await manager.write_file("c-123", "/workspace/out.txt", "hello")

        assert "已写入" in result


# ── 容器列表 ────────────────────────────────────────────────


class TestListContainers:
    @pytest.mark.asyncio
    async def test_list_containers_empty(self, manager, mock_docker_client):
        """无沙箱容器时返回空列表。"""
        mock_docker_client.containers.list.return_value = []

        result = await manager.list_containers()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_containers_returns_info(self, manager, mock_docker_client):
        """返回容器基本信息。"""
        mock_c = MagicMock()
        mock_c.id = "c-list-1"
        mock_c.short_id = "c-list-1"
        mock_c.name = "agentpal-sandbox-test"
        mock_c.status = "running"
        mock_c.labels = {
            "agentpal.sandbox": "true",
            "agentpal.created_at": "1700000000",
        }
        mock_docker_client.containers.list.return_value = [mock_c]

        result = await manager.list_containers()

        assert len(result) == 1
        assert result[0]["name"] == "agentpal-sandbox-test"
        assert result[0]["status"] == "running"


# ── 清理与删除 ──────────────────────────────────────────────


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_stale_removes_old_containers(self, manager, mock_docker_client):
        """清理超过 max_age_hours 的容器。"""
        import time

        old_container = MagicMock()
        old_container.labels = {
            "agentpal.sandbox": "true",
            "agentpal.created_at": str(int(time.time()) - 100 * 3600),
        }
        old_container.name = "agentpal-sandbox-old"

        new_container = MagicMock()
        new_container.labels = {
            "agentpal.sandbox": "true",
            "agentpal.created_at": str(int(time.time()) - 1 * 3600),
        }
        new_container.name = "agentpal-sandbox-new"

        mock_docker_client.containers.list.return_value = [old_container, new_container]

        cleaned = await manager.cleanup_stale(max_age_hours=72)

        assert cleaned == 1
        old_container.remove.assert_called_once_with(force=True)
        new_container.remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_no_stale(self, manager, mock_docker_client):
        """无过期容器时返回 0。"""
        import time

        fresh = MagicMock()
        fresh.labels = {
            "agentpal.sandbox": "true",
            "agentpal.created_at": str(int(time.time())),
        }
        fresh.name = "agentpal-sandbox-fresh"
        mock_docker_client.containers.list.return_value = [fresh]

        cleaned = await manager.cleanup_stale(max_age_hours=72)

        assert cleaned == 0

    @pytest.mark.asyncio
    async def test_remove_container_success(self, manager, mock_docker_client):
        """成功删除指定容器。"""
        mock_container = MagicMock()
        mock_docker_client.containers.get.return_value = mock_container

        result = await manager.remove_container("c-to-delete")

        assert result is True
        mock_container.remove.assert_called_once_with(force=True)

    @pytest.mark.asyncio
    async def test_remove_container_not_found(self, manager, mock_docker_client):
        """删除不存在的容器返回 False。"""
        mock_docker_client.containers.get.side_effect = Exception("Not Found")

        result = await manager.remove_container("c-nonexistent")

        assert result is False


# ── 初始化 ──────────────────────────────────────────────────


class TestManagerInit:
    def test_default_config(self):
        """默认配置正确。"""
        mgr = SandboxManager()
        assert mgr._image == "python:3.11-slim"
        assert mgr._memory_limit == "512m"

    def test_custom_config(self):
        """自定义配置生效。"""
        mgr = SandboxManager(image="ubuntu:22.04", memory_limit="1g")
        assert mgr._image == "ubuntu:22.04"
        assert mgr._memory_limit == "1g"

    def test_lazy_client_init(self):
        """client 应惰性初始化。"""
        mgr = SandboxManager()
        assert mgr._client is None

    def test_get_client_creates_docker_client(self):
        """_get_client 应调用 docker.from_env()。"""
        import importlib

        import agentpal.sandbox.manager as mgr_module

        mgr = SandboxManager()
        mock_docker_module = MagicMock()
        mock_docker_module.from_env.return_value = MagicMock()

        original_get_client = mgr._get_client.__func__

        # Patch the import inside _get_client
        with patch.dict("sys.modules", {"docker": mock_docker_module}):
            # Need to clear the cached client and call again
            mgr._client = None
            client = mgr._get_client()
            mock_docker_module.from_env.assert_called_once()
            assert client is not None
