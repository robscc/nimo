"""nimo clean — Clean up generated files (database, cache, logs, workspace)."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer

from agentpal.cli.console import banner, error, info, success, warning
from agentpal.cli.process import PidManager
from agentpal.cli.utils import find_project_root, get_workspace_dir

app = typer.Typer()


@app.callback(invoke_without_command=True)
def clean(
    db: bool = typer.Option(False, "--db", help="Delete SQLite database file"),
    logs: bool = typer.Option(False, "--logs", help="Delete log files"),
    cache: bool = typer.Option(
        False, "--cache", help="Delete __pycache__, .pytest_cache, htmlcov, .coverage"
    ),
    workspace: bool = typer.Option(
        False, "--workspace", help="Delete entire ~/.nimo/ workspace (requires confirmation)"
    ),
    all_: bool = typer.Option(False, "--all", help="Clean everything"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts"),
) -> None:
    """Clean up generated files."""
    if all_:
        db = logs = cache = workspace = True

    if not any([db, logs, cache, workspace]):
        error("Specify at least one target: --db, --logs, --cache, --workspace, or --all")
        raise typer.Exit(1)

    # Safety: check if services are running
    backend_pid = PidManager("backend")
    frontend_pid = PidManager("frontend")
    if backend_pid.is_running() or frontend_pid.is_running():
        warning("AgentPal is still running! Stop it first with `nimo stop`")
        if not yes:
            proceed = typer.confirm("Continue anyway?", default=False)
            if not proceed:
                raise typer.Exit(0)

    banner("Cleaning up...")

    ws_dir = get_workspace_dir()

    # Workspace (most destructive — confirm separately)
    if workspace:
        if not yes:
            typer.confirm(
                f"Delete entire workspace at {ws_dir}? This cannot be undone!",
                abort=True,
            )
        if ws_dir.exists():
            shutil.rmtree(ws_dir)
            success(f"Removed workspace: {ws_dir}")
        else:
            info(f"Workspace not found: {ws_dir}")
        return  # workspace includes everything else

    # Database
    if db:
        _clean_db(ws_dir, yes)

    # Logs
    if logs:
        _clean_logs(ws_dir)

    # Cache
    if cache:
        _clean_cache()


def _clean_db(ws_dir: Path, yes: bool) -> None:
    """Delete SQLite database files."""
    try:
        from agentpal.config import get_settings

        settings = get_settings()
        db_url = settings.database_url
        if ":///" in db_url:
            db_path_str = db_url.split(":///")[-1]
            db_path = Path(db_path_str)
        else:
            db_path = None
    except Exception:
        db_path = Path("agentpal.db")

    if db_path and db_path.exists():
        if not yes:
            typer.confirm(f"Delete database {db_path}?", abort=True)
        db_path.unlink()
        success(f"Removed database: {db_path}")
        # Also remove WAL and SHM files
        for suffix in ("-wal", "-shm"):
            wal = db_path.with_name(db_path.name + suffix)
            if wal.exists():
                wal.unlink()
                info(f"Removed: {wal}")
    else:
        info("Database file not found")


def _clean_logs(ws_dir: Path) -> None:
    """Delete log files."""
    log_dir = ws_dir / "logs"
    if log_dir.exists():
        shutil.rmtree(log_dir)
        success(f"Removed logs: {log_dir}")
    else:
        info("Log directory not found")


def _clean_cache() -> None:
    """Delete Python cache directories."""
    project_root = find_project_root()
    if project_root is None:
        project_root = Path.cwd()

    targets = ["__pycache__", ".pytest_cache", "htmlcov", ".mypy_cache", ".ruff_cache"]
    files_to_remove = [".coverage"]

    removed = 0
    # Walk the project to find __pycache__ dirs
    for target in targets:
        for match in project_root.rglob(target):
            if match.is_dir():
                shutil.rmtree(match)
                removed += 1

    for fname in files_to_remove:
        fpath = project_root / fname
        if fpath.exists():
            fpath.unlink()
            removed += 1

    if removed:
        success(f"Removed {removed} cache directories/files")
    else:
        info("No cache files found")
