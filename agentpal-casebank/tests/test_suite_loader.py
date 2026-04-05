import json
from pathlib import Path

from casebank.suites.suite_loader import SuiteSpec, filter_cases, load_suite_file


class _Case:
    def __init__(self, case_id: str, labels: list[str]) -> None:
        self.case_id = case_id
        self.labels = labels


def test_load_suite_and_filter_cases(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "name": "smoke",
                "include_case_ids": ["case_1", "case_2"],
                "include_labels": ["task_failure"],
                "exclude_case_ids": ["case_2"],
            }
        ),
        encoding="utf-8",
    )

    spec = load_suite_file(suite_path)
    assert spec.name == "smoke"

    cases = [_Case("case_1", ["task_failure"]), _Case("case_2", ["task_failure"]), _Case("case_3", ["tool_error"])]
    selected = filter_cases(cases, spec)

    assert [c.case_id for c in selected] == ["case_1"]
