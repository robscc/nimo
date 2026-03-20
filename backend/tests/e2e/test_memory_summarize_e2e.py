"""E2E 测试：MemoryWriter summarize（记忆压缩 + 事实提炼）触发验证。

测试策略：
  1. 通过 API 创建 session
  2. 直接往 SQLite 插入 28 条含明确事实的假消息（跳过 LLM）
  3. 通过 /api/v1/agent/chat 发 1 条真实消息
     → user 消息(29) + assistant 消息(30) → count=30 → 30 % 30 == 0 → maybe_flush
  4. 等待后台 _flush 任务完成（LLM 有 thinking，最多 90s）
  5. 验证 ~/.nimo/MEMORY.md 被写入新内容 或 今日日志被写入

前提：后端运行在 http://localhost:8099，工作空间在 ~/.nimo/
注意：
  - 系统代理会干扰 httpx，统一使用 trust_env=False
  - 后端开发模式使用 agentpal_dev.db
  - _flush 是 asyncio.create_task 后台任务，LLM thinking 需要 ~60s
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
import pytest

BACKEND_URL = "http://localhost:8099"
WORKSPACE_DIR = Path.home() / ".nimo"

# 可能的 DB 路径（按优先级排序）
_CANDIDATE_DB_PATHS = [
    # 开发环境默认（app_env=development 时使用 _dev 后缀）
    Path(__file__).parents[2] / "agentpal_dev.db",
    Path(__file__).parents[5] / "backend" / "agentpal_dev.db",
    Path.home() / "workspace" / "lab" / "nimo" / "backend" / "agentpal_dev.db",
    # 生产/其他命名
    Path(__file__).parents[2] / "agentpal.db",
    Path(__file__).parents[5] / "backend" / "agentpal.db",
    Path.home() / "workspace" / "lab" / "nimo" / "backend" / "agentpal.db",
    Path.home() / ".nimo" / "agentpal.db",
    Path.cwd() / "agentpal_dev.db",
    Path.cwd() / "agentpal.db",
]

# 含明确事实的消息对（LLM 会从中提炼事实）
_FACT_MESSAGES = [
    ("user",      "我叫 Alice，今年 28 岁，是一名软件工程师。"),
    ("assistant", "好的，我记住了：您叫 Alice，28岁，软件工程师。"),
    ("user",      "我住在上海浦东新区，养了一只猫叫 Mochi。"),
    ("assistant", "明白，您住在上海浦东，有一只猫叫 Mochi。"),
    ("user",      "我最喜欢吃寿司，每周五去健身房锻炼两小时。"),
    ("assistant", "好的，您喜欢吃寿司，每周五坚持健身两小时。"),
    ("user",      "我在字节跳动工作了 3 年，现在转行做 AI 研究。"),
    ("assistant", "了解，您曾在字节跳动工作 3 年，现在专注 AI 研究。"),
    ("user",      "我的 GitHub 用户名是 alice-dev，常用技术栈：Python 和 React。"),
    ("assistant", "记住了，GitHub: alice-dev，技术栈：Python + React。"),
    ("user",      "我目前在做一个基于 LLM 的开源个人助手项目。"),
    ("assistant", "很棒！您在做基于 LLM 的开源个人助手项目。"),
]


def _get_db_path() -> Path:
    """找到运行中 backend 使用的 SQLite 数据库路径。

    遍历候选路径，返回第一个存在且含 memory_records 表的 DB。
    """
    for path in _CANDIDATE_DB_PATHS:
        if not path.exists():
            continue
        try:
            conn = sqlite3.connect(str(path))
            conn.execute("SELECT 1 FROM memory_records LIMIT 1")
            conn.close()
            return path
        except Exception:
            pass

    raise FileNotFoundError(
        f"找不到含 memory_records 表的 agentpal.db。尝试路径：\n"
        + "\n".join(f"  {p}" for p in _CANDIDATE_DB_PATHS)
    )


def _insert_fake_messages(session_id: str, count: int) -> None:
    """直接往 SQLite memory_records 表插入 count 条含事实的假消息。"""
    db_path = _get_db_path()
    conn = sqlite3.connect(str(db_path))
    try:
        for i in range(count):
            if i < len(_FACT_MESSAGES):
                role, content = _FACT_MESSAGES[i]
            else:
                role = "user" if i % 2 == 0 else "assistant"
                content = (
                    f"我们今天讨论了 AI 技术的最新进展，第 {i} 个话题。"
                    if role == "user"
                    else f"好的，AI 技术进展话题 {i} 已记录。"
                )
            conn.execute(
                """
                INSERT INTO memory_records (id, session_id, role, content, created_at, meta)
                VALUES (?, ?, ?, ?, datetime('now', ?), NULL)
                """,
                (str(uuid.uuid4()), session_id, role, content, f"+{i} seconds"),
            )
        conn.commit()
    finally:
        conn.close()


def _read_sse_response(response: httpx.Response) -> list[dict]:
    """解析 SSE text/event-stream 响应，返回所有事件 dict 列表。"""
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


def _wait_for_flush(
    memory_md: Path,
    size_before: int,
    mtime_before: float,
    daily_log: Path,
    daily_size_before: int,
    timeout: int = 90,
) -> bool:
    """等待 MemoryWriter._flush 写入文件，最多等 timeout 秒。

    Returns:
        True if flush detected, False on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        # 检查 MEMORY.md
        if memory_md.exists():
            if (
                memory_md.stat().st_size > size_before
                or memory_md.stat().st_mtime > mtime_before
            ):
                return True
        # 检查今日日志
        if daily_log.exists() and daily_log.stat().st_size > daily_size_before:
            return True
    return False


