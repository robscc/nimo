"""Normalize inbound payloads into RawEvent records."""

from __future__ import annotations

from typing import Any

from casebank.models import RawEvent
from casebank.storage.fs_store import FileStore


def _extract_entity(payload: dict[str, Any]) -> tuple[str, str | None]:
    if "task_id" in payload:
        return "task", str(payload.get("task_id"))
    if "session_id" in payload:
        return "session", str(payload.get("session_id"))
    if "id" in payload:
        return "generic", str(payload.get("id"))
    return "generic", None


def _extract_event_time(payload: dict[str, Any]) -> str | None:
    for key in ("created_at", "timestamp", "started_at", "finished_at", "updated_at"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def normalize_raw_event(source: str, payload: dict[str, Any], source_event_id: str | None = None) -> RawEvent:
    """Create canonical RawEvent from source payload."""

    entity_type, entity_id = _extract_entity(payload)
    event_time = _extract_event_time(payload)
    return RawEvent(
        source=source,
        entity_type=entity_type,
        entity_id=entity_id,
        source_event_id=source_event_id,
        event_time=event_time,
        payload_hash=FileStore.payload_hash(payload),
        payload=payload,
    )
