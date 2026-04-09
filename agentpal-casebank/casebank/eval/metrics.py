"""Metric computation for task success, tool accuracy and stability/recovery."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from casebank.models import MetricsSummary
from casebank.storage.fs_store import FileStore


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class MetricsEngine:
    """Computes the three MVP KPIs from raw data files."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def compute(self, run_id: str, date: str | None = None, tool_path_match_rate: float | None = None) -> MetricsSummary:
        task_rows = self._load_raw_bucket("task_events", date)
        tool_rows = self._load_raw_bucket("tool_logs", date)

        task_success_rate, terminal_total, incident_count, recovery_rate, mttr = self._task_metrics(task_rows)
        execution_accuracy, tool_calls = self._tool_metrics(tool_rows)

        if tool_path_match_rate is None:
            tool_accuracy = execution_accuracy
        else:
            tool_accuracy = 0.7 * execution_accuracy + 0.3 * tool_path_match_rate

        incident_rate = (incident_count / terminal_total * 100) if terminal_total else 0.0

        # Stability score in [0,1] with simple normalized combination.
        # Lower incident_rate and MTTR are better.
        incident_component = max(0.0, 1.0 - (incident_rate / 100.0))
        if mttr is None:
            mttr_component = 1.0
        else:
            mttr_component = max(0.0, 1.0 - min(mttr / 3600.0, 1.0))
        stability_score = 0.4 * incident_component + 0.4 * recovery_rate + 0.2 * mttr_component

        return MetricsSummary(
            run_id=run_id,
            task_success_rate=round(task_success_rate, 4),
            execution_accuracy=round(execution_accuracy, 4),
            tool_path_match_rate=round(tool_path_match_rate, 4) if tool_path_match_rate is not None else None,
            tool_accuracy=round(tool_accuracy, 4),
            incident_rate_per_100_tasks=round(incident_rate, 4),
            recovery_rate=round(recovery_rate, 4),
            mttr_seconds=round(mttr, 4) if mttr is not None else None,
            stability_score=round(stability_score, 4),
            sample_size_tasks=terminal_total,
            sample_size_tool_calls=tool_calls,
        )

    def _load_raw_bucket(self, bucket: str, date: str | None) -> list[dict[str, Any]]:
        root = self.data_dir / "raw" / bucket
        if not root.exists():
            return []

        rows: list[dict[str, Any]] = []
        if date:
            files = sorted(root.glob(f"{date}/events.jsonl"))
        else:
            files = sorted(root.glob("**/events.jsonl"))

        for file in files:
            rows.extend(FileStore.read_jsonl(file))
        return rows

    def _task_metrics(self, task_rows: list[dict[str, Any]]) -> tuple[float, int, int, float, float | None]:
        # Use latest terminal state per task_id.
        latest_by_task: dict[str, dict[str, Any]] = {}
        first_failure_time: dict[str, datetime] = {}
        done_time: dict[str, datetime] = {}

        for row in task_rows:
            payload = row.get("payload", {})
            if not isinstance(payload, dict):
                continue

            task_id = payload.get("task_id") or payload.get("id")
            if not isinstance(task_id, str):
                continue

            status = str(payload.get("status", "")).lower()
            event_type = str(payload.get("event_type", "")).lower()
            created = _parse_iso(payload.get("created_at") or row.get("event_time") or row.get("observed_at"))

            if status in {"done", "failed", "cancelled"}:
                prev = latest_by_task.get(task_id)
                if prev is None:
                    latest_by_task[task_id] = payload
                else:
                    prev_time = _parse_iso(prev.get("completed_at") or prev.get("finished_at") or prev.get("created_at"))
                    if prev_time is None or (created is not None and created >= prev_time):
                        latest_by_task[task_id] = payload

            if event_type == "task.failed" and created is not None and task_id not in first_failure_time:
                first_failure_time[task_id] = created
            if (event_type == "task.completed" or status == "done") and created is not None:
                done_time[task_id] = created

        terminal_tasks = list(latest_by_task.values())
        total_terminal = len(terminal_tasks)
        if total_terminal == 0:
            return 0.0, 0, 0, 0.0, None

        done_count = sum(1 for p in terminal_tasks if str(p.get("status", "")).lower() == "done")

        incidents = 0
        recovered = 0
        for payload in terminal_tasks:
            status = str(payload.get("status", "")).lower()
            retry_count = payload.get("retry_count")
            has_incident = status in {"failed", "cancelled"} or (isinstance(retry_count, int) and retry_count > 0)
            if has_incident:
                incidents += 1
            if has_incident and status == "done":
                recovered += 1

        mttr_samples: list[float] = []
        for task_id, failed_at in first_failure_time.items():
            completed_at = done_time.get(task_id)
            if completed_at and completed_at >= failed_at:
                mttr_samples.append((completed_at - failed_at).total_seconds())

        mttr = median(mttr_samples) if mttr_samples else None
        success_rate = done_count / total_terminal
        recovery_rate = recovered / incidents if incidents else 0.0
        return success_rate, total_terminal, incidents, recovery_rate, mttr

    def _tool_metrics(self, tool_rows: list[dict[str, Any]]) -> tuple[float, int]:
        total = 0
        ok = 0

        for row in tool_rows:
            payload = row.get("payload", {})
            if not isinstance(payload, dict):
                continue
            if not payload.get("tool_name"):
                continue
            total += 1
            if payload.get("error") in (None, ""):
                ok += 1

        if total == 0:
            return 0.0, 0
        return ok / total, total
