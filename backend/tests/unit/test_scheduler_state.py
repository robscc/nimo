"""单元测试 — AgentState 状态机 + AgentProcessInfo。"""

from __future__ import annotations

import time

import pytest

from agentpal.scheduler.state import (
    VALID_TRANSITIONS,
    AgentProcessInfo,
    AgentState,
)


class TestAgentState:
    """AgentState 枚举测试。"""

    def test_all_states_exist(self):
        expected = {"pending", "starting", "running", "idle", "stopping", "stopped", "failed"}
        actual = {s.value for s in AgentState}
        assert actual == expected

    def test_str_enum_values(self):
        assert str(AgentState.PENDING) == "pending"
        assert str(AgentState.RUNNING) == "running"
        assert AgentState("idle") == AgentState.IDLE


class TestValidTransitions:
    """状态转换表测试。"""

    def test_pending_can_start_or_fail(self):
        assert VALID_TRANSITIONS[AgentState.PENDING] == {
            AgentState.STARTING,
            AgentState.FAILED,
        }

    def test_starting_can_idle_or_fail(self):
        assert VALID_TRANSITIONS[AgentState.STARTING] == {
            AgentState.IDLE,
            AgentState.FAILED,
        }

    def test_idle_can_run_stop_or_fail(self):
        assert VALID_TRANSITIONS[AgentState.IDLE] == {
            AgentState.RUNNING,
            AgentState.STOPPING,
            AgentState.FAILED,
        }

    def test_running_can_idle_stop_or_fail(self):
        assert VALID_TRANSITIONS[AgentState.RUNNING] == {
            AgentState.IDLE,
            AgentState.STOPPING,
            AgentState.FAILED,
        }

    def test_stopping_can_stop_or_fail(self):
        assert VALID_TRANSITIONS[AgentState.STOPPING] == {
            AgentState.STOPPED,
            AgentState.FAILED,
        }

    def test_terminal_states_have_no_transitions(self):
        assert VALID_TRANSITIONS[AgentState.STOPPED] == set()
        assert VALID_TRANSITIONS[AgentState.FAILED] == set()

    def test_all_states_covered(self):
        assert set(VALID_TRANSITIONS.keys()) == set(AgentState)


class TestAgentProcessInfo:
    """AgentProcessInfo 数据类测试。"""

    def test_create_with_defaults(self):
        info = AgentProcessInfo(process_id="pa:test-1", agent_type="pa")
        assert info.process_id == "pa:test-1"
        assert info.agent_type == "pa"
        assert info.state == AgentState.PENDING
        assert info.session_id is None
        assert info.os_pid is None
        assert info.error is None
        assert info.started_at > 0
        assert info.last_active_at > 0

    def test_create_with_all_fields(self):
        now = time.time()
        info = AgentProcessInfo(
            process_id="sub:coder:task-abc",
            agent_type="sub_agent",
            state=AgentState.RUNNING,
            session_id="session-123",
            task_id="task-abc",
            agent_name="coder",
            os_pid=12345,
            started_at=now,
            last_active_at=now,
        )
        assert info.agent_name == "coder"
        assert info.os_pid == 12345
        assert info.task_id == "task-abc"

    def test_valid_transition(self):
        info = AgentProcessInfo(process_id="pa:test", agent_type="pa")
        assert info.state == AgentState.PENDING

        info.transition_to(AgentState.STARTING)
        assert info.state == AgentState.STARTING

        info.transition_to(AgentState.IDLE)
        assert info.state == AgentState.IDLE

        info.transition_to(AgentState.RUNNING)
        assert info.state == AgentState.RUNNING

        info.transition_to(AgentState.IDLE)
        assert info.state == AgentState.IDLE

        info.transition_to(AgentState.STOPPING)
        assert info.state == AgentState.STOPPING

        info.transition_to(AgentState.STOPPED)
        assert info.state == AgentState.STOPPED

    def test_invalid_transition_raises(self):
        info = AgentProcessInfo(process_id="pa:test", agent_type="pa")
        with pytest.raises(ValueError, match="Invalid state transition"):
            info.transition_to(AgentState.RUNNING)  # PENDING → RUNNING 非法

    def test_invalid_transition_from_terminal(self):
        info = AgentProcessInfo(
            process_id="pa:test", agent_type="pa", state=AgentState.STOPPED
        )
        with pytest.raises(ValueError, match="Invalid state transition"):
            info.transition_to(AgentState.RUNNING)

    def test_failed_from_any_active_state(self):
        for state in [
            AgentState.PENDING,
            AgentState.STARTING,
            AgentState.IDLE,
            AgentState.RUNNING,
            AgentState.STOPPING,
        ]:
            info = AgentProcessInfo(
                process_id="pa:test", agent_type="pa", state=state
            )
            info.transition_to(AgentState.FAILED)
            assert info.state == AgentState.FAILED

    def test_is_alive_active_states(self):
        for state in [
            AgentState.PENDING,
            AgentState.STARTING,
            AgentState.IDLE,
            AgentState.RUNNING,
            AgentState.STOPPING,
        ]:
            info = AgentProcessInfo(
                process_id="pa:test", agent_type="pa", state=state
            )
            assert info.is_alive is True

    def test_is_alive_terminal_states(self):
        for state in [AgentState.STOPPED, AgentState.FAILED]:
            info = AgentProcessInfo(
                process_id="pa:test", agent_type="pa", state=state
            )
            assert info.is_alive is False

    def test_idle_seconds(self):
        info = AgentProcessInfo(
            process_id="pa:test",
            agent_type="pa",
            last_active_at=time.time() - 60,
        )
        assert info.idle_seconds >= 59

    def test_transition_updates_last_active(self):
        info = AgentProcessInfo(
            process_id="pa:test",
            agent_type="pa",
            last_active_at=time.time() - 100,
        )
        old_active = info.last_active_at
        info.transition_to(AgentState.STARTING)
        assert info.last_active_at > old_active

    def test_to_dict(self):
        info = AgentProcessInfo(
            process_id="pa:test-session",
            agent_type="pa",
            state=AgentState.RUNNING,
            session_id="test-session",
            os_pid=1234,
        )
        d = info.to_dict()
        assert d["process_id"] == "pa:test-session"
        assert d["agent_type"] == "pa"
        assert d["state"] == "running"
        assert d["session_id"] == "test-session"
        assert d["os_pid"] == 1234
        assert "started_at" in d
        assert "last_active_at" in d
        assert "idle_seconds" in d
        assert isinstance(d["idle_seconds"], float)
