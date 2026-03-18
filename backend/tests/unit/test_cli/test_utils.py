"""Tests for cli/utils.py — shared utilities."""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from agentpal.cli.utils import (
    find_project_root,
    get_workspace_dir,
    mask_secret,
    port_in_use,
    run_async,
)


# ── run_async ─────────────────────────────────────────────

class TestRunAsync:
    def test_simple_coroutine(self):
        async def add(a, b):
            return a + b

        assert run_async(add(1, 2)) == 3

    def test_returns_none(self):
        async def noop():
            pass

        assert run_async(noop()) is None


# ── port_in_use ───────────────────────────────────────────

class TestPortInUse:
    def test_unused_port(self):
        # Use a random high port that is almost certainly free
        assert port_in_use(59999, "127.0.0.1") is False

    def test_used_port(self):
        # Bind a port and verify detection
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            assert port_in_use(port, "127.0.0.1") is True


# ── find_project_root ────────────────────────────────────

class TestFindProjectRoot:
    def test_finds_root(self, tmp_path):
        # Create a fake project with pyproject.toml
        (tmp_path / "pyproject.toml").write_text("[project]")
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        with patch("agentpal.cli.utils.Path.cwd", return_value=sub):
            root = find_project_root()
            assert root == tmp_path

    def test_returns_none_when_not_found(self, tmp_path):
        with patch("agentpal.cli.utils.Path.cwd", return_value=tmp_path):
            assert find_project_root() is None


# ── get_workspace_dir ────────────────────────────────────

class TestGetWorkspaceDir:
    def test_default(self):
        with patch.dict("os.environ", {}, clear=True):
            d = get_workspace_dir()
            assert d == Path.home() / ".nimo"

    def test_env_override(self, tmp_path):
        with patch.dict("os.environ", {"WORKSPACE_DIR": str(tmp_path)}):
            assert get_workspace_dir() == tmp_path


# ── mask_secret ──────────────────────────────────────────

class TestMaskSecret:
    def test_empty_string(self):
        assert mask_secret("") == ""

    def test_short_string(self):
        assert mask_secret("abcd") == "****"

    def test_long_string(self):
        result = mask_secret("sk-abcdefghij")
        assert result.startswith("sk")
        assert result.endswith("ij")
        assert "****" in result or "*" in result

    def test_reveal(self):
        assert mask_secret("sk-secret", reveal=True) == "sk-secret"
