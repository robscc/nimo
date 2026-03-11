"""SQLiteMemory 单元测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.memory.base import MemoryRole
from agentpal.memory.sqlite import SQLiteMemory
from tests.conftest import make_msg


class TestSQLiteMemoryAdd:
    @pytest.mark.asyncio
    async def test_add_returns_message_with_id(self, sqlite_memory: SQLiteMemory):
        msg = await sqlite_memory.add(make_msg("hello sqlite"))
        assert msg.id is not None
        assert msg.content == "hello sqlite"

    @pytest.mark.asyncio
    async def test_add_persists_role(self, sqlite_memory: SQLiteMemory):
        msg = make_msg("assistant says hi", role=MemoryRole.ASSISTANT)
        saved = await sqlite_memory.add(msg)
        msgs = await sqlite_memory.get_recent("test-session")
        assert msgs[0].role == MemoryRole.ASSISTANT


class TestSQLiteMemoryGetRecent:
    @pytest.mark.asyncio
    async def test_get_recent_ascending_order(self, sqlite_memory: SQLiteMemory):
        for i in range(5):
            await sqlite_memory.add(make_msg(f"msg-{i}"))
        msgs = await sqlite_memory.get_recent("test-session")
        contents = [m.content for m in msgs]
        assert contents == [f"msg-{i}" for i in range(5)]

    @pytest.mark.asyncio
    async def test_get_recent_limit(self, sqlite_memory: SQLiteMemory):
        for i in range(10):
            await sqlite_memory.add(make_msg(f"msg-{i}"))
        msgs = await sqlite_memory.get_recent("test-session", limit=3)
        assert len(msgs) == 3
        assert msgs[-1].content == "msg-9"

    @pytest.mark.asyncio
    async def test_get_recent_session_isolation(self, sqlite_memory: SQLiteMemory):
        await sqlite_memory.add(make_msg("A", session_id="s-a"))
        await sqlite_memory.add(make_msg("B", session_id="s-b"))
        sa = await sqlite_memory.get_recent("s-a")
        sb = await sqlite_memory.get_recent("s-b")
        assert sa[0].content == "A"
        assert sb[0].content == "B"


class TestSQLiteMemorySearch:
    @pytest.mark.asyncio
    async def test_search_finds_matching(self, sqlite_memory: SQLiteMemory):
        await sqlite_memory.add(make_msg("今天天气很好"))
        await sqlite_memory.add(make_msg("明天可能下雨"))
        await sqlite_memory.add(make_msg("周末一起去爬山"))
        results = await sqlite_memory.search("test-session", "天气")
        assert len(results) == 1
        assert "天气" in results[0].content

    @pytest.mark.asyncio
    async def test_search_no_match_returns_empty(self, sqlite_memory: SQLiteMemory):
        await sqlite_memory.add(make_msg("hello world"))
        results = await sqlite_memory.search("test-session", "不存在的关键词XYZ")
        assert results == []


class TestSQLiteMemoryClear:
    @pytest.mark.asyncio
    async def test_clear_removes_all(self, sqlite_memory: SQLiteMemory):
        for i in range(5):
            await sqlite_memory.add(make_msg(f"msg-{i}"))
        await sqlite_memory.clear("test-session")
        msgs = await sqlite_memory.get_recent("test-session")
        assert msgs == []

    @pytest.mark.asyncio
    async def test_clear_session_isolation(self, sqlite_memory: SQLiteMemory):
        await sqlite_memory.add(make_msg("keep", session_id="s-keep"))
        await sqlite_memory.add(make_msg("delete", session_id="s-del"))
        await sqlite_memory.clear("s-del")
        assert len(await sqlite_memory.get_recent("s-keep")) == 1
        assert len(await sqlite_memory.get_recent("s-del")) == 0


class TestSQLiteMemoryCount:
    @pytest.mark.asyncio
    async def test_count_accurate(self, sqlite_memory: SQLiteMemory):
        assert await sqlite_memory.count("test-session") == 0
        for i in range(4):
            await sqlite_memory.add(make_msg(f"msg-{i}"))
        assert await sqlite_memory.count("test-session") == 4
