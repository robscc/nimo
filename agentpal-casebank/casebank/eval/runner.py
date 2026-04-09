"""Evaluation runner over gold cases."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from casebank.cases.gold_manager import GoldManager
from casebank.eval.metrics import MetricsEngine
from casebank.models import CaseResult, MetricsSummary, RunMeta
from casebank.storage.fs_store import FileStore


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class EvalRunner:
    """Run evaluations and persist run artifacts."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.store = FileStore(data_dir)
        self.gold_manager = GoldManager(data_dir)
        self.metrics_engine = MetricsEngine(data_dir)

    def run(
        self,
        suite: str,
        date: str | None = None,
        selected_cases: Optional[list[Any]] = None,
    ) -> tuple[RunMeta, list[CaseResult], MetricsSummary]:
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        run_meta = RunMeta(run_id=run_id, suite=suite)
        run_dir = self.store.create_run_dir(run_id)

        all_gold_cases = self.gold_manager.list_cases("gold")
        gold_cases = selected_cases if selected_cases is not None else all_gold_cases
        task_rows = self._load_rows("task_events", date)
        tool_rows = self._load_rows("tool_logs", date)

        task_state = self._index_tasks(task_rows)
        tools_by_session = self._index_tools_by_session(tool_rows)
        mttr_by_task = self._compute_mttr_by_task(task_rows)

        case_results: list[CaseResult] = []
        for case in gold_cases:
            result = self._evaluate_case(run_id, case, task_state, tools_by_session, mttr_by_task)
            case_results.append(result)

        tool_path_scores = [r.tool_path_match_rate for r in case_results if r.tool_path_match_rate is not None]
        avg_tool_path = (sum(tool_path_scores) / len(tool_path_scores)) if tool_path_scores else None

        summary = self.metrics_engine.compute(run_id=run_id, date=date, tool_path_match_rate=avg_tool_path)

        run_meta.ended_at = datetime.now(timezone.utc).isoformat()
        run_meta_payload = run_meta.model_dump()
        run_meta_payload["selected_case_count"] = len(gold_cases)
        FileStore.write_json(run_dir / "run_meta.json", run_meta_payload)
        for row in case_results:
            FileStore.append_jsonl(run_dir / "case_results.jsonl", row.model_dump())
        FileStore.write_json(run_dir / "metrics.json", summary.model_dump())

        return run_meta, case_results, summary

    def _load_rows(self, bucket: str, date: str | None) -> list[dict[str, Any]]:
        root = self.data_dir / "raw" / bucket
        if not root.exists():
            return []
        files = sorted(root.glob(f"{date}/events.jsonl")) if date else sorted(root.glob("**/events.jsonl"))
        rows: list[dict[str, Any]] = []
        for file in files:
            rows.extend(FileStore.read_jsonl(file))
        return rows

    @staticmethod
    def _index_tasks(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        latest: dict[str, tuple[datetime | None, dict[str, Any]]] = {}
        for row in rows:
            payload = row.get("payload", {})
            if not isinstance(payload, dict):
                continue
            task_id = payload.get("task_id") or payload.get("id")
            if not isinstance(task_id, str):
                continue
            timestamp = _parse_iso(payload.get("completed_at") or payload.get("finished_at") or payload.get("created_at") or row.get("event_time") or row.get("observed_at"))
            previous = latest.get(task_id)
            if previous is None:
                latest[task_id] = (timestamp, payload)
            else:
                prev_ts = previous[0]
                if prev_ts is None or (timestamp is not None and timestamp >= prev_ts):
                    latest[task_id] = (timestamp, payload)
        return {k: v[1] for k, v in latest.items()}

    @staticmethod
    def _index_tools_by_session(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            payload = row.get("payload", {})
            if not isinstance(payload, dict):
                continue
            sid = payload.get("session_id")
            if not isinstance(sid, str):
                continue
            grouped[sid].append(payload)

        for sid, items in grouped.items():
            items.sort(key=lambda x: str(x.get("created_at", "")))
        return grouped

    @staticmethod
    def _compute_mttr_by_task(rows: list[dict[str, Any]]) -> dict[str, float]:
        first_failed: dict[str, datetime] = {}
        first_done_after_failure: dict[str, datetime] = {}

        for row in rows:
            payload = row.get("payload", {})
            if not isinstance(payload, dict):
                continue
            task_id = payload.get("task_id") or payload.get("id")
            if not isinstance(task_id, str):
                continue

            event_type = str(payload.get("event_type", "")).lower()
            status = str(payload.get("status", "")).lower()
            ts = _parse_iso(payload.get("created_at") or row.get("event_time") or row.get("observed_at"))
            if ts is None:
                continue

            if event_type == "task.failed" and task_id not in first_failed:
                first_failed[task_id] = ts
            is_done = event_type == "task.completed" or status == "done"
            if is_done and task_id in first_failed and task_id not in first_done_after_failure and ts >= first_failed[task_id]:
                first_done_after_failure[task_id] = ts

        result: dict[str, float] = {}
        for task_id, fail_ts in first_failed.items():
            done_ts = first_done_after_failure.get(task_id)
            if done_ts:
                result[task_id] = (done_ts - fail_ts).total_seconds()
        return result

    def _evaluate_case(
        self,
        run_id: str,
        case,
        task_state: dict[str, dict[str, Any]],
        tools_by_session: dict[str, list[dict[str, Any]]],
        mttr_by_task: dict[str, float],
    ) -> CaseResult:
        status_payload = task_state.get(case.task_id or "", {})
        status = str(status_payload.get("status", "")).lower()
        retry_count = status_payload.get("retry_count")

        task_success = True if status == "done" else (False if status in {"failed", "cancelled"} else None)
        recovered = bool(task_success and isinstance(retry_count, int) and retry_count > 0) if task_success is not None else None

        execution_accuracy = None
        tool_path_match = None
        session_tools = tools_by_session.get(case.session_id or "")
        if session_tools:
            total = len(session_tools)
            ok = sum(1 for row in session_tools if row.get("error") in (None, ""))
            execution_accuracy = ok / total if total else None

            if case.expected_tools:
                actual = [str(row.get("tool_name", "")) for row in session_tools if row.get("tool_name")]
                expected = case.expected_tools
                match = 0
                for idx, name in enumerate(expected):
                    if idx < len(actual) and actual[idx] == name:
                        match += 1
                tool_path_match = match / len(expected) if expected else None

        tool_accuracy = None
        if execution_accuracy is not None and tool_path_match is not None:
            tool_accuracy = 0.7 * execution_accuracy + 0.3 * tool_path_match
        elif execution_accuracy is not None:
            tool_accuracy = execution_accuracy

        return CaseResult(
            run_id=run_id,
            case_id=case.case_id,
            task_success=task_success,
            execution_accuracy=execution_accuracy,
            tool_path_match_rate=tool_path_match,
            tool_accuracy=tool_accuracy,
            recovered=recovered,
            mttr_seconds=mttr_by_task.get(case.task_id or ""),
            notes=[],
        )
