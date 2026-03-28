"""Plan Mode API 集成测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.config import get_settings
from agentpal.database import Base, get_db, get_db_standalone
from agentpal.main import create_app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_app(tmp_path):
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    get_settings.cache_clear()
    original_settings = get_settings()
    test_workspace = str(tmp_path / ".nimo")
    test_plans_dir = str(tmp_path / "plans")

    object.__setattr__(original_settings, "workspace_dir", test_workspace)
    object.__setattr__(original_settings, "plans_dir", test_plans_dir)

    app = create_app()

    async def override_db():
        async with session_factory() as session:
            yield session
            await session.rollback()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_db_standalone] = override_db
    yield app

    get_settings.cache_clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


# ── Helper ────────────────────────────────────────────────

async def _create_session(client: AsyncClient) -> str:
    resp = await client.post("/api/v1/sessions?channel=web")
    assert resp.status_code == 201
    return resp.json()["id"]


async def _seed_plan(session_id: str, plan_id: str = "test-plan-001") -> None:
    """Directly seed a plan file for testing."""
    from agentpal.plans.store import Plan, PlanStatus, PlanStep, PlanStore

    settings = get_settings()
    store = PlanStore(settings.plans_dir)
    plan = Plan(
        id=plan_id,
        session_id=session_id,
        goal="调研竞品",
        summary="分三步调研",
        status=PlanStatus.EXECUTING,
        steps=[
            PlanStep(index=0, title="搜索", description="搜索竞品", status="completed", result="找到3个"),
            PlanStep(index=1, title="整理", description="整理功能", status="running"),
            PlanStep(index=2, title="报告", description="撰写报告"),
        ],
    )
    await store.save(plan)


# ── GET /sessions/{id}/plan ──────────────────────────────


@pytest.mark.asyncio
async def test_get_active_plan_none(client: AsyncClient) -> None:
    session_id = await _create_session(client)
    resp = await client.get(f"/api/v1/sessions/{session_id}/plan")
    assert resp.status_code == 200
    assert resp.json()["plan"] is None


@pytest.mark.asyncio
async def test_get_active_plan(client: AsyncClient) -> None:
    session_id = await _create_session(client)
    await _seed_plan(session_id)

    resp = await client.get(f"/api/v1/sessions/{session_id}/plan")
    assert resp.status_code == 200
    plan = resp.json()["plan"]
    assert plan is not None
    assert plan["id"] == "test-plan-001"
    assert plan["goal"] == "调研竞品"
    assert len(plan["steps"]) == 3


# ── GET /sessions/{id}/plans ─────────────────────────────


@pytest.mark.asyncio
async def test_list_plans_empty(client: AsyncClient) -> None:
    session_id = await _create_session(client)
    resp = await client.get(f"/api/v1/sessions/{session_id}/plans")
    assert resp.status_code == 200
    assert resp.json()["plans"] == []


@pytest.mark.asyncio
async def test_list_plans(client: AsyncClient) -> None:
    session_id = await _create_session(client)
    await _seed_plan(session_id, "plan-a")
    await _seed_plan(session_id, "plan-b")

    resp = await client.get(f"/api/v1/sessions/{session_id}/plans")
    assert resp.status_code == 200
    plans = resp.json()["plans"]
    assert len(plans) == 2
    ids = {p["id"] for p in plans}
    assert "plan-a" in ids
    assert "plan-b" in ids


# ── GET /sessions/{id}/plans/{plan_id} ───────────────────


@pytest.mark.asyncio
async def test_get_plan_detail(client: AsyncClient) -> None:
    session_id = await _create_session(client)
    await _seed_plan(session_id)

    resp = await client.get(f"/api/v1/sessions/{session_id}/plans/test-plan-001")
    assert resp.status_code == 200
    plan = resp.json()["plan"]
    assert plan["goal"] == "调研竞品"
    assert plan["steps"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_get_plan_detail_not_found(client: AsyncClient) -> None:
    session_id = await _create_session(client)
    resp = await client.get(f"/api/v1/sessions/{session_id}/plans/nonexistent")
    assert resp.status_code == 404


# ── Session Meta — agent_mode ────────────────────────────


@pytest.mark.asyncio
async def test_session_meta_includes_agent_mode(client: AsyncClient) -> None:
    session_id = await _create_session(client)
    resp = await client.get(f"/api/v1/sessions/{session_id}/meta")
    assert resp.status_code == 200
    data = resp.json()
    assert "agent_mode" in data
    assert data["agent_mode"] == "normal"
    assert data["active_plan_id"] is None
