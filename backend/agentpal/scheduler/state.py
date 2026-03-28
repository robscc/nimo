"""Agent 状态机 — 进程状态枚举 + AgentProcessInfo 数据类。

每个 Agent 子进程对应一个 AgentProcessInfo 实例，通过状态机管理生命周期。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class AgentState(StrEnum):
    """Agent 子进程状态枚举。"""

    PENDING = "pending"  # 已创建条目，子进程尚未启动
    STARTING = "starting"  # 子进程已 spawn，等待 ZMQ AGENT_REGISTER
    RUNNING = "running"  # Agent 正在处理消息
    IDLE = "idle"  # Agent 空闲，等待新消息
    STOPPING = "stopping"  # 正在关闭子进程
    STOPPED = "stopped"  # 子进程已退出
    FAILED = "failed"  # 子进程异常退出


# 合法状态转换表
VALID_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.PENDING: {AgentState.STARTING, AgentState.FAILED},
    AgentState.STARTING: {AgentState.IDLE, AgentState.FAILED},
    AgentState.IDLE: {AgentState.RUNNING, AgentState.STOPPING, AgentState.FAILED},
    AgentState.RUNNING: {AgentState.IDLE, AgentState.STOPPING, AgentState.FAILED},
    AgentState.STOPPING: {AgentState.STOPPED, AgentState.FAILED},
    AgentState.STOPPED: set(),
    AgentState.FAILED: set(),
}


@dataclass
class AgentProcessInfo:
    """Agent 子进程信息。

    Attributes:
        process_id:     唯一标识，如 "pa:{session_id}" 或 "sub:{task_id}"
        agent_type:     Agent 类型 "pa" | "sub_agent" | "cron"
        state:          当前状态
        session_id:     关联的 session ID（PA 用）
        task_id:        关联的 task ID（SubAgent 用）
        agent_name:     Agent 角色名（SubAgent 用）
        os_pid:         操作系统进程 PID
        started_at:     启动时间戳（Unix 秒）
        last_active_at: 最近活跃时间戳（Unix 秒）
        running_since:  进入 RUNNING 状态的时间戳（Unix 秒），非 RUNNING 时为 0
        error:          错误信息（仅 FAILED 状态）
    """

    process_id: str
    agent_type: str  # "pa" | "sub_agent" | "cron"
    state: AgentState = AgentState.PENDING
    session_id: str | None = None
    task_id: str | None = None
    agent_name: str | None = None
    os_pid: int | None = None
    started_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    running_since: float = 0.0
    error: str | None = None

    def transition_to(self, new_state: AgentState) -> None:
        """执行状态转换。

        Args:
            new_state: 目标状态

        Raises:
            ValueError: 非法状态转换
        """
        valid = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in valid:
            raise ValueError(
                f"Invalid state transition: {self.state} -> {new_state} "
                f"(valid targets: {valid})"
            )
        self.state = new_state
        self.last_active_at = time.time()

    @property
    def is_alive(self) -> bool:
        """进程是否处于活跃状态（非终态）。"""
        return self.state not in (AgentState.STOPPED, AgentState.FAILED)

    @property
    def idle_seconds(self) -> float:
        """空闲时长（秒）。"""
        return time.time() - self.last_active_at

    def to_dict(self) -> dict:
        """序列化为字典（Dashboard API 用）。"""
        from datetime import datetime, timezone

        return {
            "process_id": self.process_id,
            "agent_type": self.agent_type,
            "state": str(self.state),
            "session_id": self.session_id,
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "os_pid": self.os_pid,
            "started_at": datetime.fromtimestamp(
                self.started_at, tz=timezone.utc
            ).isoformat(),
            "last_active_at": datetime.fromtimestamp(
                self.last_active_at, tz=timezone.utc
            ).isoformat(),
            "idle_seconds": round(self.idle_seconds, 1),
            "running_since": (
                datetime.fromtimestamp(
                    self.running_since, tz=timezone.utc
                ).isoformat()
                if self.running_since > 0
                else None
            ),
            "running_seconds": (
                round(time.time() - self.running_since, 1)
                if self.running_since > 0
                else None
            ),
            "error": self.error,
        }
