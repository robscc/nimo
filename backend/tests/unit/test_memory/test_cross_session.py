"""跨 session 搜索与权限模型测试。"""

from __future__ import annotations

import pytest

from agentpal.memory.base import MemoryAccessLevel, MemoryScope
from agentpal.memory.buffer import BufferMemory
from agentpal.memory.hybrid import HybridMemory
from agentpal.memory.sqlite import SQLiteMemory
from tests.conftest import make_msg


# ── MemoryScope 测试 ──────────────────────────────────────


class TestMemoryScope:
    def test_session_level(self):
        scope = MemoryScope(session_id="s1")
        assert scope.access_level == MemoryAccessLevel.SESSION

    def test_user_level(self):
        scope = MemoryScope(user_id="u1")
        assert scope.access_level == MemoryAccessLevel.USER

    def test_channel_level(self):
        scope = MemoryScope(channel="web")
        assert scope.access_level == MemoryAccessLevel.CHANNEL

    def test_global_level(self):
        scope = MemoryScope(global_access=True)
        assert scope.access_level == MemoryAccessLevel.GLOBAL

    def test_priority_session_over_user(self):
        """session_id 优先于 user_id。"""
        scope = MemoryScope(session_id="s1", user_id="u1")
        assert scope.access_level == MemoryAccessLevel.SESSION

    def test_validate_empty_raises(self):
        scope = MemoryScope()
        with pytest.raises(ValueError, match="至少需要指定"):
            scope.validate()

    def test_validate_with_session_ok(self):
        scope = MemoryScope(session_id="s1")
        scope.validate()  # 不应抛异常

    def test_validate_with_global_ok(self):
        scope = MemoryScope(global_access=True)
        scope.validate()


# ── SQLiteMemory 跨 session 搜索 ─────────────────────────


