"""ReMeLight 持久化 E2E 测试。

直接实例化 ReMeLightMemory（使用 tmp_path，无需 LLM/embedding），
验证消息通过 add() + clear() 的完整持久化流程。

注意：这些测试需要 reme-ai 已安装（pip install reme-ai）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentpal.memory.base import MemoryMessage, MemoryRole

# 尝试 import reme，未安装则跳过全部测试
reme_available = True
try:
    from reme.reme_light import ReMeLight  # noqa: F401
except ImportError:
    reme_available = False

pytestmark = pytest.mark.skipif(not reme_available, reason="reme-ai 未安装")


def _find_dialog_jsonl(working_dir: Path) -> list[Path]:
    """查找 dialog/ 目录下所有 .jsonl 文件。"""
    dialog_dir = working_dir / "dialog"
    if not dialog_dir.exists():
        return []
    return sorted(dialog_dir.glob("*.jsonl"))


def _read_jsonl(path: Path) -> list[dict]:
    """逐行读取 jsonl 文件。"""
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


@pytest.fixture
def working_dir(tmp_path: Path) -> Path:
    """返回临时工作目录。"""
    wd = tmp_path / ".reme"
    wd.mkdir(parents=True, exist_ok=True)
    return wd


@pytest.mark.asyncio
async def test_add_and_clear_persists_to_jsonl(working_dir: Path):
    """add 多条消息 → clear → 验证 dialog/{date}.jsonl 存在且内容正确。"""
    from agentpal.memory.reme_light_adapter import ReMeLightMemory

    mem = ReMeLightMemory(working_dir=str(working_dir))

    # 添加多条消息
    messages = [
        MemoryMessage(session_id="s1", role=MemoryRole.USER, content="Hello!"),
        MemoryMessage(session_id="s1", role=MemoryRole.ASSISTANT, content="Hi there!"),
        MemoryMessage(session_id="s1", role=MemoryRole.USER, content="How are you?"),
    ]

    for msg in messages:
        await mem.add(msg)

    # clear 触发持久化
    await mem.clear("s1")

    # 验证 jsonl 文件存在
    jsonl_files = _find_dialog_jsonl(working_dir)
    assert len(jsonl_files) >= 1, f"期望至少 1 个 jsonl 文件，实际: {jsonl_files}"

    # 验证内容
    all_records = []
    for f in jsonl_files:
        all_records.extend(_read_jsonl(f))

    assert len(all_records) >= 3, f"期望至少 3 条记录，实际: {len(all_records)}"

    # 验证内容包含原始消息
    contents = [r.get("content", "") for r in all_records]
    all_content = " ".join(contents)
    assert "Hello!" in all_content
    assert "Hi there!" in all_content
    assert "How are you?" in all_content

    await mem.close()


@pytest.mark.asyncio
async def test_clear_empty_no_crash(working_dir: Path):
    """空 clear 不报错也不创建文件。"""
    from agentpal.memory.reme_light_adapter import ReMeLightMemory

    mem = ReMeLightMemory(working_dir=str(working_dir))

    # 直接 clear 一个不存在的 session
    await mem.clear("nonexistent")

    # 不应创建 dialog 目录（或目录为空）
    dialog_dir = working_dir / "dialog"
    if dialog_dir.exists():
        jsonl_files = list(dialog_dir.glob("*.jsonl"))
        assert len(jsonl_files) == 0, f"不应有 jsonl 文件，实际: {jsonl_files}"

    await mem.close()


@pytest.mark.asyncio
async def test_multiple_sessions_persist(working_dir: Path):
    """多 session add → clear → 验证全部持久化。"""
    from agentpal.memory.reme_light_adapter import ReMeLightMemory

    mem = ReMeLightMemory(working_dir=str(working_dir))

    # Session 1
    await mem.add(MemoryMessage(session_id="s1", role=MemoryRole.USER, content="Session1 Msg1"))
    await mem.add(MemoryMessage(session_id="s1", role=MemoryRole.ASSISTANT, content="Session1 Reply"))

    # Session 2
    await mem.add(MemoryMessage(session_id="s2", role=MemoryRole.USER, content="Session2 Msg1"))

    # clear 两个 session
    await mem.clear("s1")
    await mem.clear("s2")

    # 验证 jsonl 内容
    jsonl_files = _find_dialog_jsonl(working_dir)
    assert len(jsonl_files) >= 1

    all_records = []
    for f in jsonl_files:
        all_records.extend(_read_jsonl(f))

    contents = [r.get("content", "") for r in all_records]
    all_content = " ".join(contents)
    assert "Session1 Msg1" in all_content
    assert "Session1 Reply" in all_content
    assert "Session2 Msg1" in all_content

    await mem.close()


@pytest.mark.asyncio
async def test_jsonl_contains_tagged_content(working_dir: Path):
    """验证 jsonl 中包含 [session:xxx] tag。"""
    from agentpal.memory.reme_light_adapter import ReMeLightMemory

    mem = ReMeLightMemory(working_dir=str(working_dir))

    await mem.add(MemoryMessage(session_id="test-sess-42", role=MemoryRole.USER, content="tagged msg"))

    await mem.clear("test-sess-42")

    jsonl_files = _find_dialog_jsonl(working_dir)
    assert len(jsonl_files) >= 1

    all_records = []
    for f in jsonl_files:
        all_records.extend(_read_jsonl(f))

    # 至少有一条记录包含 session tag
    tagged_records = [
        r for r in all_records
        if "[session:test-sess-42]" in r.get("content", "")
    ]
    assert len(tagged_records) >= 1, (
        f"期望找到 [session:test-sess-42] tag，实际记录: {all_records}"
    )

    await mem.close()
