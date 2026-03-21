"""nimo init — Initialize workspace, config, database, and default SubAgents."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from agentpal.cli.console import banner, error, info, success
from agentpal.cli.utils import run_async

app = typer.Typer()


@app.callback(invoke_without_command=True)
def init(
    workspace_dir: Optional[str] = typer.Option(
        None,
        "--workspace-dir",
        "-w",
        help="Override workspace path (default: ~/.nimo)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-create missing files even if already initialized",
    ),
) -> None:
    """Initialize AgentPal workspace, config, database, and default SubAgents."""
    banner("Initializing AgentPal...")

    from agentpal.paths import get_workspace_dir

    ws_path = Path(workspace_dir) if workspace_dir else get_workspace_dir()

    # 1. Bootstrap workspace
    try:
        from agentpal.workspace.manager import WorkspaceManager

        mgr = WorkspaceManager(ws_path)
        created = run_async(mgr.bootstrap())
        if created:
            success(f"Workspace: {ws_path}")
        elif force:
            # Force mode: re-write default files
            mgr._write_defaults_sync()
            success(f"Workspace: {ws_path} (files refreshed)")
        else:
            info(f"Workspace: {ws_path} (already exists)")
    except Exception as e:
        error(f"Workspace initialization failed: {e}")
        raise typer.Exit(1)

    # 2. Config file
    try:
        from agentpal.services.config_file import ConfigFileManager

        cfg_mgr = ConfigFileManager(ws_path)
        if force or not cfg_mgr.config_path.exists():
            cfg_mgr.save_defaults() if not cfg_mgr.config_path.exists() else None
            if force and cfg_mgr.config_path.exists():
                info(f"Config: {cfg_mgr.config_path} (kept existing)")
            else:
                success(f"Config: {cfg_mgr.config_path}")
        else:
            info(f"Config: {cfg_mgr.config_path} (already exists)")
    except Exception as e:
        error(f"Config initialization failed: {e}")
        raise typer.Exit(1)

    # 3. Database
    try:
        from agentpal.database import init_db, run_migrations

        run_async(init_db())
        run_async(run_migrations())
        success("Database: tables created")
    except Exception as e:
        error(f"Database initialization failed: {e}")
        raise typer.Exit(1)

    # 4. Default SubAgents
    try:
        from agentpal.agents.registry import SubAgentRegistry
        from agentpal.database import AsyncSessionLocal

        async def _ensure_defaults():
            async with AsyncSessionLocal() as db:
                registry = SubAgentRegistry(db)
                await registry.ensure_defaults()
                await db.commit()

        run_async(_ensure_defaults())
        success("SubAgents: researcher, coder")
    except Exception as e:
        error(f"SubAgent initialization failed: {e}")
        raise typer.Exit(1)

    typer.echo()
    success("Done! Run `nimo start` to launch.")
