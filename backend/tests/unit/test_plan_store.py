"""Unit tests for PlanStore — file-based plan CRUD."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from agentpal.plans.store import Plan, PlanStatus, PlanStep, PlanStore, StepStatus


@pytest.fixture
def plans_dir(tmp_path: Path) -> Path:
    """Temporary plans directory."""
    return tmp_path / "plans"


@pytest.fixture
def store(plans_dir: Path) -> PlanStore:
    return PlanStore(plans_dir)


def _make_plan(session_id: str = "web:test-session", plan_id: str = "plan-001") -> Plan:
    return Plan(
        id=plan_id,
        session_id=session_id,
        goal="调研 3 个竞品",
        summary="分三步调研竞品的功能和定价",
        status=PlanStatus.CONFIRMING,
        steps=[
            PlanStep(index=0, title="搜索竞品", description="搜索三个主要竞品", strategy="使用 browser_use", tools=["browser_use"]),
            PlanStep(index=1, title="整理功能", description="整理各竞品核心功能", strategy="文本处理", tools=[]),
            PlanStep(index=2, title="撰写报告", description="撰写竞品分析报告", strategy="生成 Markdown", tools=["write_file"]),
        ],
    )


# ── Save / Load ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_and_load(store: PlanStore) -> None:
    plan = _make_plan()
    await store.save(plan)

    loaded = await store.load("web:test-session", "plan-001")
    assert loaded is not None
    assert loaded.id == "plan-001"
    assert loaded.goal == "调研 3 个竞品"
    assert len(loaded.steps) == 3
    assert loaded.steps[0].title == "搜索竞品"
    assert loaded.status == PlanStatus.CONFIRMING


@pytest.mark.asyncio
async def test_load_nonexistent(store: PlanStore) -> None:
    result = await store.load("no-session", "no-plan")
    assert result is None


# ── get_active ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_active(store: PlanStore) -> None:
    plan = _make_plan()
    plan.status = PlanStatus.EXECUTING
    await store.save(plan)

    active = await store.get_active("web:test-session")
    assert active is not None
    assert active.id == "plan-001"


@pytest.mark.asyncio
async def test_get_active_none_when_completed(store: PlanStore) -> None:
    plan = _make_plan()
    plan.status = PlanStatus.COMPLETED
    await store.save(plan)

    active = await store.get_active("web:test-session")
    assert active is None


# ── list_plans ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_plans(store: PlanStore) -> None:
    plan1 = _make_plan(plan_id="plan-001")
    plan2 = _make_plan(plan_id="plan-002")
    plan2.goal = "第二个计划"
    plan2.status = PlanStatus.COMPLETED
    await store.save(plan1)
    await store.save(plan2)

    plans = await store.list_plans("web:test-session")
    assert len(plans) == 2
    ids = {p["id"] for p in plans}
    assert "plan-001" in ids
    assert "plan-002" in ids


@pytest.mark.asyncio
async def test_list_plans_empty(store: PlanStore) -> None:
    plans = await store.list_plans("nonexistent")
    assert plans == []


# ── delete ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete(store: PlanStore) -> None:
    plan = _make_plan()
    await store.save(plan)

    deleted = await store.delete("web:test-session", "plan-001")
    assert deleted is True

    loaded = await store.load("web:test-session", "plan-001")
    assert loaded is None


@pytest.mark.asyncio
async def test_delete_nonexistent(store: PlanStore) -> None:
    result = await store.delete("no-session", "no-plan")
    assert result is False


# ── Plan data operations ─────────────────────────────────


def test_plan_to_dict_from_dict() -> None:
    plan = _make_plan()
    d = plan.to_dict()
    assert isinstance(d, dict)
    assert d["id"] == "plan-001"
    assert len(d["steps"]) == 3

    restored = Plan.from_dict(d)
    assert restored.id == plan.id
    assert restored.goal == plan.goal
    assert len(restored.steps) == 3
    assert restored.steps[0].title == "搜索竞品"


def test_next_pending_step() -> None:
    plan = _make_plan()
    plan.steps[0].status = StepStatus.COMPLETED
    next_step = plan.next_pending_step()
    assert next_step is not None
    assert next_step.index == 1


def test_all_done() -> None:
    plan = _make_plan()
    assert plan.all_done() is False

    for s in plan.steps:
        s.status = StepStatus.COMPLETED
    assert plan.all_done() is True


def test_mark_step_running() -> None:
    plan = _make_plan()
    plan.mark_step_running(0, "task-123")
    assert plan.steps[0].status == StepStatus.RUNNING
    assert plan.steps[0].task_id == "task-123"
    assert plan.steps[0].started_at is not None
    assert plan.current_step == 0


def test_mark_step_done() -> None:
    plan = _make_plan()
    plan.mark_step_done(0, "搜索完成")
    assert plan.steps[0].status == StepStatus.COMPLETED
    assert plan.steps[0].result == "搜索完成"
    assert plan.steps[0].completed_at is not None


def test_mark_step_failed() -> None:
    plan = _make_plan()
    plan.mark_step_failed(0, "网络超时")
    assert plan.steps[0].status == StepStatus.FAILED
    assert plan.steps[0].error == "网络超时"


# ── Session ID sanitization ──────────────────────────────


@pytest.mark.asyncio
async def test_session_id_with_colons(store: PlanStore) -> None:
    """Session IDs like 'web:uuid' should be safe as directory names."""
    plan = Plan(
        id="plan-special",
        session_id="web:1234-abcd",
        goal="test",
        summary="test plan",
        status=PlanStatus.CONFIRMING,
        steps=[PlanStep(index=0, title="step1", description="desc")],
    )
    await store.save(plan)
    loaded = await store.load("web:1234-abcd", "plan-special")
    assert loaded is not None
    assert loaded.id == "plan-special"
