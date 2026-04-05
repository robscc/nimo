"""Collector orchestration for streaming + pull ingestion."""

from __future__ import annotations

import asyncio
from typing import Any

from casebank.collectors.rest_puller import RestPuller
from casebank.collectors.sse_client import SSEClient
from casebank.collectors.ws_client import WSClient
from casebank.config import CaseBankConfig
from casebank.models import utc_now_iso
from casebank.normalize.normalizer import normalize_raw_event
from casebank.storage.checkpoint import CheckpointStore, DedupIndex
from casebank.storage.fs_store import FileStore


class CollectorOrchestrator:
    """Coordinates data ingestion from AgentPal APIs."""

    def __init__(self, config: CaseBankConfig) -> None:
        self.config = config
        self.store = FileStore(config.data_dir)
        self.checkpoints = CheckpointStore(config.data_dir)
        self.dedup_index = DedupIndex(config.data_dir)

        self.puller = RestPuller(
            base_url=config.base_url,
            timeout_seconds=config.collector.request_timeout_seconds,
        )
        self.sse = SSEClient(timeout_seconds=config.collector.request_timeout_seconds)
        self.ws = WSClient()

        self._state = self.checkpoints.load()
        self._dedup_state = self.dedup_index.load()
        self._session_ids: set[str] = set(self._state.get("session_ids", []))
        self._task_ids: set[str] = set(self._state.get("task_ids", []))

        self._global_stream_tasks: dict[str, asyncio.Task] = {}
        self._session_stream_tasks: dict[str, asyncio.Task] = {}
        self._task_stream_tasks: dict[str, asyncio.Task] = {}

    async def start_forever(self) -> None:
        """Start long-running ingest service."""

        self.store.bootstrap()
        await self.backfill_once()
        await self._ensure_global_streams()
        await self._ensure_entity_streams()

        while True:
            await asyncio.sleep(self.config.collector.poll_interval_seconds)
            await self.backfill_once()
            await self._ensure_entity_streams()

    async def backfill_once(self) -> None:
        """Pull periodic snapshots and write normalized raw events."""

        sessions = await self.puller.list_sessions(limit=self.config.backfill.sessions_limit)
        for session in sessions:
            sid = session.get("id")
            if isinstance(sid, str):
                self._session_ids.add(sid)
            await self._ingest("sessions_pull", "session_events", session, source_event_id=sid)

            if not sid:
                continue
            try:
                messages = await self.puller.get_session_messages(sid)
                for msg in messages:
                    source_event_id = f"{sid}:{msg.get('created_at')}:{msg.get('role')}"
                    await self._ingest("session_messages_pull", "session_events", msg, source_event_id=source_event_id)
            except Exception:
                pass

            try:
                usage = await self.puller.get_session_usage(sid)
                await self._ingest("session_usage_pull", "usage", usage, source_event_id=sid)
            except Exception:
                pass

            try:
                sub_tasks = await self.puller.list_session_sub_tasks(sid)
                for task in sub_tasks:
                    tid = task.get("id")
                    if isinstance(tid, str):
                        self._task_ids.add(tid)
                    await self._ingest("session_sub_tasks_pull", "task_events", task, source_event_id=tid)
            except Exception:
                pass

        tasks_resp = await self.puller.list_agent_tasks(limit=self.config.backfill.sessions_limit)
        for task in tasks_resp.get("items", []):
            tid = task.get("task_id") or task.get("id")
            if isinstance(tid, str):
                self._task_ids.add(tid)
            await self._ingest("agent_tasks_pull", "task_events", task, source_event_id=tid)

            if not tid:
                continue
            try:
                detail = await self.puller.get_task(tid)
                await self._ingest("task_detail_pull", "task_events", detail, source_event_id=tid)
            except Exception:
                pass

        try:
            tool_logs = await self.puller.list_tool_logs(limit=self.config.backfill.tool_logs_limit)
            for log in tool_logs:
                await self._ingest("tool_logs_pull", "tool_logs", log, source_event_id=log.get("id"))
        except Exception:
            pass

        try:
            cron_execs = await self.puller.list_cron_executions(limit=self.config.backfill.cron_limit)
            for row in cron_execs:
                execution_id = row.get("id")
                await self._ingest("cron_exec_pull", "cron_exec", row, source_event_id=execution_id)
                if execution_id:
                    try:
                        detail = await self.puller.get_cron_execution_detail(execution_id)
                        await self._ingest(
                            "cron_exec_detail_pull",
                            "cron_exec",
                            detail,
                            source_event_id=execution_id,
                        )
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            scheduler_stats = await self.puller.get_scheduler_stats()
            await self._ingest("scheduler_stats_pull", "scheduler_events", scheduler_stats)
        except Exception:
            pass

        self._persist_local_state()

    async def _ensure_global_streams(self) -> None:
        if "scheduler" not in self._global_stream_tasks:
            url = f"{self.config.base_url}/scheduler/events"
            self._global_stream_tasks["scheduler"] = asyncio.create_task(
                self._run_sse_stream(url, "scheduler_sse", "scheduler_events")
            )

        if "notifications" not in self._global_stream_tasks:
            ws_url = self._build_notifications_ws_url(self.config.base_url)
            self._global_stream_tasks["notifications"] = asyncio.create_task(
                self._run_ws_stream(ws_url, "notifications_ws", "notifications")
            )

    async def _ensure_entity_streams(self) -> None:
        for session_id in sorted(self._session_ids):
            if session_id in self._session_stream_tasks:
                continue
            url = f"{self.config.base_url}/sessions/{session_id}/events"
            self._session_stream_tasks[session_id] = asyncio.create_task(
                self._run_sse_stream(url, "session_sse", "session_events")
            )

        for task_id in sorted(self._task_ids):
            if task_id in self._task_stream_tasks:
                continue
            url = f"{self.config.base_url}/tasks/{task_id}/events"
            self._task_stream_tasks[task_id] = asyncio.create_task(
                self._run_sse_stream(url, "task_sse", "task_events")
            )

    async def _run_sse_stream(self, url: str, source: str, bucket: str) -> None:
        async def callback(event: dict[str, Any]) -> None:
            source_event_id = self._derive_source_event_id(event)
            await self._ingest(source, bucket, event, source_event_id=source_event_id)

        await self.sse.consume_forever(
            url=url,
            callback=callback,
            reconnect_delay_seconds=self.config.collector.reconnect_delay_seconds,
        )

    async def _run_ws_stream(self, url: str, source: str, bucket: str) -> None:
        async def callback(event: dict[str, Any]) -> None:
            source_event_id = self._derive_source_event_id(event)
            await self._ingest(source, bucket, event, source_event_id=source_event_id)

        await self.ws.consume_forever(
            url=url,
            callback=callback,
            reconnect_delay_seconds=self.config.collector.reconnect_delay_seconds,
        )

    async def _ingest(
        self,
        source: str,
        bucket: str,
        payload: dict[str, Any],
        source_event_id: str | None = None,
    ) -> None:
        raw = normalize_raw_event(source=source, payload=payload, source_event_id=source_event_id)
        if self.dedup_index.seen(self._dedup_state, source, raw.payload_hash):
            return

        day = (raw.event_time or raw.observed_at)[:10]
        path = self.config.data_dir / "raw" / bucket / day / "events.jsonl"
        FileStore.append_jsonl(path, raw.model_dump())

        self.dedup_index.add(self._dedup_state, source, raw.payload_hash)

    def _persist_local_state(self) -> None:
        self.checkpoints.save(
            {
                "updated_at": utc_now_iso(),
                "session_ids": sorted(self._session_ids),
                "task_ids": sorted(self._task_ids),
            }
        )
        self.dedup_index.save(self._dedup_state)

    @staticmethod
    def _derive_source_event_id(payload: dict[str, Any]) -> str | None:
        for key in ("id", "task_id", "session_id", "execution_id"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        created = payload.get("created_at") or payload.get("timestamp")
        typ = payload.get("type") or payload.get("event_type")
        if created and typ:
            return f"{typ}:{created}"
        return None

    @staticmethod
    def _build_notifications_ws_url(base_url: str) -> str:
        root = base_url
        if "/api/v1" in root:
            root = root.split("/api/v1", maxsplit=1)[0]
        if root.startswith("https://"):
            root = "wss://" + root[len("https://") :]
        elif root.startswith("http://"):
            root = "ws://" + root[len("http://") :]
        return root.rstrip("/") + "/api/v1/notifications/ws"
