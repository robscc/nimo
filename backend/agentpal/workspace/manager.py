"""WorkspaceManager — 工作空间文件的读写管理。

工作空间位于 ~/.nimo/（默认），结构：
    ~/.nimo/
    ├── .bootstrapped          ← 初始化完成标记
    ├── AGENTS.md              ← Agent 定义与路由规则
    ├── IDENTITY.md            ← Agent 身份
    ├── SOUL.md                ← 性格与价值观
    ├── USER.md                ← 用户画像
    ├── MEMORY.md              ← 持久化长期记忆
    ├── CONTEXT.md             ← 当前阶段补充背景（可选）
    ├── BOOTSTRAP.md           ← 首次运行引导（完成后删除）
    ├── HEARTBEAT.md           ← 定期心跳任务清单
    ├── memory/
    │   └── YYYY-MM-DD.md      ← 每日摘要日志
    └── canvas/                ← Agent 工作区文件
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
from loguru import logger

from agentpal.workspace.context_builder import WorkspaceFiles
from agentpal.workspace.defaults import DEFAULT_FILES, EDITABLE_FILES


class WorkspaceManager:
    """异步工作空间管理器。

    Args:
        workspace_dir: 工作空间根目录，默认 ~/.nimo
    """

    DEFAULT_DIR: Path = Path.home() / ".nimo"
    _BOOTSTRAP_FLAG = ".bootstrapped"

    def __init__(self, workspace_dir: Path | str | None = None) -> None:
        self.root = Path(workspace_dir) if workspace_dir else self.DEFAULT_DIR
        self.memory_dir = self.root / "memory"
        self.canvas_dir = self.root / "canvas"
        self._bootstrapped = False

    # ── Bootstrap ────────────────────────────────────────

    async def bootstrap(self) -> bool:
        """初始化工作空间（幂等）。首次调用时创建目录和默认文件。

        Returns:
            True 表示本次执行了初始化；False 表示已初始化，跳过。
        """
        flag = self.root / self._BOOTSTRAP_FLAG
        if flag.exists():
            self._bootstrapped = True
            return False

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._create_dirs_sync)
        await loop.run_in_executor(None, self._write_defaults_sync)
        flag.write_text("1", encoding="utf-8")
        self._bootstrapped = True
        logger.info(f"WorkspaceManager: 工作空间初始化完成 → {self.root}")
        return True

    def _create_dirs_sync(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(exist_ok=True)
        self.canvas_dir.mkdir(exist_ok=True)

    def _write_defaults_sync(self) -> None:
        for filename, content in DEFAULT_FILES.items():
            path = self.root / filename
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    # ── 读取所有 workspace 文件 ───────────────────────────

    async def load(self) -> WorkspaceFiles:
        """一次性加载所有 workspace 文件，返回 WorkspaceFiles 数据类。"""
        await self.bootstrap()

        (
            agents, identity, soul, user, memory, context,
            today_log, bootstrap_content, heartbeat,
        ) = await asyncio.gather(
            self._read(self.root / "AGENTS.md"),
            self._read(self.root / "IDENTITY.md"),
            self._read(self.root / "SOUL.md"),
            self._read(self.root / "USER.md"),
            self._read(self.root / "MEMORY.md"),
            self._read(self.root / "CONTEXT.md"),
            self._read_today_log(),
            self._read(self.root / "BOOTSTRAP.md"),
            self._read(self.root / "HEARTBEAT.md"),
        )

        return WorkspaceFiles(
            agents=agents,
            identity=identity,
            soul=soul,
            user=user,
            memory=memory,
            context=context,
            today_log=today_log,
            bootstrap=bootstrap_content,
            heartbeat=heartbeat,
        )

    # ── 读写单个文件 ──────────────────────────────────────

    async def read_file(self, name: str) -> str:
        """读取可编辑的 workspace 文件。name 如 'SOUL.md'。"""
        if name not in EDITABLE_FILES:
            raise ValueError(f"不支持的文件: {name}，可用：{EDITABLE_FILES}")
        await self.bootstrap()
        return await self._read(self.root / name)

    async def write_file(self, name: str, content: str) -> None:
        """覆写 workspace 文件。"""
        if name not in EDITABLE_FILES:
            raise ValueError(f"不支持的文件: {name}，可用：{EDITABLE_FILES}")
        await self.bootstrap()
        await self._write(self.root / name, content)

    # ── 长期记忆 ──────────────────────────────────────────

    async def append_memory(self, facts_md: str) -> None:
        """向 MEMORY.md 末尾追加新事实（带时间戳分隔线）。"""
        await self.bootstrap()
        now = datetime.now(timezone.utc).astimezone()
        stamp = now.strftime("%Y-%m-%d %H:%M")
        entry = f"\n\n<!-- {stamp} -->\n{facts_md.strip()}\n"
        await self._append(self.root / "MEMORY.md", entry)

    # ── 每日日志 ──────────────────────────────────────────

    async def append_daily_log(self, summary: str) -> None:
        """向今日日志文件追加摘要。"""
        await self.bootstrap()
        now = datetime.now(timezone.utc).astimezone()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")
        log_path = self.memory_dir / f"{date_str}.md"

        if not log_path.exists():
            header = f"# {date_str} 日志\n\n"
            await self._write(log_path, header)

        entry = f"\n## {time_str}\n{summary.strip()}\n"
        await self._append(log_path, entry)

    async def get_daily_log(self, date: str | None = None) -> str:
        """读取指定日期的日志（默认今天）。date 格式：YYYY-MM-DD。"""
        await self.bootstrap()
        if date is None:
            date = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        return await self._read(self.memory_dir / f"{date}.md")

    async def list_daily_logs(self) -> list[str]:
        """返回所有日志文件的日期列表，倒序排列。"""
        await self.bootstrap()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._list_daily_logs_sync)

    def _list_daily_logs_sync(self) -> list[str]:
        files = sorted(self.memory_dir.glob("*.md"), reverse=True)
        return [f.stem for f in files]

    # ── Canvas ────────────────────────────────────────────

    async def list_canvas(self) -> list[dict]:
        """列出 canvas 目录下的文件，返回 {name, size, modified_at}。"""
        await self.bootstrap()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._list_canvas_sync)

    def _list_canvas_sync(self) -> list[dict]:
        result = []
        for f in sorted(self.canvas_dir.iterdir()):
            if f.is_file():
                stat = f.stat()
                result.append({
                    "name": f.name,
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
        return result

    async def read_canvas(self, filename: str) -> str:
        """读取 canvas 文件。"""
        await self.bootstrap()
        self._validate_canvas_name(filename)
        return await self._read(self.canvas_dir / filename)

    async def write_canvas(self, filename: str, content: str) -> None:
        """写入 canvas 文件。"""
        await self.bootstrap()
        self._validate_canvas_name(filename)
        await self._write(self.canvas_dir / filename, content)

    @staticmethod
    def _validate_canvas_name(name: str) -> None:
        if "/" in name or "\\" in name or name.startswith("."):
            raise ValueError(f"非法文件名: {name!r}")

    # ── 底层 I/O ──────────────────────────────────────────

    async def _read(self, path: Path) -> str:
        if not path.exists():
            return ""
        try:
            async with aiofiles.open(path, encoding="utf-8") as f:
                return await f.read()
        except Exception as e:
            logger.warning(f"WorkspaceManager._read({path}): {e}")
            return ""

    async def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)

    async def _append(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            await f.write(content)

    async def _read_today_log(self) -> str:
        date_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        return await self._read(self.memory_dir / f"{date_str}.md")
