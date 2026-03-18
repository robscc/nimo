"""nimo stop — Stop running AgentPal services."""

from __future__ import annotations

import typer

from agentpal.cli.console import banner, info
from agentpal.cli.process import PidManager

app = typer.Typer()


@app.callback(invoke_without_command=True)
def stop(
    force: bool = typer.Option(
        False, "--force", help="Send SIGKILL immediately instead of SIGTERM"
    ),
    backend_only: bool = typer.Option(
        False, "--backend-only", help="Only stop the backend (keep frontend running)"
    ),
) -> None:
    """Stop running AgentPal services."""
    banner("Stopping AgentPal...")

    backend_pid = PidManager("backend")
    stopped_any = False

    if backend_pid.is_running() or backend_pid.read() is not None:
        result = backend_pid.stop(timeout=5, force=force)
        stopped_any = stopped_any or result
    else:
        info("Backend is not running")

    if not backend_only:
        frontend_pid = PidManager("frontend")
        if frontend_pid.is_running() or frontend_pid.read() is not None:
            result = frontend_pid.stop(timeout=5, force=force)
            stopped_any = stopped_any or result
        else:
            info("Frontend is not running")

    if not stopped_any:
        info("AgentPal is not running")
