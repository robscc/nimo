"""Suite loading and case selection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field


class SuiteSpec(BaseModel):
    """Defines which gold cases to include in an eval run."""

    name: str = "custom"
    include_case_ids: List[str] = Field(default_factory=list)
    include_labels: List[str] = Field(default_factory=list)
    exclude_case_ids: List[str] = Field(default_factory=list)


def load_suite_file(path: Path) -> SuiteSpec:
    """Load suite spec from JSON file."""

    data = json.loads(path.read_text(encoding="utf-8"))
    return SuiteSpec.model_validate(data)


def filter_cases(cases: list, spec: SuiteSpec) -> list:
    """Filter case objects by suite include/exclude rules."""

    selected = list(cases)

    if spec.include_case_ids:
        include_set = set(spec.include_case_ids)
        selected = [c for c in selected if c.case_id in include_set]

    if spec.include_labels:
        label_set = set(spec.include_labels)
        selected = [c for c in selected if label_set.intersection(set(c.labels))]

    if spec.exclude_case_ids:
        exclude_set = set(spec.exclude_case_ids)
        selected = [c for c in selected if c.case_id not in exclude_set]

    return selected
