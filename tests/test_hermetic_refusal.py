"""D0.1: the suite refuses to run against the real HOME, and proves it left it alone.

The suite has destroyed the owner's real install twice. ~/.sonari and
~/.local/bin are outside git, so `git status` clean is not evidence.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md §3.1.
"""
import os
import pathlib
import pwd
import subprocess
import sys

import conftest


REPO = pathlib.Path(__file__).resolve().parent.parent


def test_real_home_is_computed_from_the_password_database_not_the_environment(
    monkeypatch,
):
    """$HOME is a lie inside this suite; getpwuid is not."""
    monkeypatch.setenv("HOME", "/nowhere/at/all")
    assert conftest._real_home() == pathlib.Path(
        pwd.getpwuid(os.getuid()).pw_dir
    )
    assert conftest._real_home() != pathlib.Path(os.environ["HOME"])


def test_the_canary_watches_every_path_the_suite_has_destroyed():
    """~/.sonari, ~/.local/bin/sonari and both LaunchAgent plists."""
    real = conftest._real_home()
    watched = set(conftest._CANARY_PATHS)
    assert real / ".sonari" in watched
    assert real / ".local" / "bin" / "sonari" in watched
    assert (
        real / "Library" / "LaunchAgents" / "com.sonari.speechd.plist"
    ) in watched
    assert (
        real / "Library" / "LaunchAgents" / "com.sonari.hotkeyd.plist"
    ) in watched


def test_pytest_refuses_to_start_when_HOME_is_the_real_home(tmp_path):
    """The refusal aborts the run. Not a test failure -- an abort before
    collection completes, because a test failure arrives after the damage."""
    env = dict(os.environ)
    env["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only",
         "tests/test_config.py"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode != 0, "the suite started against the real HOME"
    combined = proc.stdout + proc.stderr
    assert "REFUSING" in combined, combined[-2000:]
