import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# pytest already prepends this directory (tests/ has no __init__.py), but say so
# explicitly: _isolation is also imported by scripts that run outside pytest,
# and the two entry points should reach it the same way.
_HERE = _REPO_ROOT / "tests"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest

from _isolation import isolate_paths

# --- D0.1 refusal: this suite may never run against the real home ------------
# The suite has destroyed the owner's real install TWICE. ~/.sonari and
# ~/.local/bin are outside git, so `git status` clean is not evidence, and a
# test FAILURE arrives after the damage. So: abort before collection completes.
#
# pwd.getpwuid reads the password database and is immune to $HOME -- it is the
# one source of truth an errant repoint cannot forge. This is the same guard
# scratchpad/e3-review/probe_receipts.py and probe_counterfactual.py already
# carry, promoted from the probe rigs into the suite itself.
#
# It REFUSES rather than silently repointing. Two reasons. A repoint would
# make a bare `pytest` quietly work, which trains exactly the habit that
# caused both outages. And the caller's `find "$SAC" -mindepth 1 | wc -l`
# residue check is this suite's hermeticity witness -- moving $HOME out from
# under it would make that check vacuous, because a leak would land in a
# directory nobody is watching.
import os
import pathlib
import pwd


def _real_home() -> pathlib.Path:
    """The real home, independent of $HOME. Never cache -- tests monkeypatch."""
    return pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir)


_REAL_HOME = _real_home()

# Every path the suite has damaged, or could damage, derived from the REAL home
# rather than from sonari.paths (which is repointed by the time tests run).
#
# Split by how the owner's LIVE daemon behaves, because a canary that always
# fires is one that gets switched off -- and then it is not there the time it
# matters. The daemon writes ~/.sonari/state.json by atomic rename, and a
# rename bumps the CONTAINING DIRECTORY's mtime, so ~/.sonari's mtime moves
# every few seconds while he is working. Its inode and its existence do not.
#
# Watching only ~/.sonari would also miss the first of the two recorded
# destructions outright: kokoro_provision.uninstall_kokoro() rmtree's
# ~/.sonari/venv, which changes neither the parent's inode nor its existence.
# So the cold children are watched directly.
_CANARY_IDENTITY_ONLY = (
    _REAL_HOME / ".sonari",              # the daemon writes inside it constantly
    _REAL_HOME / ".sonari" / "venv",     # uninstall_kokoro() rmtree's this
    _REAL_HOME / ".sonari" / "app",      # the installed app copy
)
_CANARY_FULL_STAT = (
    _REAL_HOME / ".local" / "bin" / "sonari",
    _REAL_HOME / "Library" / "LaunchAgents" / "com.sonari.speechd.plist",
    _REAL_HOME / "Library" / "LaunchAgents" / "com.sonari.hotkeyd.plist",
)
_CANARY_PATHS = _CANARY_IDENTITY_ONLY + _CANARY_FULL_STAT

_REFUSAL = (
    "REFUSING to run: {0}. This suite calls uninstall paths that rmtree "
    "~/.sonari and remove ~/.local/bin/sonari and the LaunchAgents -- all "
    "outside git. It has destroyed the live install twice. Re-run under a "
    "sacrificial HOME:\n"
    "    mktemp -d \"$TMPDIR/sonari-home.XXXXXX\"\n"
    "    HOME=<that path> .venv/bin/python -m pytest -q"
)

_HOME = os.environ.get("HOME") or ""
if not _HOME:
    pytest.exit(_REFUSAL.format("$HOME is unset"), returncode=3)
if pathlib.Path(_HOME) == _REAL_HOME:
    pytest.exit(
        _REFUSAL.format("$HOME is the real home ({0})".format(_REAL_HOME)),
        returncode=3,
    )
# Belt to those braces: paths.py derives every constant from Path.home(), so a
# Path.home() that disagrees with $HOME means the isolation is a fiction.
if pathlib.Path.home() != pathlib.Path(_HOME):
    pytest.exit(
        _REFUSAL.format(
            "Path.home() ({0}) disagrees with $HOME ({1})".format(
                pathlib.Path.home(), _HOME)),
        returncode=3,
    )


@pytest.fixture(autouse=True)
def _no_blocking_prompts(monkeypatch):
    """A test that reaches a real input() hangs the whole suite with no output.

    `uninstall()` prompts before deleting transcripts, gated on isatty(); pytest's
    captured stdout reports False, so it is unreachable *by accident today* — but
    that is incidental, not designed. Under `pytest -s`, or a CI runner that
    allocates a tty, it would block forever. Two agents were lost to exactly this
    hang before it was diagnosed. Fail loudly instead: any test that genuinely
    needs input() mocks it, and its mock takes precedence over this fixture.
    """
    def _refuse(prompt=""):
        raise AssertionError(
            "a test reached a real input() — mock it; an unmocked prompt hangs "
            "the suite instead of failing it (prompt was: {0!r})".format(prompt))
    monkeypatch.setattr("builtins.input", _refuse)


