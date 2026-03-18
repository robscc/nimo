"""SandboxAgent — 在 Docker 容器中隔离执行任务的 SubAgent。

继承 SubAgent，仅覆盖工具集构建和系统提示词两个方法，
自动创建 Docker 容器并将 workspace 文件拷贝进去。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from agentpal.agents.sub_agent import SubAgent
from agentpal.memory.base import BaseMemory
from agentpal.models.session import SubAgentTask
from agentpal.sandbox.manager import SandboxManager


class SandboxAgent(SubAgent):
    """在 Docker 沙箱中执行任务的 SubAgent。

    Args:
        session_id:       独立的子会话 ID
        memory:           子 Agent 的记忆后端
        task:             对应的 SubAgentTask 数据库记录
        db:               AsyncSession
        model_config:     LLM 配置 dict
        role_prompt:      角色系统提示词
        max_tool_rounds:  最大工具调用轮次
        parent_session_id: 父会话 ID
        sandbox_config:   沙箱配置 dict（image, memory_limit 等）
    """

    def __init__(
        self,
        session_id: str,
        memory: BaseMemory,
        task: SubAgentTask,
        db: Any,
        model_config: dict[str, Any] | None = None,
        role_prompt: str = "",
        max_tool_rounds: int = 12,
        parent_session_id: str = "",
        sandbox_config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            session_id=session_id,
            memory=memory,
            task=task,
            db=db,
            model_config=model_config,
            role_prompt=role_prompt,
            max_tool_rounds=max_tool_rounds,
            parent_session_id=parent_session_id,
        )
        self._sandbox_config = sandbox_config or {}
        self._sandbox_manager: SandboxManager | None = None
        self._container_id: str | None = None

    # ── 容器管理 ──────────────────────────────────────────────

    async def _ensure_container(self) -> str:
        """惰性创建/获取沙箱容器，返回 container_id。"""
        if self._container_id is not None:
            return self._container_id

        image = self._sandbox_config.get("image", "python:3.11-slim")
        memory_limit = self._sandbox_config.get("memory_limit", "512m")

        self._sandbox_manager = SandboxManager(
            image=image,
            memory_limit=memory_limit,
        )

        # 使用 task_id 作为 sandbox_id
        sandbox_id = self._task.id

        # 收集 workspace 文件
        workspace_files = await self._load_workspace_files()

        self._container_id = await self._sandbox_manager.create_or_get(
            sandbox_id=sandbox_id,
            workspace_files=workspace_files or None,
        )

        self._log("sandbox_created", {
            "container_id": self._container_id[:12],
            "image": image,
            "memory_limit": memory_limit,
            "workspace_files": list(workspace_files.keys()) if workspace_files else [],
        })

        logger.info(
            "SandboxAgent 容器已就绪: task={} container={}",
            self._task.id, self._container_id[:12],
        )

        return self._container_id

    async def _load_workspace_files(self) -> dict[str, str]:
        """加载 workspace 文件（SOUL.md, AGENTS.md 等）供拷贝到容器。"""
        from agentpal.config import get_settings

        settings = get_settings()
        workspace_dir = Path(settings.workspace_dir).expanduser()

        files: dict[str, str] = {}
        target_files = [
            "SOUL.md",
            "AGENTS.md",
            "HEARTBEAT.md",
            "MEMORY.md",
        ]

        for fname in target_files:
            fpath = workspace_dir / fname
            if fpath.exists():
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    files[fname] = content
                except Exception as e:
                    logger.debug(f"读取 workspace 文件失败: {fpath} — {e}")

        return files

    # ── 覆盖：工具集构建 ────────────────────────────────────

    async def _build_toolkit(self) -> Any:
        """构建沙箱工具集（所有命令在容器内执行）。"""
        from agentpal.sandbox.tools import create_sandbox_tools
        from agentpal.tools.registry import build_toolkit

        # 确保容器就绪
        container_id = await self._ensure_container()

        # 创建沙箱工具
        sandbox_tools = create_sandbox_tools(
            manager=self._sandbox_manager,
            container_id=container_id,
        )

        # 使用 build_toolkit 的 extra_tools 参数构建
        # 传入空列表（不使用宿主机内置工具），只用沙箱工具
        return build_toolkit([], extra_tools=sandbox_tools)

    # ── 覆盖：系统提示词 ────────────────────────────────────

    def _build_sub_system_prompt(self) -> str:
        """构建沙箱 SubAgent 的 system prompt。"""
        base_prompt = super()._build_sub_system_prompt()

        sandbox_supplement = (
            "\n\n---\n\n"
            "# 沙箱执行环境\n\n"
            "你运行在一个隔离的 Docker 容器中。以下是关键信息：\n\n"
            "- **工作目录**: `/workspace`（已挂载持久化卷）\n"
            "- **上下文文件**: `/workspace/context/`（包含 SOUL.md, AGENTS.md 等）\n"
            "- **操作系统**: Linux（Debian-based）\n"
            "- **Python**: 预装 Python 3.11\n"
            "- **包管理**: 可自由使用 `pip install` 安装任何需要的包\n"
            "- **系统工具**: 可使用 `apt-get install` 安装系统工具\n"
            "- **文件持久化**: `/workspace` 下的所有文件会在任务结束后保留\n\n"
            "**安全说明**：你在沙箱中操作，不会影响宿主机。可以自由实验和执行命令。\n\n"
            "**输出要求**：请将最终结果文件保存在 `/workspace/` 目录下。"
        )

        return base_prompt + sandbox_supplement
