"""Markdown report generation."""

from __future__ import annotations

from pathlib import Path

from casebank.models import CaseResult, MetricsSummary, RunMeta


def build_markdown_report(meta: RunMeta, summary: MetricsSummary, case_results: list[CaseResult]) -> str:
    """Render a compact markdown report for one run."""

    total_cases = len(case_results)
    failed_cases = [r for r in case_results if r.task_success is False]
    recovered_cases = [r for r in case_results if r.recovered]

    lines = [
        f"# CaseBank Eval Report - {meta.run_id}",
        "",
        f"- Suite: `{meta.suite}`",
        f"- Started: `{meta.started_at}`",
        f"- Ended: `{meta.ended_at}`",
        "",
        "## KPI Summary",
        "",
        f"- Task success rate: **{summary.task_success_rate:.2%}**",
        f"- Tool execution accuracy: **{summary.execution_accuracy:.2%}**",
        f"- Tool path match rate: **{(summary.tool_path_match_rate or 0.0):.2%}**",
        f"- Tool accuracy (combined): **{summary.tool_accuracy:.2%}**",
        f"- Incident rate per 100 tasks: **{summary.incident_rate_per_100_tasks:.2f}**",
        f"- Recovery rate: **{summary.recovery_rate:.2%}**",
        f"- MTTR (seconds): **{summary.mttr_seconds if summary.mttr_seconds is not None else 'n/a'}**",
        f"- Stability score: **{summary.stability_score:.4f}**",
        "",
        "## Case Stats",
        "",
        f"- Total gold cases evaluated: **{total_cases}**",
        f"- Failed cases: **{len(failed_cases)}**",
        f"- Recovered cases: **{len(recovered_cases)}**",
    ]

    if failed_cases:
        lines.extend([
            "",
            "## Failed Case IDs",
            "",
        ])
        lines.extend([f"- `{row.case_id}`" for row in failed_cases[:30]])

    lines.append("")
    return "\n".join(lines)


def write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