@pytest.fixture(autouse=True)
def _isolate_sonari_dir(tmp_path, monkeypatch):
    """Redirect every Sonari path to a per-test tmp dir.

    save_config (and anything else that writes under SONARI_DIR) targets
    CONFIG_PATH = ~/.sonari/config.json by default, which lives OUTSIDE the repo
    and is not git-tracked. Without isolation, daemon tests that exercise the
    real save_config() (e.g. the SET_RATE delta path) mutate the developer's
    actual Sonari config as a filesystem side effect. This autouse fixture
    repoints the path constants on every module that imported them so no test
    can ever touch the real ~/.sonari.

    The repoint list itself lives in tests/_isolation.py rather than here, so
    that an ad-hoc script can apply the identical list without pytest. Getting
    only part of that list is what caused the 2026-08-15 outage; the module's
    docstring carries the story and every per-constant comment.
    """
    isolate_paths(tmp_path / ".sonari", monkeypatch)
    yield


@pytest.fixture(autouse=True)
def _inert_keepalive_seams(monkeypatch):
    """No test may spawn a REAL keep-alive player or arm a real Timer.

    Any SpeechDaemon now builds a KeepAliveManager, and a SESSION_START (or one
    speak-loop tick) pushes set_active(True) into it — which on the DEFAULT seams
    launches `afplay` on 300 s of silence and arms a 295 s threading.Timer that
    outlives the test. 19 tests construct SpeechDaemon directly instead of via
    make_daemon (test_frontier, test_concurrency_guards, test_e2e_pipeline,
    test_blackbox_net, test_speaker_cancel_2b) and hit exactly that — measured,
    not assumed. Patching every construction site would leave the next new test
    file unprotected, so neutralise the DEFAULTS at the class instead: a manager
    that was handed explicit seams (test_keepalive_manager, and the keep-alive
    wiring tests, which overwrite theirs after construction) keeps them.

    Same job for the presence check's HID sampler (Task 6): the daemon binds
    host._hid_idle_seconds at construction, and a reaping tick shells out to
    `ioreg` whenever its cache is stale. Repointing the module global before any
    daemon is built keeps the suite subprocess-free AND deterministic — an
    unattended run on a machine idle past KEEPALIVE_PRESENCE_S would otherwise
    read a REAL "absent" and turn keep-alive off under tests asserting "running".
    Presence tests inject their own counting seam per-daemon on top of this.
    """
    import subprocess
    import threading

    import sonari.daemon.host as daemon_host
    import sonari.daemon.keepalive as keepalive
    from tests.daemon_helpers import (InertKeepaliveProc, InertKeepaliveTimer,
                                      inert_hid_idle)

    monkeypatch.setattr(daemon_host, "_hid_idle_seconds", inert_hid_idle)

    real_init = keepalive.KeepAliveManager.__init__

    def _inert_init(self, popen=None, timer_factory=None, clock=None):
        real_init(self, popen=popen, timer_factory=timer_factory, clock=clock)
        if self._popen is subprocess.Popen:
            self._popen = lambda cmd: InertKeepaliveProc()
        if self._timer_factory is threading.Timer:
            self._timer_factory = InertKeepaliveTimer

    monkeypatch.setattr(keepalive.KeepAliveManager, "__init__", _inert_init)


def _canary_stat(path: pathlib.Path):
    """Identity of *path*, or None when absent. Never raises.

    Paths the live daemon writes into get identity only (existence + inode):
    enough to catch a delete or a replace, immune to the mtime the daemon
    bumps every few seconds. Cold paths get the full stat.
    """
    try:
        st = path.stat()
    except (OSError, ValueError):
        return None
    if path in _CANARY_IDENTITY_ONLY:
        return (st.st_ino,)
    return (st.st_mtime_ns, st.st_ino, st.st_size)


@pytest.fixture(scope="session", autouse=True)
def _real_home_canary():
    """Detective to the refusal's preventive.

    The refusal stops the suite from starting in the wrong place. This proves,
    after the fact, that it did not touch the real install anyway -- and it is
    the only thing that would have NAMED the culprit either of the two times
    this happened.
    """
    before = {p: _canary_stat(p) for p in _CANARY_PATHS}
    yield
    changed = [
        str(p) for p in _CANARY_PATHS if _canary_stat(p) != before[p]
    ]
    assert not changed, (
        "THE SUITE TOUCHED THE REAL INSTALL: {0}. These paths are outside "
        "git; `git status` clean is not evidence. Find the test that did it "
        "before running anything else.".format(sorted(changed))
    )
