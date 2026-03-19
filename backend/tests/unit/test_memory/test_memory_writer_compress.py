"""MemoryWriter 上下文压缩单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpal.memory.base import MemoryMessage, MemoryRole
from agentpal.memory.buffer import BufferMemory
from agentpal.memory.sqlite import SQLiteMemory
from agentpal.workspace.memory_writer import (
    COMPRESS_RESET_TOKENS,
    KEEP_RECENT,
    MemoryWriter,
)
from tests.conftest import make_msg


@pytest.fixture
def writer() -> MemoryWriter:
    """创建 MemoryWriter 实例，测试后清理 _active_compressions。"""
    w = MemoryWriter(compaction_threshold=30)
    yield w
    # 清理类级别状态
    MemoryWriter._active_compressions.clear()


@pytest.fixture
def mock_ws_manager() -> MagicMock:
    ws = MagicMock()
    ws.append_memory = AsyncMock()
    ws.append_daily_log = AsyncMock()
    return ws


@pytest.fixture
def model_config() -> dict:
    return {
        "provider": "compatible",
        "model_name": "test-model",
        "api_key": "test-key",
        "base_url": "http://localhost",
    }


class TestMaybeCompress:
    """maybe_compress() 入口条件检查。"""

    @pytest.mark.asyncio
    async def test_maybe_compress_disabled(
        self, writer: MemoryWriter, mock_ws_manager: MagicMock, model_config: dict
    ):
        """context_window=0 时功能关闭，不触发压缩。"""
        memory = BufferMemory()
        await writer.maybe_compress(
            session_id="s1",
            memory=memory,
            ws_manager=mock_ws_manager,
            model_config=model_config,
            context_tokens=100000,
            context_window=0,
        )
        assert "s1" not in MemoryWriter._active_compressions

    @pytest.mark.asyncio
    async def test_maybe_compress_below_threshold(
        self, writer: MemoryWriter, mock_ws_manager: MagicMock, model_config: dict
    ):
        """context_tokens 低于阈值不触发。"""
        memory = BufferMemory()
        # 128000 * 0.8 = 102400，设 context_tokens=50000 不触发
        with patch.object(writer, "_compress", new_callable=AsyncMock) as mock_compress:
            await writer.maybe_compress(
                session_id="s1",
                memory=memory,
                ws_manager=mock_ws_manager,
                model_config=model_config,
                context_tokens=50000,
                context_window=128000,
            )
            mock_compress.assert_not_called()

    @pytest.mark.asyncio
    async def test_maybe_compress_above_threshold(
        self, writer: MemoryWriter, mock_ws_manager: MagicMock, model_config: dict
    ):
        """context_tokens 超过 80% 时触发压缩。"""
        memory = BufferMemory()
        # 128000 * 0.8 = 102400，设 context_tokens=110000 应触发
        with patch("agentpal.workspace.memory_writer.asyncio") as mock_asyncio:
            await writer.maybe_compress(
                session_id="s1",
                memory=memory,
                ws_manager=mock_ws_manager,
                model_config=model_config,
                context_tokens=110000,
                context_window=128000,
            )
            mock_asyncio.create_task.assert_called_once()
            assert "s1" in MemoryWriter._active_compressions

    @pytest.mark.asyncio
    async def test_maybe_compress_reentrant_guard(
        self, writer: MemoryWriter, mock_ws_manager: MagicMock, model_config: dict
    ):
        """同一 session 正在压缩时不重复触发。"""
        memory = BufferMemory()
        MemoryWriter._active_compressions.add("s1")

        with patch("agentpal.workspace.memory_writer.asyncio") as mock_asyncio:
            await writer.maybe_compress(
                session_id="s1",
                memory=memory,
                ws_manager=mock_ws_manager,
                model_config=model_config,
                context_tokens=110000,
                context_window=128000,
            )
            mock_asyncio.create_task.assert_not_called()


def _patch_compress_bg_memory(sqlite_memory: SQLiteMemory):
    """返回 patch 上下文：让 _compress 内部创建的 bg_memory 指向测试的 sqlite_memory。

    _compress 会做 `async with AsyncSessionLocal() as bg_db` 然后
    `MemoryFactory.create("sqlite", db=bg_db)` —— 这里我们替换 MemoryFactory.create
    使其直接返回测试 fixture 的 sqlite_memory。
    """
    mock_bg_db = AsyncMock()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_bg_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    return (
        patch("agentpal.database.AsyncSessionLocal", return_value=mock_session_ctx),
        patch(
            "agentpal.memory.factory.MemoryFactory.create",
            return_value=sqlite_memory,
        ),
        mock_bg_db,
    )


class TestCompress:
    """_compress() 核心压缩逻辑测试（需要 DB fixture）。"""

    @pytest.mark.asyncio
    async def test_compress_too_few_messages(
        self, writer: MemoryWriter, mock_ws_manager: MagicMock, model_config: dict
    ):
        """消息数 <= KEEP_RECENT 时不压缩。"""
        memory = BufferMemory()
        for i in range(KEEP_RECENT):
            await memory.add(make_msg(f"msg-{i}"))

        MemoryWriter._active_compressions.add("test-session")
        await writer._compress("test-session", memory, mock_ws_manager, model_config)
        # finally 中应移除标记
        assert "test-session" not in MemoryWriter._active_compressions

    @pytest.mark.asyncio
    async def test_compress_marks_messages(
        self,
        sqlite_memory: SQLiteMemory,
        mock_ws_manager: MagicMock,
        model_config: dict,
        writer: MemoryWriter,
    ):
        """旧消息被标记 compressed=true，最近消息不被标记。"""
        # 创建 10 条消息（KEEP_RECENT=6，所以前 4 条会被压缩）
        msgs = []
        for i in range(10):
            m = await sqlite_memory.add(make_msg(f"msg-{i}"))
            msgs.append(m)

        # Mock LLM 返回摘要
        mock_response = MagicMock()
        mock_response.content = [{"type": "text", "text": "【对话摘要】测试摘要内容"}]
        mock_response.usage = None

        patch_session, patch_factory, mock_bg_db = _patch_compress_bg_memory(sqlite_memory)

        # Mock _flush 以避免实际 LLM 调用
        with (
            patch(
                "agentpal.agents.personal_assistant._build_model",
                return_value=AsyncMock(return_value=mock_response),
            ),
            patch(
                "agentpal.agents.personal_assistant._extract_text",
                return_value="【对话摘要】测试摘要内容",
            ),
            patch.object(writer, "_flush", new_callable=AsyncMock),
            patch_session,
            patch_factory,
        ):
            MemoryWriter._active_compressions.add("test-session")
            await writer._compress("test-session", sqlite_memory, mock_ws_manager, model_config)

        # 验证前 4 条被标记
        all_msgs = await sqlite_memory.get_recent("test-session", limit=20)
        # 应有 11 条（10原始 + 1摘要）
        compressed_msgs = [m for m in all_msgs if (m.metadata or {}).get("compressed")]
        assert len(compressed_msgs) == 4

        # 验证摘要消息
        summary_msgs = [m for m in all_msgs if (m.metadata or {}).get("type") == "context_summary"]
        assert len(summary_msgs) == 1
        assert summary_msgs[0].content == "【对话摘要】测试摘要内容"
        assert summary_msgs[0].metadata.get("compressed_count") == 4

    @pytest.mark.asyncio
    async def test_compress_inserts_summary(
        self,
        sqlite_memory: SQLiteMemory,
        mock_ws_manager: MagicMock,
        model_config: dict,
        writer: MemoryWriter,
    ):
        """压缩后插入摘要消息（type=context_summary）。"""
        for i in range(10):
            await sqlite_memory.add(make_msg(f"msg-{i}"))

        mock_response = MagicMock()
        mock_response.content = [{"type": "text", "text": "【对话摘要】关于项目部署的讨论"}]
        mock_response.usage = None

        patch_session, patch_factory, mock_bg_db = _patch_compress_bg_memory(sqlite_memory)

        with (
            patch(
                "agentpal.agents.personal_assistant._build_model",
                return_value=AsyncMock(return_value=mock_response),
            ),
            patch(
                "agentpal.agents.personal_assistant._extract_text",
                return_value="【对话摘要】关于项目部署的讨论",
            ),
            patch.object(writer, "_flush", new_callable=AsyncMock),
            patch_session,
            patch_factory,
        ):
            MemoryWriter._active_compressions.add("test-session")
            await writer._compress("test-session", sqlite_memory, mock_ws_manager, model_config)

        all_msgs = await sqlite_memory.get_recent("test-session", limit=20)
        summary = [m for m in all_msgs if (m.metadata or {}).get("type") == "context_summary"]
        assert len(summary) == 1
        assert "对话摘要" in summary[0].content

    @pytest.mark.asyncio
    async def test_compress_resets_tokens(
        self,
        sqlite_memory: SQLiteMemory,
        mock_ws_manager: MagicMock,
        model_config: dict,
        writer: MemoryWriter,
    ):
        """压缩后调用 SQL UPDATE 重置 context_tokens。"""
        for i in range(10):
            await sqlite_memory.add(make_msg(f"msg-{i}"))

        mock_response = MagicMock()
        mock_response.content = [{"type": "text", "text": "【对话摘要】测试"}]
        mock_response.usage = None

        patch_session, patch_factory, mock_bg_db = _patch_compress_bg_memory(sqlite_memory)

        with (
            patch(
                "agentpal.agents.personal_assistant._build_model",
                return_value=AsyncMock(return_value=mock_response),
            ),
            patch(
                "agentpal.agents.personal_assistant._extract_text",
                return_value="【对话摘要】测试",
            ),
            patch.object(writer, "_flush", new_callable=AsyncMock),
            patch_session,
            patch_factory,
        ):
            MemoryWriter._active_compressions.add("test-session")
            await writer._compress("test-session", sqlite_memory, mock_ws_manager, model_config)

        # 验证 bg_db.execute 被调用来更新 context_tokens
        execute_calls = mock_bg_db.execute.call_args_list
        assert len(execute_calls) > 0
        # 最后一个 execute 应是 UPDATE sessions SET context_tokens
        last_call_args = execute_calls[-1]
        params = last_call_args[0][1] if len(last_call_args[0]) > 1 else last_call_args[1].get("parameters", {})
        assert params.get("tokens") == COMPRESS_RESET_TOKENS

    @pytest.mark.asyncio
    async def test_compress_clears_active_on_error(
        self, writer: MemoryWriter, mock_ws_manager: MagicMock, model_config: dict
    ):
        """即使压缩过程出错，finally 也应清除 _active_compressions。"""
        memory = MagicMock()
        memory.get_recent = AsyncMock(side_effect=Exception("DB error"))

        MemoryWriter._active_compressions.add("s1")
        await writer._compress("s1", memory, mock_ws_manager, model_config)
        assert "s1" not in MemoryWriter._active_compressions

    @pytest.mark.asyncio
    async def test_compress_skips_already_compressed(
        self,
        sqlite_memory: SQLiteMemory,
        mock_ws_manager: MagicMock,
        model_config: dict,
        writer: MemoryWriter,
    ):
        """已压缩的旧消息不会被重复压缩。"""
        # 创建 10 条消息，前 2 条已标记 compressed
        for i in range(10):
            if i < 2:
                msg = MemoryMessage(
                    session_id="test-session",
                    role=MemoryRole.USER,
                    content=f"old-compressed-{i}",
                    metadata={"compressed": True},
                )
            else:
                msg = make_msg(f"msg-{i}")
            await sqlite_memory.add(msg)

        mock_response = MagicMock()
        mock_response.content = [{"type": "text", "text": "【对话摘要】测试"}]
        mock_response.usage = None

        patch_session, patch_factory, mock_bg_db = _patch_compress_bg_memory(sqlite_memory)

        with (
            patch(
                "agentpal.agents.personal_assistant._build_model",
                return_value=AsyncMock(return_value=mock_response),
            ),
            patch(
                "agentpal.agents.personal_assistant._extract_text",
                return_value="【对话摘要】测试",
            ),
            patch.object(writer, "_flush", new_callable=AsyncMock),
            patch_session,
            patch_factory,
        ):
            MemoryWriter._active_compressions.add("test-session")
            await writer._compress("test-session", sqlite_memory, mock_ws_manager, model_config)

        all_msgs = await sqlite_memory.get_recent("test-session", limit=20)
        # 前 2 条已经是 compressed，第 3-4 条（索引 2, 3）是新标记的
        # 最后 6 条（索引 4-9）不被标记
        newly_compressed = [
            m for m in all_msgs
            if (m.metadata or {}).get("compressed") and m.content.startswith("msg-")
        ]
        # 只有 msg-2 和 msg-3 被新标记（索引 2, 3 是旧消息中未压缩的）
        assert len(newly_compressed) == 2
