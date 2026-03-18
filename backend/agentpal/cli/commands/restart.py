"""nimo restart — Stop and re-start AgentPal services."""

from __future__ import annotations

from typing import Optional

import typer

from agentpal.cli.console import banner

app = typer.Typer()


@app.callback(invoke_without_command=True)
def restart(
    host: Optional[str] = typer.Option(None, "--host", help="Bind address"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="Port number"),
    foreground: bool = typer.Option(False, "--foreground", "-f", help="Run in foreground"),
    with_frontend: bool = typer.Option(False, "--with-frontend", help="Also start frontend"),
    reload: Optional[bool] = typer.Option(None, "--reload/--no-reload", help="Hot reload"),
    log_file: Optional[str] = typer.Option(None, "--log-file", help="Log file path"),
    force: bool = typer.Option(False, "--force", help="Force kill on stop"),
) -> None:
    """Restart AgentPal services (stop + start)."""
    banner("Restarting AgentPal...")

    from agentpal.cli.commands.stop import stop as stop_cmd

    stop_cmd(force=force, backend_only=False)

    typer.echo()

    from agentpal.cli.commands.start import start as start_cmd

    start_cmd(
        host=host,
        port=port,
        foreground=foreground,
        with_frontend=with_frontend,
        reload=reload,
        log_file=log_file,
    )
