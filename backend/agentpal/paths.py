"""Centralized path configuration for AgentPal.

All paths under ~/.nimo (or custom base dir) are managed here.
Environment variables can override the base directory:
    - NIMO_HOME: Base directory (default: ~/.nimo)

Individual paths can also be overridden:
    - WORKSPACE_DIR: Workspace directory
    - SKILLS_DIR: Skills data directory
    - PROVIDERS_DIR: Providers storage directory
    - RUN_DIR: PID files directory

Usage:
    from agentpal.paths import get_nimo_home, get_workspace_dir

    home = get_nimo_home()          # ~/.nimo or NIMO_HOME
    workspace = get_workspace_dir() # respects WORKSPACE_DIR env var
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def get_nimo_home() -> Path:
    """Get the base .nimo directory.

    Respects NIMO_HOME environment variable if set.
    Otherwise defaults to ~/.nimo.

    Returns:
        The base nimo directory path.
    """
    env_dir = os.environ.get("NIMO_HOME")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".nimo"


def get_workspace_dir() -> Path:
    """Get the workspace directory.

    Priority:
    1. WORKSPACE_DIR env var
    2. NIMO_HOME env var + "workspace" subdirectory
    3. ~/.nimo (legacy default)

    Returns:
        The workspace directory path.
    """
    env_dir = os.environ.get("WORKSPACE_DIR")
    if env_dir:
        return Path(env_dir)

    # Check if NIMO_HOME is set but WORKSPACE_DIR is not
    nimo_home = os.environ.get("NIMO_HOME")
    if nimo_home:
        return Path(nimo_home)

    return Path.home() / ".nimo"


def get_skills_dir() -> Path:
    """Get the skills data directory.

    Priority:
    1. SKILLS_DIR env var
    2. NIMO_HOME env var + "skills_data" subdirectory
    3. ~/.nimo/skills_data (legacy default)

    Returns:
        The skills data directory path.
    """
    env_dir = os.environ.get("SKILLS_DIR")
    if env_dir:
        return Path(env_dir)

    nimo_home = os.environ.get("NIMO_HOME")
    if nimo_home:
        return Path(nimo_home) / "skills_data"

    return Path.home() / ".nimo" / "skills_data"


def get_providers_dir() -> Path:
    """Get the providers storage directory.

    Priority:
    1. PROVIDERS_DIR env var
    2. NIMO_HOME env var + "providers" subdirectory
    3. ~/.nimo/providers (legacy default)

    Returns:
        The providers directory path.
    """
    env_dir = os.environ.get("PROVIDERS_DIR")
    if env_dir:
        return Path(env_dir)

    nimo_home = os.environ.get("NIMO_HOME")
    if nimo_home:
        return Path(nimo_home) / "providers"

    return Path.home() / ".nimo" / "providers"


def get_run_dir() -> Path:
    """Get the run directory for PID files.

    Priority:
    1. RUN_DIR env var
    2. NIMO_HOME env var + "run" subdirectory
    3. ~/.nimo/run (legacy default)

    Returns:
        The run directory path.
    """
    env_dir = os.environ.get("RUN_DIR")
    if env_dir:
        return Path(env_dir)

    nimo_home = os.environ.get("NIMO_HOME")
    if nimo_home:
        return Path(nimo_home) / "run"

    return Path.home() / ".nimo" / "run"


def get_plans_dir() -> Path:
    """Get the plans directory.

    Priority:
    1. PLANS_DIR env var
    2. NIMO_HOME env var + "plans" subdirectory
    3. ~/.nimo/plans (legacy default)

    Returns:
        The plans directory path.
    """
    env_dir = os.environ.get("PLANS_DIR")
    if env_dir:
        return Path(env_dir)

    nimo_home = os.environ.get("NIMO_HOME")
    if nimo_home:
        return Path(nimo_home) / "plans"

    return Path.home() / ".nimo" / "plans"


def get_config_file() -> Path:
    """Get the config.yaml file path.

    Returns:
        The config file path.
    """
    return get_nimo_home() / "config.yaml"


@lru_cache(maxsize=None)
def _cached_getter(func_name: str) -> Path:
    """Cached wrapper for getter functions."""
    getters = {
        "nimo_home": get_nimo_home,
        "workspace": get_workspace_dir,
        "skills": get_skills_dir,
        "providers": get_providers_dir,
        "run": get_run_dir,
        "plans": get_plans_dir,
    }
    getter = getters.get(func_name)
    if getter:
        return getter()
    raise ValueError(f"Unknown cached getter: {func_name}")


# Backward-compatible alias — some modules import get_nimo_dir
get_nimo_dir = get_nimo_home

# Convenience constants for backward compatibility
NIMO_HOME = get_nimo_home()
WORKSPACE_DEFAULT = get_workspace_dir()
SKILLS_DEFAULT = get_skills_dir()
PROVIDERS_DEFAULT = get_providers_dir()
RUN_DEFAULT = get_run_dir()
PLANS_DEFAULT = get_plans_dir()
