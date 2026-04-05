"""JSON/JSONL file-based storage utilities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


class FileStore:
    """Persistent file store for raw events, cases, runs and reports."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def bootstrap(self) -> None:
        """Create the required directory layout."""

        paths = [
            self.root / "raw" / "session_events",
            self.root / "raw" / "task_events",
            self.root / "raw" / "notifications",
            self.root / "raw" / "scheduler_events",
            self.root / "raw" / "tool_logs",
            self.root / "raw" / "cron_exec",
            self.root / "raw" / "usage",
            self.root / "cases" / "candidate",
            self.root / "cases" / "gold",
            self.root / "runs",
            self.root / "checkpoints",
        ]
        for p in paths:
            p.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def payload_hash(payload: dict[str, Any]) -> str:
        """Stable hash used for deduplication."""

        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def append_jsonl(path: Path, row: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        return rows

    @staticmethod
    def write_json(path: Path, obj: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    @staticmethod
    def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
        if not path.exists():
            return default or {}
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def write_case(self, state: str, case_id: str, obj: dict[str, Any]) -> Path:
        """Write one candidate/gold case JSON."""

        target = self.root / "cases" / state / f"{case_id}.json"
        self.write_json(target, obj)
        return target

    def list_case_files(self, state: str) -> Iterable[Path]:
        """Yield case files sorted by filename."""

        directory = self.root / "cases" / state
        if not directory.exists():
            return []
        return sorted(directory.glob("*.json"))

    def create_run_dir(self, run_id: str) -> Path:
        run_dir = self.root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir
