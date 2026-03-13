"""MessageBus 单元测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.database import Base
from agentpal.agents.message_bus import MessageBus
from agentpal.models.message import MessageStatus, MessageType


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


class TestMessageBus:
    @pytest.mark.asyncio
    async def test_send_and_receive(self, db: AsyncSession):
        """发送消息后可以接收到。"""
        bus = MessageBus(db)
        await bus.send(
            from_agent="coder",
            to_agent="researcher",
            parent_session_id="session-1",
            content="请帮我查找一下这个 API 的文档",
            message_type=MessageType.REQUEST,
        )

        messages = await bus.receive_pending("researcher", "session-1")
        assert len(messages) == 1
        assert messages[0]["from_agent"] == "coder"
        assert messages[0]["content"] == "请帮我查找一下这个 API 的文档"
        assert messages[0]["message_type"] == MessageType.REQUEST

    @pytest.mark.asyncio
    async def test_receive_marks_delivered(self, db: AsyncSession):
        """接收后消息状态应变为 delivered。"""
        bus = MessageBus(db)
        await bus.send(
            from_agent="a",
            to_agent="b",
            parent_session_id="s1",
            content="hello",
        )

        # 第一次接收
        messages = await bus.receive_pending("b")
        assert len(messages) == 1

        # 第二次接收不到了（已标记 delivered）
        messages = await bus.receive_pending("b")
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_receive_only_for_target(self, db: AsyncSession):
        """只能接收发给自己的消息。"""
        bus = MessageBus(db)
        await bus.send(from_agent="a", to_agent="b", parent_session_id="s1", content="for b")
        await bus.send(from_agent="a", to_agent="c", parent_session_id="s1", content="for c")

        b_msgs = await bus.receive_pending("b")
        assert len(b_msgs) == 1
        assert b_msgs[0]["content"] == "for b"

    @pytest.mark.asyncio
    async def test_mark_processed(self, db: AsyncSession):
        """标记消息为已处理。"""
        bus = MessageBus(db)
        msg = await bus.send(
            from_agent="a", to_agent="b", parent_session_id="s1", content="test",
        )
        await bus.mark_processed(msg.id)

        # 验证状态已更新
        messages = await bus.receive_pending("b")
        assert len(messages) == 0  # processed 的不在 pending 列表中

    @pytest.mark.asyncio
    async def test_get_conversation(self, db: AsyncSession):
        """获取两个 Agent 之间的对话历史。"""
        bus = MessageBus(db)
        await bus.send(
            from_agent="coder", to_agent="researcher",
            parent_session_id="s1", content="msg1",
        )
        await bus.send(
            from_agent="researcher", to_agent="coder",
            parent_session_id="s1", content="msg2",
        )
        await bus.send(
            from_agent="coder", to_agent="researcher",
            parent_session_id="s1", content="msg3",
        )

        conv = await bus.get_conversation("s1", "coder", "researcher")
        assert len(conv) == 3
        assert conv[0]["content"] == "msg1"
        assert conv[2]["content"] == "msg3"

    @pytest.mark.asyncio
    async def test_session_messages(self, db: AsyncSession):
        """获取会话内所有消息。"""
        bus = MessageBus(db)
        await bus.send(from_agent="a", to_agent="b", parent_session_id="s1", content="1")
        await bus.send(from_agent="b", to_agent="c", parent_session_id="s1", content="2")
        await bus.send(from_agent="c", to_agent="a", parent_session_id="s2", content="3")

        msgs = await bus.get_session_messages("s1")
        assert len(msgs) == 2

    @pytest.mark.asyncio
    async def test_reply_to(self, db: AsyncSession):
        """回复消息应包含 in_reply_to。"""
        bus = MessageBus(db)
        original = await bus.send(
            from_agent="a", to_agent="b", parent_session_id="s1", content="question",
        )
        reply = await bus.send(
            from_agent="b", to_agent="a", parent_session_id="s1",
            content="answer", message_type=MessageType.RESPONSE,
            in_reply_to=original.id,
        )

        conv = await bus.get_conversation("s1", "a", "b")
        assert len(conv) == 2
        assert conv[1]["in_reply_to"] == original.id
