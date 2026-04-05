from pathlib import Path

from casebank.cases.gold_manager import GoldManager
from casebank.storage.fs_store import FileStore


def test_gold_manager_promote(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    store.bootstrap()

    candidate = {
        "case_id": "case_abc",
        "state": "candidate",
        "source": "prod",
        "labels": ["task_failure"],
        "input_snapshot": {},
        "timeline_refs": [],
        "difficulty": "medium",
        "created_at": "2026-04-05T00:00:00+00:00",
    }
    store.write_case("candidate", "case_abc", candidate)

    manager = GoldManager(tmp_path)
    case = manager.promote(
        case_id="case_abc",
        expected_outcome="task should be completed",
        reviewer="qa",
        expected_tools=["read_file"],
    )

    assert case.state == "gold"
    assert case.expected_outcome == "task should be completed"

    saved = FileStore.read_json(tmp_path / "cases" / "gold" / "case_abc.json")
    assert saved["state"] == "gold"
    assert saved["reviewer"] == "qa"
