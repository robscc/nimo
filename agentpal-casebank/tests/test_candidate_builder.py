from pathlib import Path

from casebank.cases.candidate_builder import CandidateBuilder
from casebank.storage.fs_store import FileStore


def test_candidate_builder_extracts_failure_case(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.bootstrap()

    raw_path = tmp_path / "raw" / "task_events" / "2026-04-05" / "events.jsonl"
    event = {
        "ingest_id": "1",
        "observed_at": "2026-04-05T10:00:00+00:00",
        "source": "agent_tasks_pull",
        "entity_type": "task",
        "entity_id": "task-1",
        "payload_hash": "hash-1",
        "payload": {
            "task_id": "task-1",
            "session_id": "web:1",
            "status": "failed",
            "task_prompt": "do something",
            "error": "boom",
        },
    }
    store.append_jsonl(raw_path, event)

    builder = CandidateBuilder(tmp_path)
    rows = builder.build(date="2026-04-05")

    assert len(rows) == 1
    case = rows[0]
    assert case.task_id == "task-1"
    assert "task_failure" in case.labels
    assert "error_present" in case.labels

    saved = FileStore.read_json(tmp_path / "cases" / "candidate" / f"{case.case_id}.json")
    assert saved["state"] == "candidate"
