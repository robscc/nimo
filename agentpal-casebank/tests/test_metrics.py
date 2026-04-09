from pathlib import Path

from casebank.eval.metrics import MetricsEngine
from casebank.storage.fs_store import FileStore


def test_metrics_engine_core_kpis(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.bootstrap()

    task_file = tmp_path / "raw" / "task_events" / "2026-04-05" / "events.jsonl"
    # task-1 failed then completed (recovered)
    store.append_jsonl(
        task_file,
        {
            "observed_at": "2026-04-05T10:00:00+00:00",
            "event_time": "2026-04-05T10:00:00+00:00",
            "source": "task_sse",
            "payload_hash": "1",
            "payload": {"task_id": "task-1", "event_type": "task.failed", "created_at": "2026-04-05T10:00:00+00:00", "status": "failed"},
        },
    )
    store.append_jsonl(
        task_file,
        {
            "observed_at": "2026-04-05T10:02:00+00:00",
            "event_time": "2026-04-05T10:02:00+00:00",
            "source": "task_sse",
            "payload_hash": "2",
            "payload": {"task_id": "task-1", "event_type": "task.completed", "created_at": "2026-04-05T10:02:00+00:00", "status": "done", "retry_count": 1},
        },
    )
    # task-2 failed terminal
    store.append_jsonl(
        task_file,
        {
            "observed_at": "2026-04-05T10:03:00+00:00",
            "event_time": "2026-04-05T10:03:00+00:00",
            "source": "task_sse",
            "payload_hash": "3",
            "payload": {"task_id": "task-2", "created_at": "2026-04-05T10:03:00+00:00", "status": "failed"},
        },
    )

    tool_file = tmp_path / "raw" / "tool_logs" / "2026-04-05" / "events.jsonl"
    store.append_jsonl(
        tool_file,
        {
            "observed_at": "2026-04-05T10:01:00+00:00",
            "source": "tool_logs_pull",
            "payload_hash": "4",
            "payload": {"session_id": "web:1", "tool_name": "read_file", "error": None},
        },
    )
    store.append_jsonl(
        tool_file,
        {
            "observed_at": "2026-04-05T10:01:05+00:00",
            "source": "tool_logs_pull",
            "payload_hash": "5",
            "payload": {"session_id": "web:1", "tool_name": "write_file", "error": "permission denied"},
        },
    )

    engine = MetricsEngine(tmp_path)
    summary = engine.compute(run_id="run_test", date="2026-04-05", tool_path_match_rate=0.5)

    assert summary.sample_size_tasks == 2
    assert summary.task_success_rate == 0.5
    assert summary.execution_accuracy == 0.5
    assert summary.tool_accuracy == 0.5  # 0.7*0.5 + 0.3*0.5
    assert summary.recovery_rate == 0.5
    assert summary.mttr_seconds == 120.0
