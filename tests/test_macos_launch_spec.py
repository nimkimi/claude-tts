# tests/test_macos_launch_spec.py
import subprocess
from unittest import mock

import pytest

from sonari.platform.macos import supervisor as sup_mod

pytestmark = pytest.mark.skipif(not hasattr(sup_mod, "MacSupervisorBackend"),
                                reason="macOS supervisor only")


def test_relaunch_stderr_is_captured_not_discarded(tmp_path):
    log = tmp_path / "daemon.err.log"
    with mock.patch("sonari.paths.DAEMON_ERR_PATH", log):
        _, kwargs = sup_mod.MacSupervisorBackend().launch_spec()
    assert kwargs["stderr"] is not subprocess.DEVNULL
    try:
        kwargs["stderr"].write("boom\n")
    finally:
        kwargs["stderr"].close()
    assert "boom" in log.read_text(encoding="utf-8")


def test_stdin_stays_devnull(tmp_path):
    with mock.patch("sonari.paths.DAEMON_ERR_PATH", tmp_path / "e.log"):
        _, kwargs = sup_mod.MacSupervisorBackend().launch_spec()
    assert kwargs["stdin"] is subprocess.DEVNULL
    kwargs["stderr"].close()


def test_an_unwritable_log_falls_back_to_devnull(tmp_path):
    """Diagnostics must never prevent the daemon from starting."""
    with mock.patch("sonari.paths.DAEMON_ERR_PATH", tmp_path / "no" / "such" / "e.log"):
        _, kwargs = sup_mod.MacSupervisorBackend().launch_spec()
    assert kwargs["stderr"] is subprocess.DEVNULL
