"""nimo start — Start the AgentPal backend (and optionally frontend)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from agentpal.cli.console import banner, error, info, success, warning
from agentpal.cli.process import PidManager
from agentpal.cli.utils import find_project_root, get_workspace_dir, port_in_use

app = typer.Typer()


def _health_check(host: str, port: int, retries: int = 10, interval: float = 0.5) -> bool:
    """Poll /health endpoint until success or retries exhausted."""
    import urllib.request
    import urllib.error

    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/health"
    for i in range(retries):
        try:
            resp = urllib.request.urlopen(url, timeout=2)
            if resp.status == 200:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


@app.callback(invoke_without_command=True)
def start(
    host: Optional[str] = typer.Option(
        None, "--host", help="Bind address (default: from config)"
    ),
    port: Optional[int] = typer.Option(
        None, "--port", "-p", help="Port number (default: from config)"
    ),
    foreground: bool = typer.Option(
        False, "--foreground", "-f", help="Run in foreground (don't daemonize)"
    ),
    with_frontend: bool = typer.Option(
        False, "--with-frontend", help="Also start the frontend dev server"
    ),
    reload: Optional[bool] = typer.Option(
        None, "--reload/--no-reload", help="Enable/disable hot reload (default: auto)"
    ),
    log_file: Optional[str] = typer.Option(
        None, "--log-file", help="Log file path"
    ),
) -> None:
    """Start the AgentPal backend server."""
    banner("Starting AgentPal...")

    ws_dir = get_workspace_dir()

    # Auto-init if workspace doesn't exist
    if not ws_dir.exists():
        info("Workspace not found, running init...")
        from agentpal.cli.commands.init_cmd import init as init_cmd

        init_cmd(workspace_dir=None, force=False)

    # Resolve host/port from config
    try:
        from agentpal.config import get_settings

        settings = get_settings()
        actual_host = host or settings.app_host
        actual_port = port or settings.app_port
    except Exception:
        actual_host = host or "0.0.0.0"
        actual_port = port or 8099

    # Check if already running
    backend_pid = PidManager("backend")
    if backend_pid.is_running():
        pid = backend_pid.read()
        warning(f"Backend is already running (PID {pid})")
        raise typer.Exit(0)

    # Check port availability
    if port_in_use(actual_port, "127.0.0.1"):
        error(f"Port {actual_port} is already in use")
        raise typer.Exit(1)

    # Determine reload setting
    if reload is None:
        try:
            reload = settings.is_dev
        except Exception:
            reload = True

    # Build uvicorn command
    cmd = [
        sys.executable, "-m", "uvicorn",
        "agentpal.main:app",
        "--host", actual_host,
        "--port", str(actual_port),
    ]
    if reload:
        cmd.append("--reload")

    # Log file
    log_path = Path(log_file) if log_file else ws_dir / "logs" / "backend.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if foreground:
        info(f"Running in foreground on {actual_host}:{actual_port}")
        info("Press Ctrl+C to stop")
        try:
            proc = subprocess.run(cmd)
            raise typer.Exit(proc.returncode)
        except KeyboardInterrupt:
            info("Stopped by user")
            raise typer.Exit(0)
    else:
        # Background mode
        log_fh = open(log_path, "a")
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        backend_pid.write(proc.pid)

        # Health check
        info(f"Waiting for backend to start (PID {proc.pid})...")
        if _health_check(actual_host, actual_port):
            success(f"Backend started on http://{actual_host}:{actual_port} (PID {proc.pid})")
            info(f"Logs: {log_path}")
        else:
            # Check if process is still alive
            if proc.poll() is not None:
                error(f"Backend process exited with code {proc.returncode}")
                error(f"Check logs: {log_path}")
                backend_pid.clean()
                raise typer.Exit(1)
            else:
                warning(
                    f"Backend started (PID {proc.pid}) but health check "
                    f"did not respond. Check logs: {log_path}"
                )

    # Optionally start frontend
    if with_frontend:
        _start_frontend(ws_dir)


def _start_frontend(ws_dir: Path) -> None:
    """Start the frontend dev server in background."""
    project_root = find_project_root()
    if project_root is None:
        warning("Cannot find project root; skipping frontend start")
        return

    frontend_dir = project_root / "frontend"
    if not frontend_dir.exists():
        warning(f"Frontend directory not found: {frontend_dir}")
        return

    if not (frontend_dir / "node_modules").exists():
        warning("Frontend node_modules not found. Run 'npm install' in frontend/ first")
        return

    frontend_pid = PidManager("frontend")
    if frontend_pid.is_running():
        pid = frontend_pid.read()
        warning(f"Frontend is already running (PID {pid})")
        return

    log_path = ws_dir / "logs" / "frontend.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_fh = open(log_path, "a")
    proc = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=str(frontend_dir),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    frontend_pid.write(proc.pid)
    success(f"Frontend started (PID {proc.pid})")
    info(f"Frontend logs: {log_path}")
