from casebank.eval.gates import KpiGates, evaluate_gates
from casebank.models import MetricsSummary


def test_evaluate_gates_reports_violations() -> None:
    summary = MetricsSummary(
        run_id="run1",
        task_success_rate=0.6,
        execution_accuracy=0.7,
        tool_path_match_rate=0.5,
        tool_accuracy=0.64,
        incident_rate_per_100_tasks=10.0,
        recovery_rate=0.5,
        mttr_seconds=100.0,
        stability_score=0.45,
        sample_size_tasks=10,
        sample_size_tool_calls=20,
    )

    gates = KpiGates(
        min_task_success_rate=0.7,
        min_tool_accuracy=0.65,
        min_stability_score=0.5,
    )
    failures = evaluate_gates(summary, gates)

    assert len(failures) == 3
    assert "task_success_rate" in failures[0]
