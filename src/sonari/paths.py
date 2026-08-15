from __future__ import annotations

import os
from pathlib import Path

SONARI_DIR = Path.home() / ".sonari"
APP_DIR = SONARI_DIR / "app"          # stable copy of the sonari package (PYTHONPATH target)
CONFIG_PATH = SONARI_DIR / "config.json"
LOCK_PATH = SONARI_DIR / "daemon.lock"
SINGLETON_PATH = SONARI_DIR / "daemon.singleton"   # held-open flock: single-instance
STATE_PATH = SONARI_DIR / "state.json"   # SP6 durable-state snapshot (daemon/persistence.py)
LOG_PATH = SONARI_DIR / "speechd.log"
KEYMAP_PATH = SONARI_DIR / "keymap.json"
HOTKEYD_RESOLVED_PATH = SONARI_DIR / "hotkeyd.resolved.json"
HOTKEYD_BIN_PATH = SONARI_DIR / "sonari-hotkeyd"
RAISE_BIN_PATH = SONARI_DIR / "sonari-raise"
INSTALL_RECORD_PATH = SONARI_DIR / "install.json"
FAULTLOG_PATH = SONARI_DIR / "faulthandler.log"   # native-crash dump; see daemon/bootstrap.py
DAEMON_ERR_PATH = SONARI_DIR / "daemon.err.log"   # lazy-relaunch's stderr; see macos/supervisor.py
# mtime = timestamp of the last ensure_daemon() spawn failure. Persisted to disk
# (not an in-process variable) because bin/sonari-hook fires as a brand-new OS
# process per hook event -- an in-memory memo cannot survive between calls. See
# client.py's ensure_daemon()/reset_failure_memo().
DAEMON_FAIL_MEMO_PATH = SONARI_DIR / "daemon.fail_memo"
# mtime = timestamp of the last recorded Speaker.speak() failure (I3): a broken
# audio device (say/afplay exits nonzero, a spawn failure, no runner configured)
# plays NOTHING and, without this, leaves no trace an eyes-free user could ever
# find -- doctor's "speech path" row reads this memo to surface it (the
# AudioQueueStart(-66681) incident: speechd.log carried the failure while
# doctor still said healthy). Same on-disk-not-in-process reasoning as
# DAEMON_FAIL_MEMO_PATH (this one crosses a daemon-process/CLI-process
# boundary, not a hook-process one). Written/cleared from
# daemon/host.py's _signal_speak_failure()/note_spoken(); read from
# cli/doctor.py's speech-path row.
SPEAK_FAIL_MEMO_PATH = SONARI_DIR / "speak.fail_memo"
KOKORO_VENV = SONARI_DIR / "venv"   # opt-in uv-managed venv for neural voices
# macOS-only LaunchAgent plist paths (platform/macos/supervisor.py and
# platform/macos/hotkeys.py each bind their own module-level LAUNCH_AGENT_PATH
# from these, str()-converted, at import time -- a by-value bind, so their
# module copies need their own conftest repoint too, same pattern as
# HOTKEYD_BIN_PATH). Centralized here rather than left as each module's
# private os.path.expanduser() call so the paths.py-vs-conftest hermeticity
# guard (test_paths_conftest_isolation.py) covers them like every other
# constant, instead of relying on per-test discipline.
SPEECHD_LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / "com.sonari.speechd.plist"
HOTKEYD_LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / "com.sonari.hotkeyd.plist"


def kokoro_venv_python() -> str:
    """Absolute path to the neural venv's Python interpreter (may not exist)."""
    return str(KOKORO_VENV / "bin" / "python")


def ensure_sonari_dir() -> None:
    SONARI_DIR.mkdir(parents=True, exist_ok=True)


def socket_connectable() -> bool:
    """Return True if the daemon is accepting connections (TCP lockfile)."""
    from sonari.platform import transport
    return transport.connectable(LOCK_PATH)


def repo_root() -> str:
    """Return the absolute path to the repository root.

    The canonical derivation: this file lives at <repo>/src/sonari/paths.py,
    so the repo root is two directories up from the directory containing it.
    """
    here = os.path.dirname(os.path.abspath(__file__))  # src/sonari
    return os.path.dirname(os.path.dirname(here))       # repo root
