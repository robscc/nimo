"""CLI entrypoint for the AgentPal CaseBank sidecar (stdlib argparse)."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Optional


def _cmd_init(args: argparse.Namespace) -> int:
    from casebank.storage.fs_store import FileStore

    store = FileStore(Path(args.data_dir))
    store.bootstrap()
    print(f"Initialized CaseBank data dir: {Path(args.data_dir).resolve()}")
    return 0


def _cmd_collect_start(args: argparse.Namespace) -> int:
    from casebank.collectors.orchestrator import CollectorOrchestrator
    from casebank.config import load_config

    cfg = load_config(base_url=args.base_url, data_dir=args.data_dir)
    orchestrator = CollectorOrchestrator(cfg)
    print("Starting collectors...")
    asyncio.run(orchestrator.start_forever())
    return 0


def _cmd_collect_backfill(args: argparse.Namespace) -> int:
    from casebank.collectors.orchestrator import CollectorOrchestrator
    from casebank.config import load_config

    cfg = load_config(base_url=args.base_url, data_dir=args.data_dir)
    orchestrator = CollectorOrchestrator(cfg)
    asyncio.run(orchestrator.backfill_once())
    print("Backfill completed.")
    return 0


def _cmd_collect_doctor(args: argparse.Namespace) -> int:
    from casebank.collectors.doctor import run_doctor

    results = asyncio.run(run_doctor(base_url=args.base_url, timeout_seconds=args.timeout_seconds))

    has_failure = False
    for item in results:
        prefix = "OK" if item.ok else "FAIL"
        print(f"[{prefix}] {item.name}: {item.detail}")
        if not item.ok:
            has_failure = True

    return 2 if has_failure else 0


def _cmd_cases_build_candidates(args: argparse.Namespace) -> int:
    from casebank.cases.candidate_builder import CandidateBuilder

    builder = CandidateBuilder(Path(args.data_dir))
    rows = builder.build(date=args.date)
    print(f"Generated {len(rows)} candidate cases.")
    return 0


def _cmd_cases_list(args: argparse.Namespace) -> int:
    from casebank.cases.gold_manager import GoldManager

    manager = GoldManager(Path(args.data_dir))
    rows = manager.list_cases(args.state)
    for row in rows:
        print(f"{row.case_id}  state={row.state} labels={','.join(row.labels)}")
    print(f"Total: {len(rows)}")
    return 0


def _cmd_cases_promote(args: argparse.Namespace) -> int:
    from casebank.cases.gold_manager import GoldManager

    manager = GoldManager(Path(args.data_dir))
    tools = [part.strip() for part in args.expected_tools.split(",")] if args.expected_tools else None
    case = manager.promote(
        case_id=args.case_id,
        expected_outcome=args.expected_outcome,
        reviewer=args.reviewer,
        expected_tools=tools,
    )
    print(f"Promoted to gold: {case.case_id}")
    return 0


def _cmd_eval_run(args: argparse.Namespace) -> int:
    from casebank.cases.gold_manager import GoldManager
    from casebank.eval.gates import KpiGates, evaluate_gates, load_gates_file
    from casebank.eval.runner import EvalRunner
    from casebank.reports.markdown_report import build_markdown_report, write_report
    from casebank.suites.suite_loader import filter_cases, load_suite_file

    data_dir = Path(args.data_dir)
    runner = EvalRunner(data_dir)

    suite_name = args.suite
    selected_cases = None

    if args.suite_file:
        suite_spec = load_suite_file(Path(args.suite_file))
        suite_name = suite_spec.name or args.suite
        all_gold = GoldManager(data_dir).list_cases("gold")
        selected_cases = filter_cases(all_gold, suite_spec)
        print(f"Suite filter applied: {len(selected_cases)} / {len(all_gold)} gold cases selected.")

    run_meta, case_results, summary = runner.run(
        suite=suite_name,
        date=args.date,
        selected_cases=selected_cases,
    )

    report_content = build_markdown_report(run_meta, summary, case_results)
    run_dir = data_dir / "runs" / run_meta.run_id
    write_report(run_dir / "report.md", report_content)

    print(f"Run completed: {run_meta.run_id}")
    print(f"Task success rate: {summary.task_success_rate:.2%}")
    print(f"Tool accuracy: {summary.tool_accuracy:.2%}")
    print(f"Stability score: {summary.stability_score:.4f}")

    gates = KpiGates(
        min_task_success_rate=args.min_task_success_rate,
        min_tool_accuracy=args.min_tool_accuracy,
        min_stability_score=args.min_stability_score,
    )
    if args.gates_file:
        gates = load_gates_file(Path(args.gates_file))

    violations = evaluate_gates(summary, gates)
    if violations:
        print("\nGate check: FAILED")
        for violation in violations:
            print(f"- {violation}")
        return 2

    if any(
        value is not None
        for value in [
            gates.min_task_success_rate,
            gates.min_tool_accuracy,
            gates.min_stability_score,
        ]
    ):
        print("\nGate check: PASSED")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="casebank", description="AgentPal file-based CaseBank sidecar")
    parser.set_defaults(func=lambda _args: parser.print_help() or 0)

    root_sub = parser.add_subparsers(dest="command")

    # init
    cmd_init = root_sub.add_parser("init", help="Initialize directory structure")
    cmd_init.add_argument("--data-dir", default="./data")
    cmd_init.set_defaults(func=_cmd_init)

    # collect
    cmd_collect = root_sub.add_parser("collect", help="Collection commands")
    collect_sub = cmd_collect.add_subparsers(dest="collect_cmd")

    collect_start = collect_sub.add_parser("start", help="Run collectors forever")
    collect_start.add_argument("--base-url", default="http://localhost:8099/api/v1")
    collect_start.add_argument("--data-dir", default="./data")
    collect_start.set_defaults(func=_cmd_collect_start)

    collect_backfill = collect_sub.add_parser("backfill", help="Run one backfill cycle")
    collect_backfill.add_argument("--base-url", default="http://localhost:8099/api/v1")
    collect_backfill.add_argument("--data-dir", default="./data")
    collect_backfill.set_defaults(func=_cmd_collect_backfill)

    collect_doctor = collect_sub.add_parser("doctor", help="Check REST/SSE/WS endpoint connectivity")
    collect_doctor.add_argument("--base-url", default="http://localhost:8099/api/v1")
    collect_doctor.add_argument("--timeout-seconds", type=int, default=8)
    collect_doctor.set_defaults(func=_cmd_collect_doctor)

    # cases
    cmd_cases = root_sub.add_parser("cases", help="Case lifecycle commands")
    cases_sub = cmd_cases.add_subparsers(dest="cases_cmd")

    cases_build = cases_sub.add_parser("build-candidates", help="Build candidate cases from raw events")
    cases_build.add_argument("--data-dir", default="./data")
    cases_build.add_argument("--date", default=None)
    cases_build.set_defaults(func=_cmd_cases_build_candidates)

    cases_list = cases_sub.add_parser("list", help="List candidate or gold cases")
    cases_list.add_argument("--state", choices=["candidate", "gold"], default="candidate")
    cases_list.add_argument("--data-dir", default="./data")
    cases_list.set_defaults(func=_cmd_cases_list)

    cases_promote = cases_sub.add_parser("promote", help="Promote candidate case to gold")
    cases_promote.add_argument("case_id")
    cases_promote.add_argument("--expected-outcome", required=True)
    cases_promote.add_argument("--reviewer", default="human-review")
    cases_promote.add_argument("--expected-tools", default=None)
    cases_promote.add_argument("--data-dir", default="./data")
    cases_promote.set_defaults(func=_cmd_cases_promote)

    # eval
    cmd_eval = root_sub.add_parser("eval", help="Evaluation commands")
    eval_sub = cmd_eval.add_subparsers(dest="eval_cmd")

    eval_run = eval_sub.add_parser("run", help="Run evaluation on gold cases")
    eval_run.add_argument("--suite", default="regression")
    eval_run.add_argument("--suite-file", default=None, help="Path to suite JSON")
    eval_run.add_argument("--gates-file", default=None, help="Path to KPI gates JSON")
    eval_run.add_argument("--min-task-success-rate", type=float, default=None)
    eval_run.add_argument("--min-tool-accuracy", type=float, default=None)
    eval_run.add_argument("--min-stability-score", type=float, default=None)
    eval_run.add_argument("--data-dir", default="./data")
    eval_run.add_argument("--date", default=None)
    eval_run.set_defaults(func=_cmd_eval_run)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 1
    return int(func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
