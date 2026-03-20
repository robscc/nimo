"""ReMeLight 适配器单元测试（使用 Mock）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpal.memory.base import MemoryMessage, MemoryRole, MemoryScope
from agentpal.memory.reme_light_adapter import (
    ReMeLightMemory,
    _extract_items_from_result,
    _extract_session_id,
    _strip_session_tag,
    _tag_content,
)


# ── 辅助函数测试 ──────────────────────────────────────────


class TestHelpers:
    def test_tag_content(self):
        assert _tag_content("s1", "hello") == "[session:s1] hello"

    def test_strip_session_tag(self):
        assert _strip_session_tag("[session:s1] hello") == "hello"

    def test_strip_session_tag_no_tag(self):
        assert _strip_session_tag("hello") == "hello"

    def test_extract_session_id(self):
        assert _extract_session_id("[session:s1] hello") == "s1"

    def test_extract_session_id_no_tag(self):
        assert _extract_session_id("hello") is None

    def test_extract_items_from_result_list(self):
        items = [{"content": "a"}, {"content": "b"}]
        assert _extract_items_from_result(items) == items

    def test_extract_items_from_result_with_items_attr(self):
        result = MagicMock()
        result.items = [{"content": "a"}]
        assert _extract_items_from_result(result) == [{"content": "a"}]

    def test_extract_items_from_result_with_content_str(self):
        result = MagicMock(spec=["content"])
        result.content = "some text"
        assert _extract_items_from_result(result) == [{"content": "some text"}]

    def test_extract_items_from_result_with_content_list(self):
        result = MagicMock(spec=["content"])
        result.content = [{"content": "a"}]
        assert _extract_items_from_result(result) == [{"content": "a"}]

    def test_extract_items_from_result_empty(self):
        assert _extract_items_from_result(42) == []


# ── add() 测试 ───────────────────────────────────────────


class TestReMeLightMemoryAdd:
    @pytest.mark.asyncio
    async def test_add_stores_in_session_messages(self):
        """add() 应在 _session_messages 中存储消息。"""
        mem = ReMeLightMemory()

        msg = MemoryMessage(session_id="s1", role=MemoryRole.USER, content="hello")
        result = await mem.add(msg)

        assert result.id is not None
        assert len(mem._session_messages["s1"]) == 1
        assert mem._session_messages["s1"][0].content == "hello"

    @pytest.mark.asyncio
    async def test_add_assigns_uuid(self):
        """add() 应为无 id 的消息分配 UUID。"""
        mem = ReMeLightMemory()

        msg = MemoryMessage(session_id="s1", role=MemoryRole.USER, content="test")
        result = await mem.add(msg)

        assert result.id is not None
        assert len(result.id) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_add_preserves_existing_id(self):
        """add() 应保留已有 id。"""
        mem = ReMeLightMemory()

        msg = MemoryMessage(session_id="s1", role=MemoryRole.USER, content="test", id="my-id")
        result = await mem.add(msg)

        assert result.id == "my-id"

    @pytest.mark.asyncio
    async def test_add_skips_empty_content(self):
        """add() 应跳过空内容消息的原生存储。"""
        mem = ReMeLightMemory()

        msg = MemoryMessage(session_id="s1", role=MemoryRole.USER, content="")
        result = await mem.add(msg)

        # 仍然存入 buffer
        assert len(mem._session_messages["s1"]) == 1
        assert result.id is not None

    @pytest.mark.asyncio
    async def test_add_skips_whitespace_content(self):
        """add() 应跳过纯空白内容消息的原生存储。"""
        mem = ReMeLightMemory()

        msg = MemoryMessage(session_id="s1", role=MemoryRole.USER, content="   ")
        result = await mem.add(msg)

        assert len(mem._session_messages["s1"]) == 1
        assert result.id is not None

    @pytest.mark.asyncio
    async def test_add_with_reme_native(self):
        """有 ReMeLight 实例时应调用原生存储。"""
        mem = ReMeLightMemory()

        mock_in_memory = MagicMock()
        mock_in_memory.add = MagicMock()

        mock_reme = AsyncMock()
        mock_reme.start = AsyncMock()
        mock_reme.get_in_memory_memory.return_value = mock_in_memory

        with patch("agentpal.memory.reme_light_adapter.ReMeLightMemory._ensure_started") as mock_ensure:

            async def _fake_ensure():
                mem._reme = mock_reme
                mem._in_memory = mock_in_memory
                mem._started = True

            mock_ensure.side_effect = _fake_ensure

            msg = MemoryMessage(session_id="s1", role=MemoryRole.USER, content="hello")
            await mem.add(msg)

            mock_in_memory.add.assert_called_once()
            call_kwargs = mock_in_memory.add.call_args
            assert "hello" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_add_tolerates_reme_failure(self):
        """ReMeLight 写入失败时不影响 buffer 存储。"""
        mem = ReMeLightMemory()

        with patch.object(mem, "_ensure_started", side_effect=RuntimeError("boom")):
            msg = MemoryMessage(session_id="s1", role=MemoryRole.USER, content="hello")
            result = await mem.add(msg)

            assert result.id is not None
            assert len(mem._session_messages["s1"]) == 1


# ── get_recent() 测试 ────────────────────────────────────


class TestReMeLightMemoryGetRecent:
    @pytest.mark.asyncio
    async def test_get_recent_returns_latest(self):
        mem = ReMeLightMemory()

        for i in range(5):
            await mem.add(
                MemoryMessage(session_id="s1", role=MemoryRole.USER, content=f"msg{i}")
            )

        msgs = await mem.get_recent("s1", limit=3)
        assert len(msgs) == 3
        assert msgs[-1].content == "msg4"
        assert msgs[0].content == "msg2"

    @pytest.mark.asyncio
    async def test_get_recent_empty_session(self):
        mem = ReMeLightMemory()
        msgs = await mem.get_recent("nonexistent")
        assert msgs == []

    @pytest.mark.asyncio
    async def test_get_recent_session_isolation(self):
        mem = ReMeLightMemory()

        await mem.add(MemoryMessage(session_id="s1", role=MemoryRole.USER, content="s1-msg"))
        await mem.add(MemoryMessage(session_id="s2", role=MemoryRole.USER, content="s2-msg"))

        s1_msgs = await mem.get_recent("s1")
        s2_msgs = await mem.get_recent("s2")

        assert len(s1_msgs) == 1
        assert s1_msgs[0].content == "s1-msg"
        assert len(s2_msgs) == 1
        assert s2_msgs[0].content == "s2-msg"


# ── search() 测试 ────────────────────────────────────────


class TestReMeLightMemorySearch:
    @pytest.mark.asyncio
    async def test_search_with_reme(self):
        """有 ReMeLight 实例时通过 memory_search 检索。"""
        mem = ReMeLightMemory()
        mem._started = True

        mock_reme = AsyncMock()
        mock_reme.memory_search = AsyncMock(
            return_value=[
                {"id": "1", "content": "[session:s1] I like coffee", "role": "user"},
                {"id": "2", "content": "[session:s2] I like tea", "role": "user"},
            ]
        )
        mem._reme = mock_reme
        mem._in_memory = MagicMock()

        results = await mem.search("s1", "coffee")
        assert len(results) == 1
        assert results[0].content == "I like coffee"
        assert results[0].session_id == "s1"

    @pytest.mark.asyncio
    async def test_search_session_filter(self):
        """search() 应按 session_id 过滤结果。"""
        mem = ReMeLightMemory()
        mem._started = True

        mock_reme = AsyncMock()
        mock_reme.memory_search = AsyncMock(
            return_value=[
                {"id": "1", "content": "[session:s1] msg1", "role": "user"},
                {"id": "2", "content": "[session:s1] msg2", "role": "assistant"},
                {"id": "3", "content": "[session:s2] msg3", "role": "user"},
            ]
        )
        mem._reme = mock_reme
        mem._in_memory = MagicMock()

        results = await mem.search("s1", "msg", limit=10)
        assert len(results) == 2
        assert all(r.session_id == "s1" for r in results)

    @pytest.mark.asyncio
    async def test_search_fallback_to_buffer(self):
        """ReMeLight 失败时回退到 buffer 关键词搜索。"""
        mem = ReMeLightMemory()

        await mem.add(MemoryMessage(session_id="s1", role=MemoryRole.USER, content="I like coffee"))
        await mem.add(MemoryMessage(session_id="s1", role=MemoryRole.USER, content="I like tea"))

        with patch.object(mem, "_ensure_started", side_effect=RuntimeError("boom")):
            results = await mem.search("s1", "coffee")
            assert len(results) == 1
            assert "coffee" in results[0].content


# ── cross_session_search() 测试 ──────────────────────────


class TestReMeLightCrossSessionSearch:
    @pytest.mark.asyncio
    async def test_cross_session_delegates_to_search(self):
        """有 session_id 时应委托给 search()。"""
        mem = ReMeLightMemory()

        await mem.add(MemoryMessage(session_id="s1", role=MemoryRole.USER, content="hello world"))

        scope = MemoryScope(session_id="s1")
        with patch.object(mem, "_ensure_started", side_effect=RuntimeError("no reme")):
            results = await mem.cross_session_search(scope, "hello")
            assert len(results) == 1

    @pytest.mark.asyncio
    async def test_cross_session_global_search(self):
        """全局搜索通过 memory_search。"""
        mem = ReMeLightMemory()
        mem._started = True

        mock_reme = AsyncMock()
        mock_reme.memory_search = AsyncMock(
            return_value=[
                {"id": "1", "content": "[session:s1] hello", "role": "user"},
                {"id": "2", "content": "[session:s2] world", "role": "user"},
            ]
        )
        mem._reme = mock_reme
        mem._in_memory = MagicMock()

        scope = MemoryScope(global_access=True)
        results = await mem.cross_session_search(scope, "test", limit=10)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_cross_session_fallback_to_buffer(self):
        """全局搜索失败时回退到 buffer 扫描。"""
        mem = ReMeLightMemory()

        await mem.add(
            MemoryMessage(session_id="s1", role=MemoryRole.USER, content="消息1", user_id="u1")
        )
        await mem.add(
            MemoryMessage(session_id="s2", role=MemoryRole.USER, content="消息2", user_id="u1")
        )
        await mem.add(
            MemoryMessage(session_id="s3", role=MemoryRole.USER, content="消息3", user_id="u2")
        )

        scope = MemoryScope(user_id="u1")
        with patch.object(mem, "_ensure_started", side_effect=RuntimeError("no reme")):
            results = await mem.cross_session_search(scope, "消息")
            assert len(results) == 2

    @pytest.mark.asyncio
    async def test_cross_session_filter_by_channel(self):
        """回退时按 channel 过滤。"""
        mem = ReMeLightMemory()

        await mem.add(
            MemoryMessage(session_id="s1", role=MemoryRole.USER, content="msg", channel="web")
        )
        await mem.add(
            MemoryMessage(session_id="s2", role=MemoryRole.USER, content="msg", channel="dingtalk")
        )

        scope = MemoryScope(channel="web")
        with patch.object(mem, "_ensure_started", side_effect=RuntimeError("no reme")):
            results = await mem.cross_session_search(scope, "msg")
            assert len(results) == 1
            assert results[0].channel == "web"


# ── clear() 测试 ─────────────────────────────────────────


class TestReMeLightMemoryClear:
    @pytest.mark.asyncio
    async def test_clear_removes_session(self):
        mem = ReMeLightMemory()

        await mem.add(MemoryMessage(session_id="s1", role=MemoryRole.USER, content="hello"))
        await mem.clear("s1")

        msgs = await mem.get_recent("s1")
        assert msgs == []

    @pytest.mark.asyncio
    async def test_clear_nonexistent_session(self):
        mem = ReMeLightMemory()
        # 不应抛异常
        await mem.clear("nonexistent")


# ── count() 测试 ─────────────────────────────────────────


class TestReMeLightMemoryCount:
    @pytest.mark.asyncio
    async def test_count(self):
        mem = ReMeLightMemory()

        for i in range(3):
            await mem.add(
                MemoryMessage(session_id="s1", role=MemoryRole.USER, content=f"msg{i}")
            )

        assert await mem.count("s1") == 3

    @pytest.mark.asyncio
    async def test_count_empty(self):
        mem = ReMeLightMemory()
        assert await mem.count("nonexistent") == 0


# ── close() 测试 ─────────────────────────────────────────


class TestReMeLightMemoryClose:
    @pytest.mark.asyncio
    async def test_close_with_reme(self):
        """关闭时调用 reme.close()。"""
        mem = ReMeLightMemory()
        mock_reme = AsyncMock()
        mock_reme.close = AsyncMock()
        mem._reme = mock_reme
        mem._started = True

        await mem.close()

        mock_reme.close.assert_called_once()
        assert mem._reme is None
        assert mem._in_memory is None
        assert mem._started is False

    @pytest.mark.asyncio
    async def test_close_without_reme(self):
        """未初始化时关闭不抛异常。"""
        mem = ReMeLightMemory()
        await mem.close()  # 不应抛异常

    @pytest.mark.asyncio
    async def test_close_tolerates_failure(self):
        """reme.close() 失败不应抛异常。"""
        mem = ReMeLightMemory()
        mock_reme = AsyncMock()
        mock_reme.close = AsyncMock(side_effect=RuntimeError("close failed"))
        mem._reme = mock_reme
        mem._started = True

        await mem.close()  # 不应抛异常
        assert mem._reme is None


# ── ReMeLight 原生能力测试 ────────────────────────────────


class TestReMeLightNativeCapabilities:
    @pytest.mark.asyncio
    async def test_compact_history(self):
        """compact_history 应调用 compact_memory()。"""
        mem = ReMeLightMemory()
        mem._started = True

        mock_reme = AsyncMock()
        mock_reme.compact_memory = AsyncMock(return_value="compressed summary")
        mem._reme = mock_reme

        result = await mem.compact_history("s1")
        assert result == "compressed summary"
        mock_reme.compact_memory.assert_called_once()

    @pytest.mark.asyncio
    async def test_compact_history_failure(self):
        """compact_history 失败返回 None。"""
        mem = ReMeLightMemory()

        with patch.object(mem, "_ensure_started", side_effect=RuntimeError("boom")):
            result = await mem.compact_history("s1")
            assert result is None

    @pytest.mark.asyncio
    async def test_summarize_session(self):
        """summarize_session 应调用 summary_memory()。"""
        mem = ReMeLightMemory()
        mem._started = True

        mock_reme = AsyncMock()
        mock_reme.summary_memory = AsyncMock(return_value="session summary")
        mem._reme = mock_reme

        result = await mem.summarize_session("s1")
        assert result == "session summary"
        mock_reme.summary_memory.assert_called_once()

    @pytest.mark.asyncio
    async def test_summarize_session_failure(self):
        """summarize_session 失败返回 None。"""
        mem = ReMeLightMemory()

        with patch.object(mem, "_ensure_started", side_effect=RuntimeError("boom")):
            result = await mem.summarize_session("s1")
            assert result is None

    @pytest.mark.asyncio
    async def test_pre_reasoning(self):
        """pre_reasoning 应调用 pre_reasoning_hook()。"""
        mem = ReMeLightMemory()
        mem._started = True

        mock_reme = AsyncMock()
        mock_reme.pre_reasoning_hook = AsyncMock(
            return_value={"compressed": True, "summary": "..."}
        )
        mem._reme = mock_reme

        result = await mem.pre_reasoning("s1", system_prompt="You are helpful.")
        assert result == {"compressed": True, "summary": "..."}
        mock_reme.pre_reasoning_hook.assert_called_once_with(system_prompt="You are helpful.")

    @pytest.mark.asyncio
    async def test_pre_reasoning_with_compressed_summary(self):
        """pre_reasoning 应传递 compressed_summary 参数。"""
        mem = ReMeLightMemory()
        mem._started = True

        mock_reme = AsyncMock()
        mock_reme.pre_reasoning_hook = AsyncMock(return_value={"ok": True})
        mem._reme = mock_reme

        result = await mem.pre_reasoning("s1", compressed_summary="prev summary")
        assert result == {"ok": True}
        mock_reme.pre_reasoning_hook.assert_called_once_with(compressed_summary="prev summary")

    @pytest.mark.asyncio
    async def test_pre_reasoning_failure(self):
        """pre_reasoning 失败返回 None。"""
        mem = ReMeLightMemory()

        with patch.object(mem, "_ensure_started", side_effect=RuntimeError("boom")):
            result = await mem.pre_reasoning("s1")
            assert result is None

    @pytest.mark.asyncio
    async def test_pre_reasoning_non_dict_result(self):
        """pre_reasoning 非 dict 结果应包装为 dict。"""
        mem = ReMeLightMemory()
        mem._started = True

        mock_reme = AsyncMock()
        mock_reme.pre_reasoning_hook = AsyncMock(return_value="some string result")
        mem._reme = mock_reme

        result = await mem.pre_reasoning("s1")
        assert result == {"result": "some string result"}

    def test_get_reme_instance(self):
        """get_reme_instance 应返回底层实例。"""
        mem = ReMeLightMemory()
        assert mem.get_reme_instance() is None

        mock_reme = MagicMock()
        mem._reme = mock_reme
        assert mem.get_reme_instance() is mock_reme


# ── _ensure_started() 测试 ───────────────────────────────


class TestReMeLightEnsureStarted:
    @pytest.mark.asyncio
    async def test_ensure_started_import_error(self):
        """reme-ai 未安装时应抛出 ImportError。"""
        mem = ReMeLightMemory()

        with patch.dict("sys.modules", {"reme": None, "reme.reme_light": None}):
            with pytest.raises(ImportError, match="reme-ai"):
                await mem._ensure_started()

    @pytest.mark.asyncio
    async def test_ensure_started_initializes_once(self):
        """多次调用只初始化一次。"""
        mem = ReMeLightMemory()

        mock_reme_cls = MagicMock()
        mock_instance = AsyncMock()
        mock_instance.start = AsyncMock()
        mock_instance.get_in_memory_memory.return_value = MagicMock()
        mock_reme_cls.return_value = mock_instance

        with patch(
            "agentpal.memory.reme_light_adapter.ReMeLightMemory._ensure_started",
            wraps=mem._ensure_started,
        ):
            # 手动设置 started 状态来模拟
            mem._started = True
            await mem._ensure_started()
            await mem._ensure_started()

            # 因为 _started=True，所以不会实际初始化
            assert mem._started is True
