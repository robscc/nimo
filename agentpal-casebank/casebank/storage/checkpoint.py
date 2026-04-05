"""Checkpoint and dedup index persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from casebank.storage.fs_store import FileStore


class CheckpointStore:
    """Persists collector cursors and run metadata."""

    def __init__(self, root: Path) -> None:
        self.file = root / "checkpoints" / "collectors.json"

    def load(self) -> dict[str, Any]:
        return FileStore.read_json(self.file, default={})

    def save(self, data: dict[str, Any]) -> None:
        FileStore.write_json(self.file, data)


class DedupIndex:
    """Simple hash index for idempotent ingestion."""

    def __init__(self, root: Path) -> None:
        self.file = root / "checkpoints" / "dedup_index.json"

    def load(self) -> dict[str, list[str]]:
        raw = FileStore.read_json(self.file, default={})
        return {k: list(v) for k, v in raw.items()}

    def save(self, data: dict[str, list[str]]) -> None:
        FileStore.write_json(self.file, data)

    def seen(self, index: dict[str, list[str]], source: str, payload_hash: str) -> bool:
        return payload_hash in set(index.get(source, []))

    def add(self, index: dict[str, list[str]], source: str, payload_hash: str, max_per_source: int = 5000) -> None:
        bucket = index.setdefault(source, [])
        if payload_hash in bucket:
            return
        bucket.append(payload_hash)
        if len(bucket) > max_per_source:
            del bucket[: len(bucket) - max_per_source]