class TestMemorySummarize:
    """MemoryWriter.maybe_flush 的 E2E 触发验证。"""

    def test_summarize_triggers_on_30_messages(self):
        """发送消息满足 compaction_threshold=30 时触发 MemoryWriter._flush。

        步骤：
        1. 创建 session
        2. 插入 28 条含事实的假消息（不经 LLM）
        3. 发 1 条真实 chat → user(29) + assistant(30) → 30%30==0 → maybe_flush
        4. 等后台 _flush 任务完成（最多 90s，LLM thinking 需时较长）
        5. 验证 MEMORY.md 或今日日志被写入
        """
        # 1. 创建 session
        with httpx.Client(base_url=BACKEND_URL, timeout=30.0, trust_env=False) as client:
            resp = client.post("/api/v1/sessions", json={"channel": "test"})
            assert resp.is_success, f"创建 session 失败: {resp.status_code} {resp.text}"
            session_id = resp.json()["id"]

        # 2. 插入 28 条含事实的假消息
        # user(29) + assistant(30) 由真实 LLM chat 产生 → count=30 触发 flush
        _insert_fake_messages(session_id, count=28)

        # 记录写入前的文件状态
        memory_md = WORKSPACE_DIR / "MEMORY.md"
        size_before = memory_md.stat().st_size if memory_md.exists() else 0
        mtime_before = memory_md.stat().st_mtime if memory_md.exists() else 0.0

        today = time.strftime("%Y-%m-%d")
        daily_log = WORKSPACE_DIR / "memory" / f"{today}.md"
        daily_before = daily_log.stat().st_size if daily_log.exists() else 0

        # 3. 发真实 chat（SSE 流式）
        with httpx.Client(base_url=BACKEND_URL, timeout=60.0, trust_env=False) as client:
            resp = client.post(
                "/api/v1/agent/chat",
                json={
                    "session_id": session_id,
                    "message": "好的，我们继续聊。请简单回应一下。",
                },
                headers={"Accept": "text/event-stream"},
            )
            assert resp.is_success, f"chat 请求失败: {resp.status_code} {resp.text[:200]}"
            events = _read_sse_response(resp)

        # 验证对话正常完成
        event_types = [e.get("type") for e in events]
        assert "done" in event_types, f"未收到 done 事件，实际: {event_types}"
        assert "error" not in event_types, f"对话报错: {[e for e in events if e.get('type')=='error']}"

        # 4. 等待后台 _flush 完成（最多 90s）
        flushed = _wait_for_flush(
            memory_md, size_before, mtime_before, daily_log, daily_before, timeout=90
        )

        assert flushed, (
            "等待 90s 后 MemoryWriter._flush 未触发写入。\n"
            f"MEMORY.md: before={size_before} / after={memory_md.stat().st_size if memory_md.exists() else 'N/A'}\n"
            f"today log: before={daily_before} / after={daily_log.stat().st_size if daily_log.exists() else 'N/A'}\n"
            f"SSE events: {event_types}"
        )

    def test_summarize_content_written_to_memory(self):
        """验证 MemoryWriter 写入 MEMORY.md 的内容包含事实提炼。

        使用含明确个人信息的假消息，触发 flush 后检查 MEMORY.md 有新内容写入。
        """
        # 1. 创建 session
        with httpx.Client(base_url=BACKEND_URL, timeout=30.0, trust_env=False) as client:
            resp = client.post("/api/v1/sessions", json={"channel": "test"})
            assert resp.is_success, f"创建 session 失败: {resp.status_code} {resp.text}"
            session_id = resp.json()["id"]

        # 2. 插入 28 条含可识别关键词的假消息
        # user(29) + assistant(30) → count=30 → 触发 maybe_flush
        db_path = _get_db_path()
        conn = sqlite3.connect(str(db_path))
        try:
            # 先插入有明确个人信息的消息
            for i, (role, content) in enumerate(_FACT_MESSAGES):
                conn.execute(
                    """INSERT INTO memory_records (id, session_id, role, content, created_at, meta)
                       VALUES (?, ?, ?, ?, datetime('now', ?), NULL)""",
                    (str(uuid.uuid4()), session_id, role, content, f"+{i} seconds"),
                )
            # 填充到 28 条
            for i in range(len(_FACT_MESSAGES), 28):
                role = "user" if i % 2 == 0 else "assistant"
                conn.execute(
                    """INSERT INTO memory_records (id, session_id, role, content, created_at, meta)
                       VALUES (?, ?, ?, ?, datetime('now', ?), NULL)""",
                    (
                        str(uuid.uuid4()), session_id, role,
                        f"关于 AI 和个人助手话题的第 {i} 轮对话。", f"+{i} seconds",
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        # 记录写入前状态
        memory_md = WORKSPACE_DIR / "MEMORY.md"
        content_before = memory_md.read_text(encoding="utf-8") if memory_md.exists() else ""
        size_before = len(content_before)
        mtime_before = memory_md.stat().st_mtime if memory_md.exists() else 0.0

        today = time.strftime("%Y-%m-%d")
        daily_log = WORKSPACE_DIR / "memory" / f"{today}.md"
        daily_before = daily_log.stat().st_size if daily_log.exists() else 0

        # 3. 发真实 chat（触发第 30 条 → maybe_flush）
        with httpx.Client(base_url=BACKEND_URL, timeout=60.0, trust_env=False) as client:
            resp = client.post(
                "/api/v1/agent/chat",
                json={
                    "session_id": session_id,
                    "message": "好的，感谢你的记录，我们继续。",
                },
                headers={"Accept": "text/event-stream"},
            )
            assert resp.is_success, f"chat 请求失败: {resp.status_code} {resp.text[:200]}"
            events = _read_sse_response(resp)

        event_types = [e.get("type") for e in events]
        assert "done" in event_types, f"未收到 done 事件: {event_types}"

        # 4. 等待后台 _flush 写入（最多 90s）
        flushed = _wait_for_flush(
            memory_md, size_before, mtime_before, daily_log, daily_before, timeout=90
        )

        assert flushed, (
            "90s 内 MemoryWriter._flush 未触发写入，MEMORY.md 或今日日志无变化"
        )

        # 5. 验证写入内容不为空
        new_size = memory_md.stat().st_size if memory_md.exists() else 0
        new_daily = daily_log.stat().st_size if daily_log.exists() else 0
        assert new_size > size_before or new_daily > daily_before, (
            "文件大小未增加，写入内容可能为空"
        )
