"""nimo doctor — Environment health check."""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

import typer
from rich.table import Table

from agentpal.cli.console import console, banner, error, info, success, warning
from agentpal.cli.utils import find_project_root, get_workspace_dir, port_in_use

app = typer.Typer()

# Checks return (passed: bool, detail: str)
CheckResult = tuple[bool, str]


def _check_python_version() -> CheckResult:
    """Python >= 3.10."""
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 10):
        return True, f"Python {version_str}"
    return False, f"Python {version_str} (need >= 3.10)"


def _check_dependencies() -> CheckResult:
    """Key packages importable."""
    required = ["agentscope", "fastapi", "uvicorn", "sqlalchemy", "pydantic", "typer"]
    missing = []
    for pkg in required:
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        return False, f"Missing: {', '.join(missing)}"
    return True, "All key dependencies installed"


def _check_config_file() -> CheckResult:
    """~/.nimo/config.yaml exists and is valid YAML."""
    ws_dir = get_workspace_dir()
    config_path = ws_dir / "config.yaml"
    if not config_path.exists():
        return False, f"Not found: {config_path}"
    try:
        import yaml

        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False, "config.yaml is not a valid YAML dict"
        return True, str(config_path)
    except Exception as e:
        return False, f"Invalid YAML: {e}"


def _check_workspace() -> CheckResult:
    """Workspace directory and core files exist."""
    ws_dir = get_workspace_dir()
    if not ws_dir.exists():
        return False, f"Workspace not found: {ws_dir}"

    expected_files = [
        "SOUL.md", "IDENTITY.md", "AGENTS.md", "USER.md",
        "MEMORY.md", "CONTEXT.md", "HEARTBEAT.md", "BOOTSTRAP.md",
    ]
    found = sum(1 for f in expected_files if (ws_dir / f).exists())
    total = len(expected_files)
    if found == total:
        return True, f"{found}/{total} workspace files"
    return False, f"{found}/{total} workspace files (missing some)"


def _check_database() -> CheckResult:
    """SQLite database is accessible."""
    try:
        from agentpal.config import get_settings

        settings = get_settings()
        db_url = settings.database_url
        if ":///" in db_url:
            db_path_str = db_url.split(":///")[-1]
            db_path = Path(db_path_str)
            if db_path.exists():
                size_kb = db_path.stat().st_size / 1024
                return True, f"{db_path} ({size_kb:.0f} KB)"
            return False, f"Database file not found: {db_path}"
        return True, db_url
    except Exception as e:
        return False, f"Cannot check database: {e}"


def _check_llm_api_key() -> CheckResult:
    """LLM API key is configured."""
    ws_dir = get_workspace_dir()
    try:
        from agentpal.services.config_file import ConfigFileManager

        cfg_mgr = ConfigFileManager(ws_dir)
        api_key = cfg_mgr.get("llm.api_key", "")
        if api_key:
            return True, f"Configured ({api_key[:4]}...)"
        return False, "Not configured (llm.api_key is empty)"
    except Exception:
        # Fall back to env var
        import os

        key = os.environ.get("LLM_API_KEY", "")
        if key:
            return True, "Set via LLM_API_KEY env var"
        return False, "Not configured"


def _check_port() -> CheckResult:
    """Default port is available."""
    try:
        from agentpal.config import get_settings

        port = get_settings().app_port
    except Exception:
        port = 8099

    if port_in_use(port):
        return False, f"Port {port} is in use"
    return True, f"Port {port} is available"


def _check_frontend() -> CheckResult:
    """Frontend node_modules exists."""
    project_root = find_project_root()
    if project_root is None:
        return True, "Not in project directory (skipped)"

    frontend_dir = project_root / "frontend"
    if not frontend_dir.exists():
        return False, "frontend/ directory not found"

    if (frontend_dir / "node_modules").exists():
        return True, "node_modules found"
    return False, "node_modules not found (run npm install)"


@app.callback(invoke_without_command=True)
def doctor() -> None:
    """Run environment health checks for AgentPal."""
    banner("AgentPal Doctor")
    console.print()

    checks = [
        ("Python Version", _check_python_version),
        ("Dependencies", _check_dependencies),
        ("Config File", _check_config_file),
        ("Workspace", _check_workspace),
        ("Database", _check_database),
        ("LLM API Key", _check_llm_api_key),
        ("Port Availability", _check_port),
        ("Frontend", _check_frontend),
    ]

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Check", style="bold", width=20)
    table.add_column("Status", width=8)
    table.add_column("Details")

    passed = 0
    total = len(checks)

    for name, check_fn in checks:
        try:
            ok, detail = check_fn()
        except Exception as e:
            ok, detail = False, f"Error: {e}"

        status_str = "[green]PASS[/green]" if ok else "[red]FAIL[/red]"
        table.add_row(name, status_str, detail)
        if ok:
            passed += 1

    console.print(table)
    console.print()

    if passed == total:
        success(f"All {total} checks passed!")
    else:
        warning(f"{passed}/{total} checks passed, {total - passed} failed")
