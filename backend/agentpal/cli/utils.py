"""Shared CLI utilities."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Bridge async code for synchronous CLI commands.

    Uses asyncio.run() which creates a new event loop each time.
    """
    return asyncio.run(coro)


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is currently in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((host, port))
            return True
        except (ConnectionRefusedError, OSError):
            return False


def find_project_root() -> Path | None:
    """Walk up from CWD to find the directory containing pyproject.toml.

    Returns None if not found (e.g., when running from an installed package).
    """
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return None


def get_workspace_dir() -> Path:
    """Get the workspace directory, respecting WORKSPACE_DIR env var."""
    from agentpal.paths import get_workspace_dir as _get_workspace_dir
    return _get_workspace_dir()


def mask_secret(value: str, reveal: bool = False) -> str:
    """Mask a secret string, showing only first/last 2 chars.

    If reveal is True, return the full value.
    """
    if reveal or not value:
        return value
    if len(value) <= 8:
        return "****"
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"
