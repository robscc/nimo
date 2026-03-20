"""MemoryFactory 单元测试。"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agentpal.memory.buffer import BufferMemory
from agentpal.memory.factory import MemoryFactory
from agentpal.memory.hybrid import HybridMemory
from agentpal.memory.sqlite import SQLiteMemory


class TestMemoryFactory:
    def test_create_buffer(self):
        mem = MemoryFactory.create("buffer")
        assert isinstance(mem, BufferMemory)

    def test_create_buffer_custom_size(self):
        mem = MemoryFactory.create("buffer", buffer_size=50)
        assert isinstance(mem, BufferMemory)
        assert mem._max_size == 50

    def test_create_sqlite_requires_db(self):
        with pytest.raises(ValueError, match="db"):
            MemoryFactory.create("sqlite")

    def test_create_sqlite_with_db(self):
        mock_db = MagicMock()
        mem = MemoryFactory.create("sqlite", db=mock_db)
        assert isinstance(mem, SQLiteMemory)

    def test_create_hybrid_requires_db(self):
        with pytest.raises(ValueError, match="db"):
            MemoryFactory.create("hybrid")

    def test_create_hybrid_with_db(self):
        mock_db = MagicMock()
        mem = MemoryFactory.create("hybrid", db=mock_db)
        assert isinstance(mem, HybridMemory)

    def test_create_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="未知的 memory_backend"):
            MemoryFactory.create("vector_db_xyz")

    def test_create_none_uses_settings_default(self, monkeypatch):
        """None backend 时读取全局配置（默认 hybrid）。"""
        mock_db = MagicMock()
        mem = MemoryFactory.create(None, db=mock_db)
        assert isinstance(mem, HybridMemory)

    def test_create_mem0(self):
        """创建 mem0 后端。"""
        from agentpal.memory.mem0_adapter import Mem0Memory

        mem = MemoryFactory.create("mem0")
        assert isinstance(mem, Mem0Memory)

    def test_create_mem0_with_config(self):
        """创建 mem0 后端（带配置）。"""
        from agentpal.memory.mem0_adapter import Mem0Memory

        config = {"llm": {"provider": "openai"}}
        mem = MemoryFactory.create("mem0", mem0_config=config)
        assert isinstance(mem, Mem0Memory)

    def test_create_reme(self):
        """创建 ReMe 后端。"""
        from agentpal.memory.reme_adapter import ReMeMemory

        mem = MemoryFactory.create("reme")
        assert isinstance(mem, ReMeMemory)

    def test_create_reme_with_server_url(self):
        """创建 ReMe 后端（带 server URL）。"""
        from agentpal.memory.reme_adapter import ReMeMemory

        mem = MemoryFactory.create("reme", reme_server_url="http://localhost:8080")
        assert isinstance(mem, ReMeMemory)

    def test_create_reme_light(self):
        """创建 ReMeLight 后端。"""
        from agentpal.memory.reme_light_adapter import ReMeLightMemory

        mem = MemoryFactory.create("reme_light")
        assert isinstance(mem, ReMeLightMemory)

    def test_create_reme_light_with_config(self):
        """创建 ReMeLight 后端（带配置）。"""
        from agentpal.memory.reme_light_adapter import ReMeLightMemory

        mem = MemoryFactory.create(
            "reme_light",
            reme_light_working_dir="/tmp/reme",
            reme_light_llm_api_key="sk-test",
            reme_light_llm_base_url="https://api.example.com/v1",
            reme_light_embedding_api_key="sk-emb",
            reme_light_vector_weight=0.8,
            reme_light_candidate_multiplier=5.0,
        )
        assert isinstance(mem, ReMeLightMemory)
        assert mem._working_dir == "/tmp/reme"
        assert mem._llm_api_key == "sk-test"
        assert mem._llm_base_url == "https://api.example.com/v1"
        assert mem._embedding_api_key == "sk-emb"
        assert mem._vector_weight == 0.8
        assert mem._candidate_multiplier == 5.0
