"""Build candidate cases from raw events."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from casebank.models import CaseRecord
from casebank.storage.fs_store import FileStore


class CandidateBuilder:
    """Rule-based candidate case extractor."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.store = FileStore(data_dir)

    def build(self, date: str | None = None) -> list[CaseRecord]:
        """Extract candidate cases from raw JSONL events."""

        candidates: dict[str, CaseRecord] = {}
        for path in self._raw_files(date):
            rows = FileStore.read_jsonl(path)
            for row in rows:
                maybe = self._event_to_case(row)
                if maybe is None:
                    continue
                candidates[maybe.case_id] = maybe

        for case in candidates.values():
            self.store.write_case("candidate", case.case_id, case.model_dump())
        return sorted(candidates.values(), key=lambda c: c.case_id)

    def _raw_files(self, date: str | None) -> list[Path]:
        root = self.data_dir / "raw"
        if not root.exists():
            return []

        if date:
            return sorted(root.glob(f"*/{date}/events.jsonl"))
        return sorted(root.glob("*/**/events.jsonl"))

    def _event_to_case(self, event: dict[str, Any]) -> CaseRecord | None:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            return None

        labels: list[str] = []
        status = str(payload.get("status", "")).lower()
        error = payload.get("error")
        retry_count = payload.get("retry_count")

        if status in {"failed", "cancelled"}:
            labels.append("task_failure")
        if status == "done" and isinstance(retry_count, int) and retry_count > 0:
            labels.append("retry_recovered")
        if error:
            labels.append("error_present")
        if payload.get("tool_name") and payload.get("error"):
            labels.append("tool_error")
        if payload.get("event_type") in {"task.failed", "task.cancelled"}:
            labels.append("task_event_failure")

        if not labels:
            return None

        case_id = self._build_case_id(event)
        session_id = payload.get("session_id") or payload.get("parent_session_id")
        task_id = payload.get("task_id") or payload.get("id")

        input_snapshot = {
            "task_prompt": payload.get("task_prompt"),
            "message": payload.get("message"),
            "tool_name": payload.get("tool_name"),
            "input": payload.get("input"),
            "status": payload.get("status"),
            "error": payload.get("error"),
        }

        return CaseRecord(
            case_id=case_id,
            state="candidate",
            source="prod",
            session_id=session_id if isinstance(session_id, str) else None,
            task_id=task_id if isinstance(task_id, str) else None,
            input_snapshot={k: v for k, v in input_snapshot.items() if v is not None},
            timeline_refs=[
                {
                    "source": event.get("source"),
                    "observed_at": event.get("observed_at"),
                    "event_time": event.get("event_time"),
                    "payload_hash": event.get("payload_hash"),
                }
            ],
            labels=sorted(set(labels)),
            difficulty="medium",
        )

    @staticmethod
    def _build_case_id(event: dict[str, Any]) -> str:
        payload_hash = str(event.get("payload_hash", ""))
        source = str(event.get("source", ""))
        raw = f"{source}:{payload_hash}".encode("utf-8")
        return "case_" + hashlib.sha1(raw).hexdigest()[:16]
