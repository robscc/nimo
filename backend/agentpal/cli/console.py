"""Rich console singleton and output helpers."""

from __future__ import annotations

from rich.console import Console

console = Console()


def banner(title: str) -> None:
    """Print a banner title with fish emoji."""
    console.print(f"\n[bold cyan]:tropical_fish: {title}[/bold cyan]")


def success(msg: str) -> None:
    """Print a success message."""
    console.print(f"  [green]:white_check_mark: {msg}[/green]")


def error(msg: str) -> None:
    """Print an error message."""
    console.print(f"  [red]:cross_mark: {msg}[/red]")


def warning(msg: str) -> None:
    """Print a warning message."""
    console.print(f"  [yellow]:warning: {msg}[/yellow]")


def info(msg: str) -> None:
    """Print an info message."""
    console.print(f"  [blue]:information: {msg}[/blue]")


def step(msg: str) -> None:
    """Print a step message (in-progress)."""
    console.print(f"  [dim]:hourglass_not_done: {msg}[/dim]")
