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
