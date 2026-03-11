"""BufferMemory 单元测试。"""

from __future__ import annotations

import pytest

from agentpal.memory.base import MemoryMessage, MemoryRole
from agentpal.memory.buffer import BufferMemory
from tests.conftest import make_msg


class TestBufferMemoryAdd:
    @pytest.mark.asyncio
    async def test_add_assigns_id(self):
        mem = BufferMemory()
        msg = await mem.add(make_msg("hello"))
        assert msg.id is not None

    @pytest.mark.asyncio
    async def test_add_preserves_existing_id(self):
        mem = BufferMemory()
        original = make_msg("hello")
        original.id = "fixed-id"
        result = await mem.add(original)
        assert result.id == "fixed-id"

    @pytest.mark.asyncio
    async def test_add_multiple_sessions_isolated(self):
        mem = BufferMemory()
        await mem.add(make_msg("s1 msg", session_id="session-1"))
        await mem.add(make_msg("s2 msg", session_id="session-2"))
        s1 = await mem.get_recent("session-1")
        s2 = await mem.get_recent("session-2")
        assert len(s1) == 1
        assert len(s2) == 1
        assert s1[0].content == "s1 msg"
        assert s2[0].content == "s2 msg"


class TestBufferMemoryGetRecent:
    @pytest.mark.asyncio
    async def test_get_recent_returns_in_order(self):
        mem = BufferMemory()
        for i in range(5):
            await mem.add(make_msg(f"msg-{i}"))
        msgs = await mem.get_recent("test-session", limit=5)
        assert [m.content for m in msgs] == [f"msg-{i}" for i in range(5)]

    @pytest.mark.asyncio
    async def test_get_recent_respects_limit(self):
        mem = BufferMemory()
        for i in range(10):
            await mem.add(make_msg(f"msg-{i}"))
        msgs = await mem.get_recent("test-session", limit=3)
        assert len(msgs) == 3
        assert msgs[-1].content == "msg-9"

    @pytest.mark.asyncio
    async def test_get_recent_empty_session(self):
        mem = BufferMemory()
        msgs = await mem.get_recent("non-existent")
        assert msgs == []


class TestBufferMemorySliding:
    @pytest.mark.asyncio
    async def test_sliding_window_drops_oldest(self):
        mem = BufferMemory(max_size=3)
        for i in range(5):
            await mem.add(make_msg(f"msg-{i}"))
        msgs = await mem.get_recent("test-session", limit=10)
        assert len(msgs) == 3
        # 保留最后 3 条
        assert msgs[0].content == "msg-2"
        assert msgs[-1].content == "msg-4"


class TestBufferMemoryClear:
    @pytest.mark.asyncio
    async def test_clear_removes_all(self):
        mem = BufferMemory()
        for i in range(5):
            await mem.add(make_msg(f"msg-{i}"))
        await mem.clear("test-session")
        msgs = await mem.get_recent("test-session")
        assert msgs == []

    @pytest.mark.asyncio
    async def test_clear_only_affects_target_session(self):
        mem = BufferMemory()
        await mem.add(make_msg("keep", session_id="session-a"))
        await mem.add(make_msg("delete", session_id="session-b"))
        await mem.clear("session-b")
        assert len(await mem.get_recent("session-a")) == 1
        assert len(await mem.get_recent("session-b")) == 0


class TestBufferMemoryCount:
    @pytest.mark.asyncio
    async def test_count(self):
        mem = BufferMemory()
        assert await mem.count("test-session") == 0
        for i in range(3):
            await mem.add(make_msg(f"msg-{i}"))
        assert await mem.count("test-session") == 3


class TestBufferMemoryLoadFrom:
    @pytest.mark.asyncio
    async def test_load_from_preloads_messages(self):
        mem = BufferMemory(max_size=5)
        msgs = [make_msg(f"msg-{i}") for i in range(5)]
        for idx, m in enumerate(msgs):
            m.id = f"id-{idx}"
        mem.load_from("test-session", msgs)
        result = await mem.get_recent("test-session")
        assert len(result) == 5
