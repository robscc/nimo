"""Manage case promotion from candidate to gold."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from casebank.models import CaseRecord, utc_now_iso
from casebank.storage.fs_store import FileStore


class GoldManager:
    """Promote and list case records."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.store = FileStore(data_dir)

    def list_cases(self, state: str) -> list[CaseRecord]:
        cases: list[CaseRecord] = []
        for path in self.store.list_case_files(state):
            data = FileStore.read_json(path)
            cases.append(CaseRecord.model_validate(data))
        return sorted(cases, key=lambda c: c.case_id)

    def promote(
        self,
        case_id: str,
        expected_outcome: str,
        reviewer: str,
        expected_tools: list[str] | None = None,
    ) -> CaseRecord:
        candidate_path = self.data_dir / "cases" / "candidate" / f"{case_id}.json"
        if not candidate_path.exists():
            raise FileNotFoundError(f"candidate case not found: {case_id}")

        case = CaseRecord.model_validate(FileStore.read_json(candidate_path))
        case.state = "gold"
        case.expected_outcome = expected_outcome
        case.expected_tools = expected_tools
        case.promoted_at = utc_now_iso()
        case.reviewer = reviewer

        self.store.write_case("gold", case.case_id, case.model_dump())
        return case
