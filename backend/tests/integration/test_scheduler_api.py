"""集成测试 — Scheduler API 端点。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from agentpal.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.mark.asyncio
class TestSchedulerAPI:
    """Scheduler REST API 测试。"""

    async def test_list_agents(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/scheduler/agents")
            # Scheduler 未启动时应返回 503，lifespan 启动后返回 200
            assert resp.status_code in (200, 503)

    async def test_get_stats(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/scheduler/stats")
            assert resp.status_code in (200, 503)

    async def test_stop_nonexistent_agent(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/scheduler/agents/nonexistent/stop")
            # 503 if scheduler not available, 404 if agent not found
            assert resp.status_code in (404, 503)
