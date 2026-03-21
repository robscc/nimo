"""PID file management for service lifecycle."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from agentpal.cli.console import error, info, success, warning


class PidManager:
    """Manage PID files for a service (backend / frontend)."""

    def __init__(self, service: str, run_dir: Path | None = None) -> None:
        from agentpal.paths import get_run_dir

        self.service = service
        if run_dir is None:
            run_dir = get_run_dir()
        self.pid_file = run_dir / f"{service}.pid"

    def write(self, pid: int) -> None:
        """Write a PID to the PID file."""
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(pid), encoding="utf-8")

    def read(self) -> int | None:
        """Read the PID from the PID file. Returns None if not found."""
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return None

    def is_running(self) -> bool:
        """Check if the process is alive via kill(pid, 0)."""
        pid = self.read()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def stop(self, timeout: int = 5, force: bool = False) -> bool:
        """Stop the process.

        1. Send SIGTERM (or SIGKILL if force=True)
        2. Wait up to `timeout` seconds for the process to exit
        3. If still alive after timeout, send SIGKILL
        4. Clean up PID file

        Returns:
            True if the process was stopped, False if it wasn't running.
        """
        pid = self.read()
        if pid is None:
            info(f"{self.service} is not running (no PID file)")
            return False

        if not self._pid_alive(pid):
            warning(f"{self.service} PID {pid} is stale (process already dead)")
            self.clean()
            return False

        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            self.clean()
            return False
        except PermissionError:
            error(f"Permission denied to stop {self.service} (PID {pid})")
            return False

        # Wait for graceful shutdown
        if not force:
            for _ in range(timeout * 10):  # check every 100ms
                if not self._pid_alive(pid):
                    break
                time.sleep(0.1)
            else:
                # Timeout — force kill
                warning(f"{self.service} did not stop gracefully, sending SIGKILL")
                try:
                    os.kill(pid, signal.SIGKILL)
                    time.sleep(0.5)
                except ProcessLookupError:
                    pass

        self.clean()
        success(f"{self.service} stopped (PID {pid})")
        return True

    def clean(self) -> None:
        """Remove the PID file."""
        if self.pid_file.exists():
            self.pid_file.unlink(missing_ok=True)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Check if a PID is alive."""
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
