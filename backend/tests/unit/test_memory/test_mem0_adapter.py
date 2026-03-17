"""mem0 适配器单元测试（使用 Mock）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpal.memory.base import MemoryRole, MemoryScope
from agentpal.memory.mem0_adapter import Mem0Memory


class TestMem0MemoryAdd:
    @pytest.mark.asyncio
    async def test_add_calls_mem0_api(self):
        """add() 应调用 mem0 的 add API。"""
        mock_client = AsyncMock()
        mock_client.add.return_value = {
            "results": [{"id": "mem0-id-1", "memory": "test"}]
        }

        mem = Mem0Memory()
        mem._client = mock_client

        from agentpal.memory.base import MemoryMessage
        msg = MemoryMessage(session_id="s1", role=MemoryRole.USER, content="hello world")
        result = await mem.add(msg)

        assert result.id == "mem0-id-1"
        mock_client.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_assigns_uuid_on_empty_result(self):
        """mem0 返回空结果时应分配 UUID。"""
        mock_client = AsyncMock()
        mock_client.add.return_value = {"results": []}

        mem = Mem0Memory()
        mem._client = mock_client

        from agentpal.memory.base import MemoryMessage
        msg = MemoryMessage(session_id="s1", role=MemoryRole.USER, content="hello")
        result = await mem.add(msg)

        assert result.id is not None

    @pytest.mark.asyncio
    async def test_add_with_user_id_and_channel(self):
        """add() 传入 user_id 和 channel 应映射到 mem0 的 user_id 和 agent_id。"""
        mock_client = AsyncMock()
        mock_client.add.return_value = {"results": [{"id": "id1"}]}

        mem = Mem0Memory()
        mem._client = mock_client

        from agentpal.memory.base import MemoryMessage
        msg = MemoryMessage(
            session_id="s1", role=MemoryRole.USER, content="hello",
            user_id="u1", channel="web",
        )
        await mem.add(msg)

        call_kwargs = mock_client.add.call_args
        assert call_kwargs[1]["user_id"] == "u1"
        assert call_kwargs[1]["agent_id"] == "web"


class TestMem0MemoryGetRecent:
    @pytest.mark.asyncio
    async def test_get_recent_returns_messages(self):
        mock_client = AsyncMock()
        mock_client.get_all.return_value = {
            "results": [
                {"id": "1", "memory": "msg1", "run_id": "s1", "created_at": "2024-01-01T00:00:00Z"},
                {"id": "2", "memory": "msg2", "run_id": "s1", "created_at": "2024-01-01T00:01:00Z"},
            ]
        }

        mem = Mem0Memory()
        mem._client = mock_client

        msgs = await mem.get_recent("s1", limit=10)
        assert len(msgs) == 2
        assert msgs[0].content == "msg1"

    @pytest.mark.asyncio
    async def test_get_recent_empty(self):
        mock_client = AsyncMock()
        mock_client.get_all.return_value = {"results": []}

        mem = Mem0Memory()
        mem._client = mock_client

        msgs = await mem.get_recent("s1")
        assert msgs == []


class TestMem0MemorySearch:
    @pytest.mark.asyncio
    async def test_search_semantic(self):
        mock_client = AsyncMock()
        mock_client.search.return_value = {
            "results": [
                {"id": "1", "memory": "I love Python", "run_id": "s1", "score": 0.9},
            ]
        }

        mem = Mem0Memory()
        mem._client = mock_client

        results = await mem.search("s1", "programming language")
        assert len(results) == 1
        assert results[0].content == "I love Python"


class TestMem0CrossSessionSearch:
    @pytest.mark.asyncio
    async def test_cross_session_by_user(self):
        mock_client = AsyncMock()
        mock_client.search.return_value = {
            "results": [
                {"id": "1", "memory": "fact1", "user_id": "u1"},
                {"id": "2", "memory": "fact2", "user_id": "u1"},
            ]
        }

        mem = Mem0Memory()
        mem._client = mock_client

        scope = MemoryScope(user_id="u1")
        results = await mem.cross_session_search(scope, "test", limit=5)
        assert len(results) == 2

        # 验证 search 调用时传了 user_id
        call_kwargs = mock_client.search.call_args
        assert call_kwargs[1]["user_id"] == "u1"


class TestMem0MemoryClear:
    @pytest.mark.asyncio
    async def test_clear_calls_delete_all(self):
        mock_client = AsyncMock()

        mem = Mem0Memory()
        mem._client = mock_client

        await mem.clear("s1")
        mock_client.delete_all.assert_called_once_with(run_id="s1")


class TestMem0MemoryCount:
    @pytest.mark.asyncio
    async def test_count(self):
        mock_client = AsyncMock()
        mock_client.get_all.return_value = {
            "results": [{"id": "1"}, {"id": "2"}, {"id": "3"}]
        }

        mem = Mem0Memory()
        mem._client = mock_client

        count = await mem.count("s1")
        assert count == 3
