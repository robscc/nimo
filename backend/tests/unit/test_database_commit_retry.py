"""数据库提交重试逻辑测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from agentpal import database as db_module
from agentpal.database import commit_with_retry


class _DummyOrigExc(Exception):
    pass


def _locked_error() -> OperationalError:
    return OperationalError(
        statement="COMMIT",
        params={},
        orig=_DummyOrigExc("database is locked"),
    )


@pytest.mark.asyncio
async def test_commit_with_retry_success_after_locked() -> None:
    """遇到一次 locked 后应 rollback 并重试成功。"""
    session = AsyncMock()
    session.commit.side_effect = [_locked_error(), None]

    await commit_with_retry(session, max_attempts=3, base_delay=0)

    assert session.commit.await_count == 2
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_commit_with_retry_raise_after_exhausted() -> None:
    """超过重试上限应抛出最后一次 locked 异常。"""
    session = AsyncMock()
    session.commit.side_effect = [_locked_error(), _locked_error(), _locked_error()]

    with pytest.raises(OperationalError):
        await commit_with_retry(session, max_attempts=3, base_delay=0)

    assert session.commit.await_count == 3
    assert session.rollback.await_count == 2


@pytest.mark.asyncio
async def test_commit_with_retry_non_locked_no_retry() -> None:
    """非锁冲突错误不应重试。"""
    session = AsyncMock()
    non_locked = OperationalError(
        statement="COMMIT",
        params={},
        orig=_DummyOrigExc("syntax error"),
    )
    session.commit.side_effect = non_locked

    with pytest.raises(OperationalError):
        await commit_with_retry(session, max_attempts=3, base_delay=0)

    assert session.commit.await_count == 1
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_with_retry_logs_context_on_retry() -> None:
    """锁冲突重试时应带统一上下文字段并使用固定日志前缀。"""
    session = AsyncMock()
    session.commit.side_effect = [_locked_error(), None]

    with patch("agentpal.database.logger") as mock_logger:
        await commit_with_retry(
            session,
            max_attempts=3,
            base_delay=0,
            context={
                "component": "sub_agent",
                "phase": "before_tool_call",
                "session_id": "s1",
                "task_id": "t1",
                "tool_name": "read_file",
                "status": "running",
                "agent_name": "researcher",
            },
        )

    assert mock_logger.warning.called
    assert mock_logger.info.called

    warning_msg = mock_logger.warning.call_args[0][0]
    assert "sqlite_commit_retry" in warning_msg
    assert "event=retrying" in warning_msg


@pytest.mark.asyncio
async def test_commit_with_retry_context_has_fixed_fields() -> None:
    """context dict 缺失字段时也应补齐固定 key，便于 grep 聚合。"""
    session = AsyncMock()
    session.commit.side_effect = [_locked_error(), None]

    with patch("agentpal.database.logger") as mock_logger:
        await commit_with_retry(
            session,
            max_attempts=3,
            base_delay=0,
            context={"component": "personal_assistant", "phase": "run_tool_log_commit"},
        )

    # warning(..., ctx, ...) 第二个位置参数是格式化后的 context
    ctx_text = mock_logger.warning.call_args[0][1]
    assert "component=personal_assistant" in ctx_text
    assert "phase=run_tool_log_commit" in ctx_text
    assert "session_id=n/a" in ctx_text
    assert "task_id=n/a" in ctx_text
    assert "tool_name=n/a" in ctx_text
    assert "status=n/a" in ctx_text
    assert "agent_name=n/a" in ctx_text


@pytest.mark.asyncio
async def test_commit_with_retry_uses_jitter_delay() -> None:
    """未显式传 base_delay 时，每次重试应在 [20, 150] ms 间随机抖动。"""
    session = AsyncMock()
    session.commit.side_effect = [
        _locked_error(),
        _locked_error(),
        _locked_error(),
        None,
    ]

    sampled: list[tuple[float, float]] = []

    def _fake_uniform(lo: float, hi: float) -> float:
        sampled.append((lo, hi))
        return 0.0  # 不真睡

    with patch("agentpal.database.random.uniform", side_effect=_fake_uniform):
        await commit_with_retry(session, max_attempts=10)

    assert len(sampled) == 3
    for lo, hi in sampled:
        assert lo == 0.020
        assert hi == 0.150


@pytest.mark.asyncio
async def test_write_session_executes_begin_immediate() -> None:
    """write_session 进入上下文时必须先 BEGIN IMMEDIATE。"""
    session = AsyncMock()
    session.commit.return_value = None

    fake_ctx = MagicMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=session)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("agentpal.database.AsyncSessionLocal", return_value=fake_ctx):
        async with db_module.write_session() as s:
            assert s is session

    # 首次 execute 必须是 BEGIN IMMEDIATE
    assert session.execute.await_args_list[0].args[0].text == "BEGIN IMMEDIATE"
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_write_session_rolls_back_on_error() -> None:
    """写路径抛异常时 write_session 必须 rollback 并向外传播。"""
    session = AsyncMock()

    fake_ctx = MagicMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=session)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("agentpal.database.AsyncSessionLocal", return_value=fake_ctx):
        with pytest.raises(RuntimeError, match="boom"):
            async with db_module.write_session():
                raise RuntimeError("boom")

    session.rollback.assert_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_checkpoint_triggers_every_n_writes() -> None:
    """累计 _CHECKPOINT_EVERY 次成功写入应触发一次 PASSIVE checkpoint。"""
    # 重置全局计数，避免其他测试污染
    db_module._write_count = 0

    session = AsyncMock()
    session.execute.return_value = None

    for _ in range(db_module._CHECKPOINT_EVERY):
        await db_module._maybe_checkpoint(session)

    # 最后一次调用应该是 wal_checkpoint PASSIVE
    passive_calls = [
        call for call in session.execute.await_args_list
        if "wal_checkpoint(PASSIVE)" in str(call.args[0])
    ]
    assert len(passive_calls) == 1


@pytest.mark.asyncio
async def test_maybe_checkpoint_swallows_errors() -> None:
    """checkpoint 失败应静默吞异常，不影响主流程。"""
    db_module._write_count = db_module._CHECKPOINT_EVERY - 1

    session = AsyncMock()
    session.execute.side_effect = RuntimeError("disk full")

    # 不应抛异常
    await db_module._maybe_checkpoint(session)
