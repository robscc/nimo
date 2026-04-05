"""Agent 文件上传 API 集成测试。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agentpal.api.v1.endpoints import agent as agent_endpoint
from agentpal.database import Base, get_db, get_db_standalone
from agentpal.main import create_app
from agentpal.models.session import TaskArtifact

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def app_client(tmp_path: Path):
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    app = create_app()

    async def override_db() -> AsyncGenerator:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_db_standalone] = override_db

    upload_dir = tmp_path / "uploads" / "chat"
    upload_dir.mkdir(parents=True, exist_ok=True)

    original_get_upload_dir = agent_endpoint._get_upload_dir
    agent_endpoint._get_upload_dir = lambda: upload_dir

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, session_factory, upload_dir

    agent_endpoint._get_upload_dir = original_get_upload_dir
    await engine.dispose()


class TestAgentFileUpload:
    @pytest.mark.asyncio
    async def test_upload_success(self, app_client):
        client, session_factory, upload_dir = app_client

        resp = await client.post(
            "/api/v1/agent/files/upload",
            data={"session_id": "session-upload-1"},
            files={"file": ("notes.txt", b"hello upload", "text/plain")},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "ready"
        assert payload["name"] == "notes.txt"
        assert payload["mime_type"] == "text/plain"
        assert payload["size_bytes"] == len(b"hello upload")
        assert payload["file_id"]

        async with session_factory() as db:
            artifact = await db.get(TaskArtifact, payload["file_id"])
            assert artifact is not None
            assert artifact.artifact_type == "uploaded_file"
            assert artifact.task_id == "upload:session-upload-1"
            assert artifact.name == "notes.txt"
            assert artifact.size_bytes == len(b"hello upload")
            assert artifact.file_path is not None
            assert artifact.extra is not None
            assert artifact.extra["session_id"] == "session-upload-1"
            assert artifact.extra["source"] == "chat_upload"
            assert artifact.extra["sha256"]
            assert artifact.extra["stored_name"]

            stored_path = Path(artifact.file_path)
            assert stored_path.exists()
            assert upload_dir in stored_path.parents

    @pytest.mark.asyncio
    async def test_upload_blocked_extension(self, app_client):
        client, _, _ = app_client

        resp = await client.post(
            "/api/v1/agent/files/upload",
            data={"session_id": "session-upload-2"},
            files={"file": ("bad.exe", b"MZ", "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "不允许上传" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_upload_oversize(self, app_client):
        client, session_factory, _ = app_client

        too_large = b"a" * (25 * 1024 * 1024 + 1)
        resp = await client.post(
            "/api/v1/agent/files/upload",
            data={"session_id": "session-upload-3"},
            files={"file": ("large.txt", too_large, "text/plain")},
        )
        assert resp.status_code == 413

        async with session_factory() as db:
            rows = await db.execute(select(TaskArtifact).where(TaskArtifact.task_id == "upload:session-upload-3"))
            assert rows.scalars().first() is None

    @pytest.mark.asyncio
    async def test_chat_accepts_file_ids_and_forwards_to_assistant_direct_mode(self, app_client, monkeypatch):
        client, session_factory, _ = app_client
        captured_calls: list[dict[str, object]] = []

        class FakePersonalAssistant:
            def __init__(self, session_id: str, memory, db):
                self.session_id = session_id

            async def reply_stream(self, message: str, images=None, file_ids=None):
                captured_calls.append(
                    {
                        "session_id": self.session_id,
                        "message": message,
                        "images": images,
                        "file_ids": file_ids,
                    }
                )
                yield {"type": "text_delta", "delta": "fake direct reply"}
                yield {"type": "done"}

            def cancel(self):
                return None

        monkeypatch.setattr(agent_endpoint, "_get_zmq_manager", lambda request: None)
        monkeypatch.setattr(agent_endpoint, "PersonalAssistant", FakePersonalAssistant)

        original_ensure_session = agent_endpoint._ensure_session

        async def fake_ensure_session(db, session_id: str, channel: str) -> None:
            async with session_factory() as test_db:
                await original_ensure_session(test_db, session_id, channel)

        monkeypatch.setattr(agent_endpoint, "_ensure_session", fake_ensure_session)

        resp = await client.post(
            "/api/v1/agent/chat",
            json={
                "session_id": "session-chat-file-1",
                "message": "please analyze uploaded files",
                "file_ids": ["artifact-1", "artifact-2"],
            },
            headers={"Accept": "text/event-stream"},
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        events = [
            json.loads(line.removeprefix("data: "))
            for line in resp.text.splitlines()
            if line.startswith("data: ")
        ]
        assert any(e.get("type") == "text_delta" and e.get("delta") == "fake direct reply" for e in events)
        assert any(e.get("type") == "done" for e in events)

        assert len(captured_calls) == 1
        assert captured_calls[0]["message"] == "please analyze uploaded files"
        assert captured_calls[0]["file_ids"] == ["artifact-1", "artifact-2"]

