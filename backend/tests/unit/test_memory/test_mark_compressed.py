"""mark_compressed() 方法单元测试。"""

from __future__ import annotations

import pytest

from agentpal.memory.base import MemoryMessage, MemoryRole
from agentpal.memory.buffer import BufferMemory
from agentpal.memory.hybrid import HybridMemory
from agentpal.memory.sqlite import SQLiteMemory
from tests.conftest import make_msg


class TestSQLiteMarkCompressed:
    """SQLiteMemory.mark_compressed 测试。"""

    @pytest.mark.asyncio
    async def test_sqlite_mark_compressed(self, sqlite_memory: SQLiteMemory):
        """批量标记多条消息为 compressed=true。"""
        msgs = []
        for i in range(5):
            m = await sqlite_memory.add(make_msg(f"msg-{i}"))
            msgs.append(m)

        # 标记前 3 条
        ids_to_mark = [m.id for m in msgs[:3]]
        count = await sqlite_memory.mark_compressed("test-session", ids_to_mark)
        assert count == 3

        # 验证标记结果
        all_msgs = await sqlite_memory.get_recent("test-session", limit=10)
        for m in all_msgs:
            if m.id in ids_to_mark:
                assert m.metadata.get("compressed") is True
            else:
                assert not m.metadata.get("compressed")

    @pytest.mark.asyncio
    async def test_sqlite_mark_compressed_empty_list(self, sqlite_memory: SQLiteMemory):
        """空 ID 列表应返回 0。"""
        count = await sqlite_memory.mark_compressed("test-session", [])
        assert count == 0

    @pytest.mark.asyncio
    async def test_sqlite_mark_compressed_nonexistent_ids(self, sqlite_memory: SQLiteMemory):
        """不存在的 ID 不被标记。"""
        await sqlite_memory.add(make_msg("msg-0"))
        count = await sqlite_memory.mark_compressed("test-session", ["nonexistent-id"])
        assert count == 0

    @pytest.mark.asyncio
    async def test_sqlite_mark_compressed_preserves_existing_meta(self, sqlite_memory: SQLiteMemory):
        """标记时保留 meta 中已有的字段。"""
        msg = MemoryMessage(
            session_id="test-session",
            role=MemoryRole.ASSISTANT,
            content="has meta",
            metadata={"thinking": "some thought", "tool_calls": []},
        )
        saved = await sqlite_memory.add(msg)
        count = await sqlite_memory.mark_compressed("test-session", [saved.id])
        assert count == 1

        msgs = await sqlite_memory.get_recent("test-session", limit=1)
        assert msgs[0].metadata.get("compressed") is True
        assert msgs[0].metadata.get("thinking") == "some thought"


class TestHybridMarkCompressed:
    """HybridMemory.mark_compressed 测试。"""

    @pytest.mark.asyncio
    async def test_hybrid_mark_compressed_delegates(self, hybrid_memory: HybridMemory):
        """HybridMemory 正确委托给 SQLiteMemory 并清除 Buffer 缓存。"""
        # 添加消息并触发 warm-up
        msgs = []
        for i in range(3):
            m = await hybrid_memory.add(make_msg(f"msg-{i}"))
            msgs.append(m)

        # 确认 session 已在 warmed set 中
        await hybrid_memory.get_recent("test-session")
        assert "test-session" in hybrid_memory._warmed_sessions

        # 标记压缩
        ids = [m.id for m in msgs[:2]]
        count = await hybrid_memory.mark_compressed("test-session", ids)
        assert count == 2

        # 验证 Buffer 缓存已清除，warmed_sessions 也被移除
        assert "test-session" not in hybrid_memory._warmed_sessions

    @pytest.mark.asyncio
    async def test_hybrid_mark_compressed_zero_no_clear(self, hybrid_memory: HybridMemory):
        """标记 0 条时不清除 Buffer。"""
        await hybrid_memory.add(make_msg("msg-0"))
        await hybrid_memory.get_recent("test-session")
        assert "test-session" in hybrid_memory._warmed_sessions

        count = await hybrid_memory.mark_compressed("test-session", ["nonexistent"])
        assert count == 0
        # Buffer 不应被清除
        assert "test-session" in hybrid_memory._warmed_sessions


class TestBaseMemoryMarkCompressed:
    """BaseMemory 默认实现测试。"""

    @pytest.mark.asyncio
    async def test_mark_compressed_noop_base(self, buffer_memory: BufferMemory):
        """BufferMemory（继承 BaseMemory 默认）应返回 0。"""
        count = await buffer_memory.mark_compressed("test-session", ["some-id"])
        assert count == 0
