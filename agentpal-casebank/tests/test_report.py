from casebank.models import MetricsSummary, RunMeta
from casebank.reports.markdown_report import build_markdown_report


def test_markdown_report_renders_core_sections() -> None:
    meta = RunMeta(run_id="run_1", suite="regression", started_at="2026-04-05T10:00:00+00:00", ended_at="2026-04-05T10:10:00+00:00")
    summary = MetricsSummary(
        run_id="run_1",
        task_success_rate=0.8,
        execution_accuracy=0.9,
        tool_path_match_rate=0.7,
        tool_accuracy=0.84,
        incident_rate_per_100_tasks=20.0,
        recovery_rate=0.5,
        mttr_seconds=120.0,
        stability_score=0.66,
        sample_size_tasks=10,
        sample_size_tool_calls=20,
    )

    report = build_markdown_report(meta, summary, case_results=[])
    assert "KPI Summary" in report
    assert "Task success rate" in report
    assert "Stability score" in report
