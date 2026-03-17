"""ReMe 适配器单元测试（使用 Mock）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpal.memory.base import MemoryRole, MemoryScope
from agentpal.memory.reme_adapter import ReMeMemory


class TestReMeMemoryAdd:
    @pytest.mark.asyncio
    async def test_add_stores_in_local_buffer(self):
        """add() 应在本地 buffer 中存储消息。"""
        mem = ReMeMemory()

        from agentpal.memory.base import MemoryMessage
        msg = MemoryMessage(session_id="s1", role=MemoryRole.USER, content="hello")
        result = await mem.add(msg)

        assert result.id is not None
        assert len(mem._local_buffer["s1"]) == 1

    @pytest.mark.asyncio
    async def test_add_with_server_url(self):
        """有 server_url 时应尝试调用 ReMe API。"""
        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(status_code=200)

        mem = ReMeMemory(server_url="http://localhost:8080")
        mem._client = mock_client

        from agentpal.memory.base import MemoryMessage
        msg = MemoryMessage(session_id="s1", role=MemoryRole.USER, content="hello")
        await mem.add(msg)

        mock_client.post.assert_called_once()
        assert "/store_personal_memory" in str(mock_client.post.call_args)


class TestReMeMemoryGetRecent:
    @pytest.mark.asyncio
    async def test_get_recent_from_buffer(self):
        mem = ReMeMemory()

        from agentpal.memory.base import MemoryMessage
        for i in range(5):
            await mem.add(
                MemoryMessage(session_id="s1", role=MemoryRole.USER, content=f"msg{i}")
            )

        msgs = await mem.get_recent("s1", limit=3)
        assert len(msgs) == 3
        assert msgs[-1].content == "msg4"

    @pytest.mark.asyncio
    async def test_get_recent_empty(self):
        mem = ReMeMemory()
        msgs = await mem.get_recent("s1")
        assert msgs == []


class TestReMeMemorySearch:
    @pytest.mark.asyncio
    async def test_search_with_server(self):
        """有 server_url 时通过 ReMe API 搜索。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "memories": [
                {"id": "1", "content": "I like coffee", "workspace_id": "s1"},
            ]
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        mem = ReMeMemory(server_url="http://localhost:8080")
        mem._client = mock_client

        results = await mem.search("s1", "coffee")
        assert len(results) == 1
        assert results[0].content == "I like coffee"

    @pytest.mark.asyncio
    async def test_search_fallback_to_buffer(self):
        """无 server_url 时回退到本地 buffer 搜索。"""
        mem = ReMeMemory()

        from agentpal.memory.base import MemoryMessage
        await mem.add(MemoryMessage(session_id="s1", role=MemoryRole.USER, content="I like coffee"))
        await mem.add(MemoryMessage(session_id="s1", role=MemoryRole.USER, content="I like tea"))

        results = await mem.search("s1", "coffee")
        assert len(results) == 1
        assert "coffee" in results[0].content


class TestReMeCrossSessionSearch:
    @pytest.mark.asyncio
    async def test_cross_session_by_user(self):
        mem = ReMeMemory()

        from agentpal.memory.base import MemoryMessage
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
        results = await mem.cross_session_search(scope, "消息")
        assert len(results) == 2


class TestReMeMemoryClear:
    @pytest.mark.asyncio
    async def test_clear_removes_buffer(self):
        mem = ReMeMemory()

        from agentpal.memory.base import MemoryMessage
        await mem.add(MemoryMessage(session_id="s1", role=MemoryRole.USER, content="hello"))
        await mem.clear("s1")

        msgs = await mem.get_recent("s1")
        assert msgs == []


class TestReMeMemoryCount:
    @pytest.mark.asyncio
    async def test_count(self):
        mem = ReMeMemory()

        from agentpal.memory.base import MemoryMessage
        for i in range(3):
            await mem.add(
                MemoryMessage(session_id="s1", role=MemoryRole.USER, content=f"msg{i}")
            )

        assert await mem.count("s1") == 3