class TestSQLiteCrossSessionSearch:
    @pytest.mark.asyncio
    async def test_cross_session_search_by_user(self, sqlite_memory: SQLiteMemory):
        """按 user_id 跨 session 搜索。"""
        await sqlite_memory.add(make_msg("用户A的天气消息", session_id="s1", user_id="userA", channel="web"))
        await sqlite_memory.add(make_msg("用户A的日程消息", session_id="s2", user_id="userA", channel="web"))
        await sqlite_memory.add(make_msg("用户B的天气消息", session_id="s3", user_id="userB", channel="web"))

        scope = MemoryScope(user_id="userA")
        results = await sqlite_memory.cross_session_search(scope, "消息")
        assert len(results) == 2
        assert all(r.user_id == "userA" for r in results)

    @pytest.mark.asyncio
    async def test_cross_session_search_by_channel(self, sqlite_memory: SQLiteMemory):
        """按 channel 跨 session 搜索。"""
        await sqlite_memory.add(make_msg("web消息", session_id="s1", channel="web"))
        await sqlite_memory.add(make_msg("dingtalk消息", session_id="s2", channel="dingtalk"))
        await sqlite_memory.add(make_msg("web另一条消息", session_id="s3", channel="web"))

        scope = MemoryScope(channel="web")
        results = await sqlite_memory.cross_session_search(scope, "消息")
        assert len(results) == 2
        assert all(r.channel == "web" for r in results)

    @pytest.mark.asyncio
    async def test_cross_session_search_global(self, sqlite_memory: SQLiteMemory):
        """全局搜索。"""
        await sqlite_memory.add(make_msg("全局消息1", session_id="s1", user_id="u1"))
        await sqlite_memory.add(make_msg("全局消息2", session_id="s2", user_id="u2"))

        scope = MemoryScope(global_access=True)
        results = await sqlite_memory.cross_session_search(scope, "全局消息")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_cross_session_search_session_fallback(self, sqlite_memory: SQLiteMemory):
        """指定 session_id 时回退到单 session 搜索。"""
        await sqlite_memory.add(make_msg("session1消息", session_id="s1"))
        await sqlite_memory.add(make_msg("session2消息", session_id="s2"))

        scope = MemoryScope(session_id="s1")
        results = await sqlite_memory.cross_session_search(scope, "消息")
        assert len(results) == 1
        assert results[0].session_id == "s1"

    @pytest.mark.asyncio
    async def test_cross_session_search_no_match(self, sqlite_memory: SQLiteMemory):
        """无匹配结果。"""
        await sqlite_memory.add(make_msg("hello world", session_id="s1", user_id="u1"))

        scope = MemoryScope(user_id="u1")
        results = await sqlite_memory.cross_session_search(scope, "不存在的关键词XYZ")
        assert results == []

    @pytest.mark.asyncio
    async def test_cross_session_search_limit(self, sqlite_memory: SQLiteMemory):
        """limit 限制。"""
        for i in range(10):
            await sqlite_memory.add(make_msg(f"消息{i}", session_id=f"s{i}", user_id="u1"))

        scope = MemoryScope(user_id="u1")
        results = await sqlite_memory.cross_session_search(scope, "消息", limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_cross_session_search_invalid_scope(self, sqlite_memory: SQLiteMemory):
        """无效的 scope 应抛出 ValueError。"""
        scope = MemoryScope()  # 空 scope
        with pytest.raises(ValueError):
            await sqlite_memory.cross_session_search(scope, "test")


# ── BufferMemory 跨 session 搜索 ─────────────────────────


class TestBufferCrossSessionSearch:
    @pytest.mark.asyncio
    async def test_cross_session_search_by_user(self):
        mem = BufferMemory(max_size=20)
        await mem.add(make_msg("用户A消息1", session_id="s1", user_id="userA"))
        await mem.add(make_msg("用户A消息2", session_id="s2", user_id="userA"))
        await mem.add(make_msg("用户B消息", session_id="s3", user_id="userB"))

        scope = MemoryScope(user_id="userA")
        results = await mem.cross_session_search(scope, "消息")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_cross_session_search_by_channel(self):
        mem = BufferMemory(max_size=20)
        await mem.add(make_msg("web消息", session_id="s1", channel="web"))
        await mem.add(make_msg("dt消息", session_id="s2", channel="dingtalk"))

        scope = MemoryScope(channel="web")
        results = await mem.cross_session_search(scope, "消息")
        assert len(results) == 1
        assert results[0].channel == "web"

    @pytest.mark.asyncio
    async def test_cross_session_search_session_fallback(self):
        mem = BufferMemory(max_size=20)
        await mem.add(make_msg("消息1", session_id="s1"))
        await mem.add(make_msg("消息2", session_id="s2"))

        scope = MemoryScope(session_id="s1")
        results = await mem.cross_session_search(scope, "消息")
        assert len(results) == 1


# ── HybridMemory 跨 session 搜索 ─────────────────────────


class TestHybridCrossSessionSearch:
    @pytest.mark.asyncio
    async def test_cross_session_search_delegates_to_sqlite(self, hybrid_memory: HybridMemory):
        """HybridMemory 的跨 session 搜索应代理到 SQLite 层。"""
        await hybrid_memory.add(make_msg("消息1", session_id="s1", user_id="u1"))
        await hybrid_memory.add(make_msg("消息2", session_id="s2", user_id="u1"))

        scope = MemoryScope(user_id="u1")
        results = await hybrid_memory.cross_session_search(scope, "消息")
        assert len(results) == 2


# ── MemoryMessage 新字段测试 ──────────────────────────────


class TestMemoryMessageNewFields:
    def test_default_memory_type(self):
        msg = make_msg("hello")
        assert msg.memory_type == "conversation"

    def test_custom_memory_type(self):
        msg = make_msg("hello", memory_type="personal")
        assert msg.memory_type == "personal"

    def test_user_id_and_channel(self):
        msg = make_msg("hello", user_id="u1", channel="web")
        assert msg.user_id == "u1"
        assert msg.channel == "web"

    def test_to_agentscope_msg_unchanged(self):
        """新字段不影响 to_agentscope_msg 输出。"""
        msg = make_msg("hello", user_id="u1", channel="web", memory_type="personal")
        result = msg.to_agentscope_msg()
        assert result == {"role": "user", "content": "hello", "name": "user"}
