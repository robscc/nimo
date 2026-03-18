"""AgentPal CLI — main Typer application.

Entry point: `nimo` console script.
"""

from __future__ import annotations

import typer

from agentpal import __version__
from agentpal.cli.commands import (
    clean,
    config_cmd,
    doctor,
    init_cmd,
    logs,
    restart,
    start,
    status,
    stop,
)

app = typer.Typer(
    name="nimo",
    help="AgentPal CLI — manage your personal AI assistant.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"nimo (AgentPal) {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """AgentPal CLI — manage your personal AI assistant."""


# ── Register sub-commands ────────────────────────────────
app.add_typer(init_cmd.app, name="init", help="Initialize workspace, config, database")
app.add_typer(start.app, name="start", help="Start AgentPal services")
app.add_typer(stop.app, name="stop", help="Stop running services")
app.add_typer(restart.app, name="restart", help="Restart services (stop + start)")
app.add_typer(status.app, name="status", help="Show service status and config")
app.add_typer(config_cmd.app, name="config", help="Manage configuration")
app.add_typer(logs.app, name="logs", help="View service logs")
app.add_typer(clean.app, name="clean", help="Clean generated files")
app.add_typer(doctor.app, name="doctor", help="Run environment health checks")


# ── Also expose `nimo version` as a top-level command ────
@app.command()
def version() -> None:
    """Show version information."""
    from rich.panel import Panel

    from agentpal.cli.console import console

    console.print(
        Panel(
            f"[bold cyan]nimo[/bold cyan] (AgentPal) [green]{__version__}[/green]",
            title="Version",
            expand=False,
        )
    )


if __name__ == "__main__":
    app()
