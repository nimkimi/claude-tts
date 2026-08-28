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
