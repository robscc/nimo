"""nimo logs — View backend logs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

from agentpal.cli.console import console, error, info
from agentpal.cli.utils import get_workspace_dir

app = typer.Typer()


@app.callback(invoke_without_command=True)
def logs(
    follow: bool = typer.Option(
        False, "--follow", "-f", help="Follow log output (like tail -f)"
    ),
    lines: int = typer.Option(
        50, "--lines", "-n", help="Number of lines to show"
    ),
    service: str = typer.Option(
        "backend", "--service", "-s", help="Service to show logs for (backend/frontend)"
    ),
) -> None:
    """View AgentPal service logs."""
    ws_dir = get_workspace_dir()
    log_path = ws_dir / "logs" / f"{service}.log"

    if not log_path.exists():
        error(f"Log file not found: {log_path}")
        info("Is AgentPal running? Start with `nimo start`")
        raise typer.Exit(1)

    if follow:
        info(f"Following {log_path} (Ctrl+C to stop)")
        try:
            subprocess.run(
                ["tail", "-f", "-n", str(lines), str(log_path)],
            )
        except KeyboardInterrupt:
            pass
        except FileNotFoundError:
            # tail not available (Windows), fall back to Python
            _tail_follow_python(log_path, lines)
    else:
        _tail_python(log_path, lines)


def _tail_python(path: Path, lines: int) -> None:
    """Show last N lines of a file using Python (cross-platform)."""
    try:
        all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in all_lines[-lines:]:
            console.print(line, highlight=False)
    except Exception as e:
        error(f"Failed to read log file: {e}")


def _tail_follow_python(path: Path, lines: int) -> None:
    """Follow a log file using Python (fallback for systems without tail)."""
    import time

    _tail_python(path, lines)

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            # Seek to end
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    console.print(line.rstrip(), highlight=False)
                else:
                    time.sleep(0.1)
    except KeyboardInterrupt:
        pass
