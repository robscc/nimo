"""SubAgent 单元测试。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentpal.agents.sub_agent import SubAgent
from agentpal.memory.buffer import BufferMemory
from agentpal.models.session import SubAgentTask, TaskStatus


def _make_task(task_id: str = "task-001") -> SubAgentTask:
    task = SubAgentTask(
        id=task_id,
        parent_session_id="parent-session",
        sub_session_id=f"sub:parent-session:{task_id}",
        task_prompt="执行测试任务",
        status=TaskStatus.PENDING,
    )
    return task


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture
def sub_agent(mock_db) -> SubAgent:
    task = _make_task()
    memory = BufferMemory(max_size=10)
    return SubAgent(
        session_id="sub:parent-session:task-001",
        memory=memory,
        task=task,
        db=mock_db,
        model_config={"config_name": "test"},
    )


class TestSubAgentRun:
    @pytest.mark.asyncio
    async def test_run_success_updates_status(self, sub_agent: SubAgent):
        with patch.object(sub_agent, "reply", return_value="任务完成结果"):
            result = await sub_agent.run("执行任务")

        assert result == "任务完成结果"
        assert sub_agent._task.status == TaskStatus.DONE
        assert sub_agent._task.result == "任务完成结果"
        assert sub_agent._task.finished_at is not None

    @pytest.mark.asyncio
    async def test_run_failure_updates_status(self, sub_agent: SubAgent):
        with patch.object(sub_agent, "reply", side_effect=RuntimeError("LLM Error")):
            result = await sub_agent.run("执行任务")

        assert result == ""
        assert sub_agent._task.status == TaskStatus.FAILED
        assert "RuntimeError" in sub_agent._task.error
        assert sub_agent._task.finished_at is not None

    @pytest.mark.asyncio
    async def test_run_sets_running_before_reply(self, sub_agent: SubAgent):
        statuses = []

        async def capture_reply(prompt, **kwargs):
            statuses.append(sub_agent._task.status)
            return "done"

        with patch.object(sub_agent, "reply", side_effect=capture_reply):
            await sub_agent.run("task")

        assert TaskStatus.RUNNING in statuses


class TestSubAgentMemoryIsolation:
    @pytest.mark.asyncio
    async def test_sub_agent_has_own_session(self, sub_agent: SubAgent):
        assert sub_agent.session_id == "sub:parent-session:task-001"

    @pytest.mark.asyncio
    async def test_sub_agent_writes_to_own_memory(self, sub_agent: SubAgent):
        mock_response = MagicMock()
        mock_response.content = [{"type": "text", "text": "response"}]
        # model.__call__ 是 async，必须用 AsyncMock
        mock_model = AsyncMock(return_value=mock_response)

        with patch("agentpal.agents.personal_assistant._build_model", return_value=mock_model):
            with patch.object(sub_agent, "_build_toolkit", return_value=None):
                await sub_agent.reply("question")

        user_msgs = await sub_agent.memory.get_recent(sub_agent.session_id)
        roles = [m.role for m in user_msgs]
        assert "user" in [str(r) for r in roles]
        assert "assistant" in [str(r) for r in roles]
