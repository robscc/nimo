"""KPI gate validation for eval runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

from casebank.models import MetricsSummary


class KpiGates(BaseModel):
    """Thresholds that metrics must satisfy."""

    min_task_success_rate: Optional[float] = None
    min_tool_accuracy: Optional[float] = None
    min_stability_score: Optional[float] = None


def load_gates_file(path: Path) -> KpiGates:
    data = json.loads(path.read_text(encoding="utf-8"))
    return KpiGates.model_validate(data)


def evaluate_gates(summary: MetricsSummary, gates: KpiGates) -> List[str]:
    """Return violations; empty list means pass."""

    failures: List[str] = []
    if gates.min_task_success_rate is not None and summary.task_success_rate < gates.min_task_success_rate:
        failures.append(
            f"task_success_rate {summary.task_success_rate:.4f} < {gates.min_task_success_rate:.4f}"
        )
    if gates.min_tool_accuracy is not None and summary.tool_accuracy < gates.min_tool_accuracy:
        failures.append(f"tool_accuracy {summary.tool_accuracy:.4f} < {gates.min_tool_accuracy:.4f}")
    if gates.min_stability_score is not None and summary.stability_score < gates.min_stability_score:
        failures.append(
            f"stability_score {summary.stability_score:.4f} < {gates.min_stability_score:.4f}"
        )
    return failures
