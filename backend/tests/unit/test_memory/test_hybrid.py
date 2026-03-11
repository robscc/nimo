"""HybridMemory 单元测试。"""

from __future__ import annotations

import pytest

from agentpal.memory.hybrid import HybridMemory
from agentpal.memory.buffer import BufferMemory
from agentpal.memory.sqlite import SQLiteMemory
from tests.conftest import make_msg


class TestHybridMemoryAdd:
    @pytest.mark.asyncio
    async def test_add_writes_to_both_layers(self, hybrid_memory: HybridMemory):
        msg = await hybrid_memory.add(make_msg("dual write"))

        # SQLite 层
        sqlite_msgs = await hybrid_memory._persistent.get_recent("test-session")
        assert any(m.content == "dual write" for m in sqlite_msgs)

        # Buffer 层
        buf_msgs = await hybrid_memory._buffer.get_recent("test-session")
        assert any(m.content == "dual write" for m in buf_msgs)

    @pytest.mark.asyncio
    async def test_add_returns_id(self, hybrid_memory: HybridMemory):
        msg = await hybrid_memory.add(make_msg("check id"))
        assert msg.id is not None


class TestHybridMemoryGetRecent:
    @pytest.mark.asyncio
    async def test_get_recent_served_from_buffer_after_warm(self, hybrid_memory: HybridMemory):
        for i in range(5):
            await hybrid_memory.add(make_msg(f"msg-{i}"))

        # 清空 buffer 模拟冷启动
        await hybrid_memory._buffer.clear("test-session")
        hybrid_memory._warmed_sessions.discard("test-session")

        msgs = await hybrid_memory.get_recent("test-session", limit=5)
        assert len(msgs) == 5

    @pytest.mark.asyncio
    async def test_get_recent_limit(self, hybrid_memory: HybridMemory):
        for i in range(8):
            await hybrid_memory.add(make_msg(f"msg-{i}"))
        msgs = await hybrid_memory.get_recent("test-session", limit=3)
        assert len(msgs) == 3

    @pytest.mark.asyncio
    async def test_get_recent_no_duplicate_on_warm(self, hybrid_memory: HybridMemory):
        """预热后再读，不应出现重复消息。"""
        for i in range(5):
            await hybrid_memory.add(make_msg(f"msg-{i}"))

        # 重置 warmed 状态，触发再次预热
        hybrid_memory._warmed_sessions.discard("test-session")

        msgs = await hybrid_memory.get_recent("test-session", limit=20)
        ids = [m.id for m in msgs if m.id]
        assert len(ids) == len(set(ids)), "存在重复消息 ID"


class TestHybridMemoryClear:
    @pytest.mark.asyncio
    async def test_clear_both_layers(self, hybrid_memory: HybridMemory):
        await hybrid_memory.add(make_msg("to be cleared"))
        await hybrid_memory.clear("test-session")

        buf_msgs = await hybrid_memory._buffer.get_recent("test-session")
        sqlite_msgs = await hybrid_memory._persistent.get_recent("test-session")
        assert buf_msgs == []
        assert sqlite_msgs == []

    @pytest.mark.asyncio
    async def test_clear_resets_warm_state(self, hybrid_memory: HybridMemory):
        await hybrid_memory.add(make_msg("msg"))
        await hybrid_memory.get_recent("test-session")  # 触发预热
        assert "test-session" in hybrid_memory._warmed_sessions
        await hybrid_memory.clear("test-session")
        assert "test-session" not in hybrid_memory._warmed_sessions


class TestHybridMemorySearch:
    @pytest.mark.asyncio
    async def test_search_delegates_to_sqlite(self, hybrid_memory: HybridMemory):
        await hybrid_memory.add(make_msg("机器学习很有趣"))
        await hybrid_memory.add(make_msg("今天吃了午饭"))
        results = await hybrid_memory.search("test-session", "机器学习")
        assert len(results) == 1
        assert "机器学习" in results[0].content


class TestHybridMemoryCount:
    @pytest.mark.asyncio
    async def test_count_from_sqlite(self, hybrid_memory: HybridMemory):
        assert await hybrid_memory.count("test-session") == 0
        for i in range(3):
            await hybrid_memory.add(make_msg(f"msg-{i}"))
        assert await hybrid_memory.count("test-session") == 3
