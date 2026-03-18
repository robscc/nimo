"""nimo config — Configuration management subcommands."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

import typer
from rich.syntax import Syntax

from agentpal.cli.console import console, error, info, success
from agentpal.cli.utils import get_workspace_dir, mask_secret

app = typer.Typer(help="Manage AgentPal configuration.")

# Keys whose values should be masked by default
_SENSITIVE_KEYS = {"api_key", "app_secret", "secret_key", "secret", "encrypt_key"}


def _mask_config(data: dict, reveal: bool = False) -> dict:
    """Recursively mask sensitive values in config dict."""
    result = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = _mask_config(value, reveal)
        elif isinstance(value, str) and any(s in key for s in _SENSITIVE_KEYS):
            result[key] = mask_secret(value, reveal)
        else:
            result[key] = value
    return result


@app.command()
def show(
    reveal: bool = typer.Option(
        False, "--reveal", help="Show sensitive values (API keys, secrets) unmasked"
    ),
) -> None:
    """Print the current configuration (sensitive values masked by default)."""
    import yaml

    ws_dir = get_workspace_dir()
    try:
        from agentpal.services.config_file import ConfigFileManager

        cfg_mgr = ConfigFileManager(ws_dir)
        config = cfg_mgr.load()
        display = _mask_config(config, reveal)

        yaml_str = yaml.dump(display, default_flow_style=False, allow_unicode=True, sort_keys=False)
        syntax = Syntax(yaml_str, "yaml", theme="monokai", line_numbers=False)
        console.print(syntax)
    except Exception as e:
        error(f"Failed to load config: {e}")
        raise typer.Exit(1)


@app.command()
def get(
    key: str = typer.Argument(help="Dotted path to config value (e.g. 'llm.model')"),
) -> None:
    """Get a specific configuration value by dotted path."""
    ws_dir = get_workspace_dir()
    try:
        from agentpal.services.config_file import ConfigFileManager

        cfg_mgr = ConfigFileManager(ws_dir)
        value = cfg_mgr.get(key)
        if value is None:
            error(f"Key '{key}' not found")
            raise typer.Exit(1)
        console.print(value)
    except SystemExit:
        raise
    except Exception as e:
        error(f"Failed to read config: {e}")
        raise typer.Exit(1)


@app.command("set")
def set_value(
    key: str = typer.Argument(help="Dotted path to config value (e.g. 'llm.model')"),
    value: str = typer.Argument(help="Value to set"),
) -> None:
    """Set a configuration value by dotted path."""
    ws_dir = get_workspace_dir()
    try:
        from agentpal.services.config_file import ConfigFileManager

        cfg_mgr = ConfigFileManager(ws_dir)

        # Auto type inference
        parsed_value = _infer_type(value)

        cfg_mgr.set(key, parsed_value)
        success(f"{key} = {parsed_value}")
    except Exception as e:
        error(f"Failed to set config: {e}")
        raise typer.Exit(1)


@app.command()
def edit() -> None:
    """Open config.yaml in $EDITOR."""
    ws_dir = get_workspace_dir()
    config_path = ws_dir / "config.yaml"

    if not config_path.exists():
        error(f"Config file not found: {config_path}")
        info("Run `nimo init` first")
        raise typer.Exit(1)

    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", ""))
    if not editor:
        # Try common editors
        for candidate in ("vim", "vi", "nano", "code"):
            if _command_exists(candidate):
                editor = candidate
                break

    if not editor:
        error("No editor found. Set $EDITOR environment variable")
        raise typer.Exit(1)

    info(f"Opening {config_path} with {editor}")
    try:
        subprocess.run([editor, str(config_path)], check=True)
    except subprocess.CalledProcessError as e:
        error(f"Editor exited with code {e.returncode}")
        raise typer.Exit(1)
    except FileNotFoundError:
        error(f"Editor '{editor}' not found")
        raise typer.Exit(1)


def _infer_type(value: str):
    """Infer Python type from string value."""
    # Boolean
    if value.lower() in ("true", "yes", "on"):
        return True
    if value.lower() in ("false", "no", "off"):
        return False
    # Integer
    try:
        return int(value)
    except ValueError:
        pass
    # Float
    try:
        return float(value)
    except ValueError:
        pass
    # List (comma-separated)
    if "," in value:
        return [item.strip() for item in value.split(",")]
    # String
    return value


def _command_exists(cmd: str) -> bool:
    """Check if a command exists in PATH."""
    import shutil

    return shutil.which(cmd) is not None
