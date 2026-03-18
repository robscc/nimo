"""nimo status — Show AgentPal service status and configuration summary."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.panel import Panel
from rich.table import Table

from agentpal.cli.console import console, error, info
from agentpal.cli.process import PidManager
from agentpal.cli.utils import get_workspace_dir, mask_secret, port_in_use

app = typer.Typer()


def _check_health(host: str, port: int) -> dict | None:
    """Quick health check against /health endpoint."""
    import urllib.request
    import urllib.error
    import json

    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/health"
    try:
        resp = urllib.request.urlopen(url, timeout=2)
        return json.loads(resp.read().decode())
    except Exception:
        return None


@app.callback(invoke_without_command=True)
def status() -> None:
    """Show AgentPal service status, configuration, and database info."""
    ws_dir = get_workspace_dir()

    # ── Service Status ──
    table = Table(title="Service Status", show_header=True, header_style="bold cyan")
    table.add_column("Service", style="bold")
    table.add_column("Status")
    table.add_column("PID")
    table.add_column("Details")

    backend_pid = PidManager("backend")
    frontend_pid = PidManager("frontend")

    # Backend
    if backend_pid.is_running():
        pid = backend_pid.read()
        # Try to get port from config
        try:
            from agentpal.config import get_settings
            settings = get_settings()
            port = settings.app_port
            host = settings.app_host
        except Exception:
            port = 8099
            host = "0.0.0.0"

        health = _check_health(host, port)
        if health:
            table.add_row(
                "Backend",
                "[green]Running[/green]",
                str(pid),
                f"http://{host}:{port} (healthy, v{health.get('version', '?')})",
            )
        else:
            table.add_row(
                "Backend",
                "[yellow]Running (unhealthy)[/yellow]",
                str(pid),
                f"http://{host}:{port} (health check failed)",
            )
    else:
        stale_pid = backend_pid.read()
        if stale_pid:
            table.add_row("Backend", "[red]Dead[/red]", str(stale_pid), "Stale PID file")
            backend_pid.clean()
        else:
            table.add_row("Backend", "[dim]Stopped[/dim]", "-", "")

    # Frontend
    if frontend_pid.is_running():
        pid = frontend_pid.read()
        table.add_row("Frontend", "[green]Running[/green]", str(pid), "http://localhost:3000")
    else:
        stale_pid = frontend_pid.read()
        if stale_pid:
            table.add_row("Frontend", "[red]Dead[/red]", str(stale_pid), "Stale PID file")
            frontend_pid.clean()
        else:
            table.add_row("Frontend", "[dim]Stopped[/dim]", "-", "")

    console.print()
    console.print(table)

    # ── Configuration Summary ──
    try:
        from agentpal.services.config_file import ConfigFileManager

        cfg_mgr = ConfigFileManager(ws_dir)
        config = cfg_mgr.load()

        cfg_table = Table(title="Configuration", show_header=True, header_style="bold cyan")
        cfg_table.add_column("Key", style="bold")
        cfg_table.add_column("Value")

        llm = config.get("llm", {})
        cfg_table.add_row("LLM Provider", str(llm.get("provider", "-")))
        cfg_table.add_row("LLM Model", str(llm.get("model", "-")))
        cfg_table.add_row("LLM API Key", mask_secret(str(llm.get("api_key", ""))))
        cfg_table.add_row("LLM Base URL", str(llm.get("base_url", "-")) or "-")

        app_cfg = config.get("app", {})
        cfg_table.add_row("Host", str(app_cfg.get("host", "-")))
        cfg_table.add_row("Port", str(app_cfg.get("port", "-")))
        cfg_table.add_row("Environment", str(app_cfg.get("env", "-")))

        console.print()
        console.print(cfg_table)
    except Exception:
        info("Config: unable to load")

    # ── Database Info ──
    try:
        from agentpal.config import get_settings

        settings = get_settings()
        db_url = settings.database_url

        # Extract file path from SQLite URL
        if ":///" in db_url:
            db_path_str = db_url.split(":///")[-1]
            db_path = Path(db_path_str)
            if db_path.exists():
                size_mb = db_path.stat().st_size / (1024 * 1024)
                info(f"Database: {db_path} ({size_mb:.2f} MB)")
            else:
                info(f"Database: {db_path} (not created yet)")
        else:
            info(f"Database: {db_url}")
    except Exception:
        pass

    # ── Workspace ──
    if ws_dir.exists():
        files = list(ws_dir.glob("*.md"))
        info(f"Workspace: {ws_dir} ({len(files)} files)")
    else:
        info(f"Workspace: {ws_dir} (not initialized)")

    # ── Channels ──
    try:
        from agentpal.services.config_file import ConfigFileManager

        cfg_mgr = ConfigFileManager(ws_dir)
        config = cfg_mgr.load()
        channels = config.get("channels", {})

        ch_parts = []
        for name in ("dingtalk", "feishu", "imessage"):
            ch = channels.get(name, {})
            enabled = ch.get("enabled", False)
            ch_parts.append(f"{name}: {'[green]on[/green]' if enabled else '[dim]off[/dim]'}")

        console.print()
        info(f"Channels: {', '.join(ch_parts)}")
    except Exception:
        pass
