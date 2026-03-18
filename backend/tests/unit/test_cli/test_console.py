"""Tests for cli/console.py — Rich console output helpers."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from rich.console import Console

from agentpal.cli import console as console_mod


class TestConsoleHelpers:
    """Verify that console helpers produce output without errors."""

    def _capture(self, fn, msg: str) -> str:
        """Capture rich console output as plain text."""
        buf = StringIO()
        test_console = Console(file=buf, force_terminal=True, width=120)
        with patch.object(console_mod, "console", test_console):
            fn(msg)
        return buf.getvalue()

    def test_banner(self):
        output = self._capture(console_mod.banner, "Test Title")
        assert "Test Title" in output

    def test_success(self):
        output = self._capture(console_mod.success, "all good")
        assert "all good" in output

    def test_error(self):
        output = self._capture(console_mod.error, "something broke")
        assert "something broke" in output

    def test_warning(self):
        output = self._capture(console_mod.warning, "careful")
        assert "careful" in output

    def test_info(self):
        output = self._capture(console_mod.info, "fyi")
        assert "fyi" in output

    def test_step(self):
        output = self._capture(console_mod.step, "working on it")
        assert "working on it" in output
