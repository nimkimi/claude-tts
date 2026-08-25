import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest


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
    """
    # Do NOT pre-create the directory: several tests (test_config,
    # test_paths, test_cli_uninstall) assert SONARI_DIR does not yet exist and
    # then verify their own code creates it. save_config()/ensure_sonari_dir()
    # create it on demand on first write.
    sonari_dir = tmp_path / ".sonari"

    import sonari.paths as paths

    monkeypatch.setattr(paths, "SONARI_DIR", sonari_dir, raising=False)
    # APP_DIR is SONARI_DIR/"app" bound at import; it is NOT derived live, so
    # patching SONARI_DIR alone leaves it pointing at the real ~/.sonari/app.
    # The uninstall path shutil.rmtree(APP_DIR)s it — without this repoint, a
    # plain `pytest` run DELETES the developer's live daemon copy (it did).
    monkeypatch.setattr(paths, "APP_DIR", sonari_dir / "app", raising=False)
    monkeypatch.setattr(paths, "CONFIG_PATH", sonari_dir / "config.json", raising=False)
    monkeypatch.setattr(paths, "LOCK_PATH", sonari_dir / "daemon.lock", raising=False)
    # client.send does `from sonari.paths import LOCK_PATH` (a by-value bind), so
    # patching paths.LOCK_PATH alone leaves the client reading the developer's
    # real ~/.sonari/daemon.lock. Repoint the client module's copy too.
    import sonari.client as client_mod
    monkeypatch.setattr(client_mod, "LOCK_PATH", sonari_dir / "daemon.lock", raising=False)
    monkeypatch.setattr(paths, "LOG_PATH", sonari_dir / "speechd.log", raising=False)
    monkeypatch.setattr(paths, "KEYMAP_PATH", sonari_dir / "keymap.json", raising=False)
    monkeypatch.setattr(
        paths, "HOTKEYD_RESOLVED_PATH", sonari_dir / "hotkeyd.resolved.json",
        raising=False)
    monkeypatch.setattr(
        paths, "HOTKEYD_BIN_PATH", sonari_dir / "sonari-hotkeyd", raising=False)
    monkeypatch.setattr(
        paths, "INSTALL_RECORD_PATH", sonari_dir / "install.json", raising=False)
    # install_record.py binds INSTALL_RECORD_PATH by value at import; patching
    # paths.INSTALL_RECORD_PATH alone leaves it reading the real ~/.sonari/install.json.
    import sonari.install_record as install_record
    monkeypatch.setattr(
        install_record, "INSTALL_RECORD_PATH", sonari_dir / "install.json",
        raising=False)

    # Modules that bound these names at import time need their copies repointed too.
    import sonari.config as config

    monkeypatch.setattr(config, "SONARI_DIR", sonari_dir, raising=False)
    monkeypatch.setattr(config, "CONFIG_PATH", sonari_dir / "config.json", raising=False)

    # keymap.py binds KEYMAP_PATH/HOTKEYD_RESOLVED_PATH/SONARI_DIR by value at
    # import time, so patching paths.* alone does not redirect it. Repoint the
    # keymap module's copies too so no test (e.g. the `keymap` subcommand, which
    # reads load_keymap()) can ever read or write the real ~/.sonari.
    import sonari.keymap as keymap

    monkeypatch.setattr(keymap, "SONARI_DIR", sonari_dir, raising=False)
    monkeypatch.setattr(keymap, "KEYMAP_PATH", sonari_dir / "keymap.json", raising=False)
    monkeypatch.setattr(
        keymap, "HOTKEYD_RESOLVED_PATH", sonari_dir / "hotkeyd.resolved.json",
        raising=False)

    # daemon/host.py binds LOCK_PATH by value at import; daemon/bootstrap.py binds
    # SINGLETON_PATH and main() takes an exclusive flock on it for single-instance.
    # Repoint each module's copy per-test (each test has a unique sonari_dir) and
    # reset the process-wide held-flock global so a main()-calling test never
    # blocks a later one.
    monkeypatch.setattr(paths, "SINGLETON_PATH", sonari_dir / "daemon.singleton", raising=False)
    # STATE_PATH is SONARI_DIR/"state.json" bound at import (same trap as APP_DIR
    # above): without this repoint, any doctor() call that does not explicitly
    # mock sonari.paths.STATE_PATH reads the developer's real ~/.sonari/state.json.
    monkeypatch.setattr(paths, "STATE_PATH", sonari_dir / "state.json", raising=False)
    # FAULTLOG_PATH is SONARI_DIR/"faulthandler.log" bound at import (same trap):
    # without this repoint, _arm_faulthandler()'s live `from sonari.paths import
    # FAULTLOG_PATH` and any doctor() call that does not explicitly mock
    # sonari.paths.FAULTLOG_PATH would read/write the developer's real
    # ~/.sonari/faulthandler.log.
    monkeypatch.setattr(paths, "FAULTLOG_PATH", sonari_dir / "faulthandler.log", raising=False)
    # KEEPALIVE_WAV_PATH is SONARI_DIR/"keepalive.wav". keepalive.py's
    # ensure_silence_wav() reads it live off the `paths` module (import inside
    # the function, no by-value bind), but without this repoint it would still
    # generate the silent WAV under the developer's real ~/.sonari.
    monkeypatch.setattr(paths, "KEEPALIVE_WAV_PATH", sonari_dir / "keepalive.wav", raising=False)
    # ~/.local/bin/sonari is built from os.path.expanduser("~") inside the macOS
    # supervisor, NOT from SONARI_DIR, so none of the repoints above reach it.
    # Un-isolated, MacSupervisorBackend.uninstall() deletes the DEVELOPER'S REAL
    # launcher: running the suite made `sonari` vanish from this machine until
    # the next install. Proven by running the suite and stat-ing the file.
    local_bin = tmp_path / "local-bin"
    local_bin.mkdir(exist_ok=True)
    import sonari.platform.macos.supervisor as _sup
    monkeypatch.setattr(_sup, "_local_bin_dir", lambda: str(local_bin), raising=False)
    monkeypatch.setattr(_sup, "_launcher_path", lambda: str(local_bin / "sonari"),
                        raising=False)
    # DAEMON_ERR_PATH is SONARI_DIR/"daemon.err.log". supervisor.py's launch_spec()
    # reads it live off the `paths` module (no by-value bind), but any test that
    # calls launch_spec() without explicitly mocking the path (e.g.
    # test_macos_supervisor.py's start_new_session test) would still open the
    # developer's real ~/.sonari/daemon.err.log without this default repoint.
    monkeypatch.setattr(paths, "DAEMON_ERR_PATH", sonari_dir / "daemon.err.log", raising=False)
    # DAEMON_FAIL_MEMO_PATH is SONARI_DIR/"daemon.fail_memo" bound at import (same
    # trap as LOCK_PATH above): client.py's `from sonari.paths import
    # DAEMON_FAIL_MEMO_PATH` is a by-value bind, so without repointing both the
    # paths module's copy AND the client module's copy, ensure_daemon() tests
    # would read/write the developer's real ~/.sonari/daemon.fail_memo.
    monkeypatch.setattr(
        paths, "DAEMON_FAIL_MEMO_PATH", sonari_dir / "daemon.fail_memo", raising=False)
    monkeypatch.setattr(
        client_mod, "DAEMON_FAIL_MEMO_PATH", sonari_dir / "daemon.fail_memo", raising=False)
    import sonari.daemon.host as daemon_host
    import sonari.daemon.bootstrap as daemon_bootstrap
    monkeypatch.setattr(daemon_host, "LOCK_PATH", sonari_dir / "daemon.lock", raising=False)
    monkeypatch.setattr(daemon_bootstrap, "SINGLETON_PATH", sonari_dir / "daemon.singleton", raising=False)
    monkeypatch.setattr(daemon_bootstrap, "_SINGLETON", None, raising=False)

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
