"""PlanStore — 计划的文件存储层。

计划以 JSON 文件存储在 ``{plans_dir}/{session_id}/{plan_id}.json``。
使用 aiofiles 异步读写，遵循 WorkspaceManager 的文件 I/O 模式。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

import aiofiles
from loguru import logger


# ── 数据结构 ──────────────────────────────────────────────


class PlanStatus(StrEnum):
    GENERATING = "generating"
    CONFIRMING = "confirming"
    EXECUTING = "executing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    index: int
    title: str
    description: str
    strategy: str = ""
    tools: list[str] = field(default_factory=list)
    status: str = "pending"  # pending / running / completed / failed / skipped
    task_id: str | None = None  # SubAgentTask ID
    result: str | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class Plan:
    id: str
    session_id: str
    goal: str
    summary: str
    status: PlanStatus
    auto_proceed: bool = True  # Phase 1 默认 true
    current_step: int = 0
    steps: list[PlanStep] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = _utc_now()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict[str, Any]:
        """转为 JSON 可序列化的 dict。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        """从 dict 反序列化。"""
        steps_data = data.pop("steps", [])
        steps = [PlanStep(**s) for s in steps_data]
        # 确保 status 是 PlanStatus 枚举
        if isinstance(data.get("status"), str):
            data["status"] = PlanStatus(data["status"])
        return cls(steps=steps, **data)

    def next_pending_step(self) -> PlanStep | None:
        """返回下一个待执行的步骤，或 None。"""
        for step in self.steps:
            if step.status == StepStatus.PENDING:
                return step
        return None

    def all_done(self) -> bool:
        """所有步骤是否都已完成或跳过。"""
        return all(
            s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
            for s in self.steps
        )

    def mark_step_running(self, index: int, task_id: str) -> None:
        if 0 <= index < len(self.steps):
            step = self.steps[index]
            step.status = StepStatus.RUNNING
            step.task_id = task_id
            step.started_at = _utc_now()
            self.current_step = index
            self.updated_at = _utc_now()

    def mark_step_done(self, index: int, result: str) -> None:
        if 0 <= index < len(self.steps):
            step = self.steps[index]
            step.status = StepStatus.COMPLETED
            step.result = result
            step.completed_at = _utc_now()
            self.updated_at = _utc_now()

    def mark_step_failed(self, index: int, error: str) -> None:
        if 0 <= index < len(self.steps):
            step = self.steps[index]
            step.status = StepStatus.FAILED
            step.error = error
            step.completed_at = _utc_now()
            self.updated_at = _utc_now()


# ── PlanStore ─────────────────────────────────────────────


class PlanStore:
    """文件系统上的计划存储。

    目录结构::

        {plans_dir}/
            {session_id}/
                {plan_id}.json
    """

    def __init__(self, plans_dir: str | Path) -> None:
        self._base = Path(plans_dir)

    def _session_dir(self, session_id: str) -> Path:
        # 将 session_id 中不适合做目录名的字符替换
        safe_id = session_id.replace(":", "_").replace("/", "_")
        return self._base / safe_id

    def _plan_path(self, session_id: str, plan_id: str) -> Path:
        return self._session_dir(session_id) / f"{plan_id}.json"

    async def save(self, plan: Plan) -> None:
        """保存（创建或更新）计划。"""
        plan.updated_at = _utc_now()
        path = self._plan_path(plan.session_id, plan.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(plan.to_dict(), ensure_ascii=False, indent=2)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(data)
        logger.debug(f"Plan saved: {path}")

    async def load(self, session_id: str, plan_id: str) -> Plan | None:
        """加载指定计划。"""
        path = self._plan_path(session_id, plan_id)
        if not path.exists():
            return None
        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                text = await f.read()
            data = json.loads(text)
            return Plan.from_dict(data)
        except Exception as exc:
            logger.warning(f"Failed to load plan {path}: {exc}")
            return None

    async def get_active(self, session_id: str) -> Plan | None:
        """获取当前 session 的活跃计划（executing 或 confirming 状态）。"""
        plans = await self._load_all(session_id)
        for plan in plans:
            if plan.status in (
                PlanStatus.EXECUTING,
                PlanStatus.CONFIRMING,
                PlanStatus.GENERATING,
            ):
                return plan
        return None

    async def list_plans(self, session_id: str) -> list[dict[str, Any]]:
        """返回摘要列表。"""
        plans = await self._load_all(session_id)
        return [
            {
                "id": p.id,
                "goal": p.goal,
                "summary": p.summary,
                "status": p.status.value if isinstance(p.status, PlanStatus) else p.status,
                "steps_total": len(p.steps),
                "steps_completed": sum(
                    1 for s in p.steps if s.status == StepStatus.COMPLETED
                ),
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            }
            for p in sorted(plans, key=lambda x: x.created_at, reverse=True)
        ]

    async def delete(self, session_id: str, plan_id: str) -> bool:
        """删除计划文件。"""
        path = self._plan_path(session_id, plan_id)
        if path.exists():
            path.unlink()
            logger.info(f"Plan deleted: {path}")
            return True
        return False

    async def _load_all(self, session_id: str) -> list[Plan]:
        """加载指定 session 下所有计划。"""
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return []
        plans: list[Plan] = []
        for fp in session_dir.glob("*.json"):
            try:
                async with aiofiles.open(fp, "r", encoding="utf-8") as f:
                    text = await f.read()
                data = json.loads(text)
                plans.append(Plan.from_dict(data))
            except Exception as exc:
                logger.warning(f"Failed to load plan {fp}: {exc}")
        return plans


# ── 辅助 ──────────────────────────────────────────────────


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
