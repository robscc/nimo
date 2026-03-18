"""Tests for cli/process.py — PID file management."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from unittest.mock import patch

import pytest

from agentpal.cli.process import PidManager


@pytest.fixture
def pid_mgr(tmp_path):
    """Create a PidManager with a temp run dir."""
    return PidManager("backend", run_dir=tmp_path)


class TestPidManager:
    def test_write_and_read(self, pid_mgr: PidManager):
        pid_mgr.write(12345)
        assert pid_mgr.read() == 12345

    def test_read_no_file(self, pid_mgr: PidManager):
        assert pid_mgr.read() is None

    def test_read_corrupt_file(self, pid_mgr: PidManager):
        pid_mgr.pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_mgr.pid_file.write_text("not-a-number")
        assert pid_mgr.read() is None

    def test_is_running_no_file(self, pid_mgr: PidManager):
        assert pid_mgr.is_running() is False

    def test_is_running_current_process(self, pid_mgr: PidManager):
        """Current process PID should be detected as running."""
        pid_mgr.write(os.getpid())
        assert pid_mgr.is_running() is True

    def test_is_running_dead_process(self, pid_mgr: PidManager):
        """A very large PID should not be running."""
        pid_mgr.write(99999999)
        assert pid_mgr.is_running() is False

    def test_clean(self, pid_mgr: PidManager):
        pid_mgr.write(12345)
        assert pid_mgr.pid_file.exists()
        pid_mgr.clean()
        assert not pid_mgr.pid_file.exists()

    def test_clean_no_file(self, pid_mgr: PidManager):
        # Should not raise
        pid_mgr.clean()

    def test_stop_no_pid_file(self, pid_mgr: PidManager):
        assert pid_mgr.stop() is False

    def test_stop_stale_pid(self, pid_mgr: PidManager):
        """Stale PID (process dead) should clean up PID file."""
        pid_mgr.write(99999999)
        result = pid_mgr.stop()
        assert result is False
        assert not pid_mgr.pid_file.exists()

    def test_stop_sends_signal(self, pid_mgr: PidManager):
        """Verify that stop sends SIGTERM to a live process."""
        pid_mgr.write(12345)

        killed_signals = []

        def fake_kill(pid, sig):
            killed_signals.append((pid, sig))
            if sig == 0:
                return  # Process "exists"
            raise ProcessLookupError  # Process "dies" after signal

        with patch("agentpal.cli.process.os.kill", side_effect=fake_kill):
            result = pid_mgr.stop(timeout=1)

        assert result is False  # ProcessLookupError means it "cleaned up"
        # Should have tried kill(pid, 0) first, then SIGTERM
        assert any(sig == 0 for _, sig in killed_signals)

    def test_stop_force(self, pid_mgr: PidManager):
        """Force stop should send SIGKILL."""
        pid_mgr.write(12345)

        killed_signals = []

        def fake_kill(pid, sig):
            killed_signals.append((pid, sig))
            if sig == 0:
                return
            raise ProcessLookupError

        with patch("agentpal.cli.process.os.kill", side_effect=fake_kill):
            pid_mgr.stop(force=True)

        sigkill_sent = any(sig == signal.SIGKILL for _, sig in killed_signals)
        assert sigkill_sent

    def test_pid_file_path(self, tmp_path):
        mgr = PidManager("frontend", run_dir=tmp_path)
        assert mgr.pid_file == tmp_path / "frontend.pid"
