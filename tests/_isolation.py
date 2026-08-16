"""The one Sonari isolation list: repoint every path, or repoint none.

`tests/conftest.py`'s autouse fixture calls this, and so must any ad-hoc script
that runs Sonari code against a sacrificial directory. It exists because
PARTIAL isolation caused a real outage on 2026-08-15: a probe script repointed
`sonari.paths.*` but missed the by-value binds
(`install_record.INSTALL_RECORD_PATH`, `client.LOCK_PATH`), so it overwrote the
developer's real ~/.sonari/install.json and deleted the real
~/.sonari/daemon.lock while the real daemon kept running. That lockfile is the
only place the daemon's port and token live, so the daemon became permanently
unreachable -- and it still held the single-instance flock, so no replacement
could start either. The user, who is blind, had no speech at all for about
nineteen hours.

One call repoints everything, so a caller can no longer end up with half of it.

Underscore-prefixed so pytest does not collect it (same as `_fakeplatform.py`,
`_fakeclient/`).

Script usage -- `tests/` is not a package, so put it on sys.path first, the same
way `tests/test_sonari_hook_bin.py` reaches the repo's `bin/`:

    import sys, tempfile
    from pathlib import Path

    REPO = Path("/path/to/sonari")
    sys.path.insert(0, str(REPO / "tests"))
    sys.path.insert(0, str(REPO / "src"))

    from _isolation import isolate_paths

    sandbox = Path(tempfile.mkdtemp())
    isolate_paths(sandbox / ".sonari")   # process-lifetime, no monkeypatch
    # every sonari module now reads and writes under `sandbox`, never under ~

Under pytest, pass the test's monkeypatch instead so the repoints revert per
test:

    isolate_paths(tmp_path / ".sonari", monkeypatch)
"""
from pathlib import Path


def isolate_paths(root, monkeypatch=None) -> None:
    """Redirect every Sonari path constant to live under `root`.

    `root` is the directory to use as SONARI_DIR (e.g. `tmp_path / ".sonari"`).
    Two of the repointed locations are NOT Sonari-owned in real life
    (`~/.local/bin/sonari` and `~/Library/LaunchAgents/com.sonari.*.plist`), so
    they are placed BESIDE `root`, in `root.parent` -- give `root` a parent you
    own (a tmp_path, a mkdtemp), never a directory you care about.

    With `monkeypatch`, the repoints revert when the test ends. Without it they
    last for the life of the process, which is what an ad-hoc script wants.
    """
    if monkeypatch is not None:
        def _setattr(obj, name, value):
            monkeypatch.setattr(obj, name, value, raising=False)
    else:
        # Plain setattr is the exact analogue of monkeypatch's raising=False:
        # it binds the name whether or not the module already had one.
        def _setattr(obj, name, value):
            setattr(obj, name, value)

    # Do NOT pre-create the directory: several tests (test_config,
    # test_paths, test_cli_uninstall) assert SONARI_DIR does not yet exist and
    # then verify their own code creates it. save_config()/ensure_sonari_dir()
    # create it on demand on first write.
    sonari_dir = Path(root)
    # ~/.local/bin and ~/Library/LaunchAgents are the user's, not Sonari's, so
    # their stand-ins are siblings of sonari_dir rather than children of it.
    sibling_root = sonari_dir.parent

    import sonari.paths as paths

    _setattr(paths, "SONARI_DIR", sonari_dir)
    # APP_DIR is SONARI_DIR/"app" bound at import; it is NOT derived live, so
    # patching SONARI_DIR alone leaves it pointing at the real ~/.sonari/app.
    # The uninstall path shutil.rmtree(APP_DIR)s it -- without this repoint, a
    # plain `pytest` run DELETES the developer's live daemon copy (it did).
    _setattr(paths, "APP_DIR", sonari_dir / "app")
    _setattr(paths, "CONFIG_PATH", sonari_dir / "config.json")
    _setattr(paths, "LOCK_PATH", sonari_dir / "daemon.lock")
    # client.send does `from sonari.paths import LOCK_PATH` (a by-value bind), so
    # patching paths.LOCK_PATH alone leaves the client reading the developer's
    # real ~/.sonari/daemon.lock. Repoint the client module's copy too.
    import sonari.client as client_mod
    _setattr(client_mod, "LOCK_PATH", sonari_dir / "daemon.lock")
    _setattr(paths, "LOG_PATH", sonari_dir / "speechd.log")
    _setattr(paths, "KEYMAP_PATH", sonari_dir / "keymap.json")
    _setattr(paths, "HOTKEYD_RESOLVED_PATH", sonari_dir / "hotkeyd.resolved.json")
    _setattr(paths, "HOTKEYD_BIN_PATH", sonari_dir / "sonari-hotkeyd")
    _setattr(paths, "INSTALL_RECORD_PATH", sonari_dir / "install.json")
    # install_record.py binds INSTALL_RECORD_PATH by value at import; patching
    # paths.INSTALL_RECORD_PATH alone leaves it reading the real ~/.sonari/install.json.
    import sonari.install_record as install_record
    _setattr(install_record, "INSTALL_RECORD_PATH", sonari_dir / "install.json")

    # Modules that bound these names at import time need their copies repointed too.
    import sonari.config as config

    _setattr(config, "SONARI_DIR", sonari_dir)
    _setattr(config, "CONFIG_PATH", sonari_dir / "config.json")

    # keymap.py binds KEYMAP_PATH/HOTKEYD_RESOLVED_PATH/SONARI_DIR by value at
    # import time, so patching paths.* alone does not redirect it. Repoint the
    # keymap module's copies too so no test (e.g. the `keymap` subcommand, which
    # reads load_keymap()) can ever read or write the real ~/.sonari.
    import sonari.keymap as keymap

    _setattr(keymap, "SONARI_DIR", sonari_dir)
    _setattr(keymap, "KEYMAP_PATH", sonari_dir / "keymap.json")
    _setattr(keymap, "HOTKEYD_RESOLVED_PATH", sonari_dir / "hotkeyd.resolved.json")

    # daemon/host.py binds LOCK_PATH by value at import; daemon/bootstrap.py binds
    # SINGLETON_PATH and main() takes an exclusive flock on it for single-instance.
    # Repoint each module's copy per-test (each test has a unique sonari_dir) and
    # reset the process-wide held-flock global so a main()-calling test never
    # blocks a later one.
    _setattr(paths, "SINGLETON_PATH", sonari_dir / "daemon.singleton")
    # STATE_PATH is SONARI_DIR/"state.json" bound at import (same trap as APP_DIR
    # above): without this repoint, any doctor() call that does not explicitly
    # mock sonari.paths.STATE_PATH reads the developer's real ~/.sonari/state.json.
    _setattr(paths, "STATE_PATH", sonari_dir / "state.json")
    # FAULTLOG_PATH is SONARI_DIR/"faulthandler.log" bound at import (same trap):
    # without this repoint, _arm_faulthandler()'s live `from sonari.paths import
    # FAULTLOG_PATH` and any doctor() call that does not explicitly mock
    # sonari.paths.FAULTLOG_PATH would read/write the developer's real
    # ~/.sonari/faulthandler.log.
    _setattr(paths, "FAULTLOG_PATH", sonari_dir / "faulthandler.log")
    # ~/.local/bin/sonari is built from os.path.expanduser("~") inside the macOS
    # supervisor, NOT from SONARI_DIR, so none of the repoints above reach it.
    # Un-isolated, MacSupervisorBackend.uninstall() deletes the DEVELOPER'S REAL
    # launcher: running the suite made `sonari` vanish from this machine until
    # the next install. Proven by running the suite and stat-ing the file.
    local_bin = sibling_root / "local-bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    import sonari.platform.macos.supervisor as _sup
    _setattr(_sup, "_local_bin_dir", lambda: str(local_bin))
    _setattr(_sup, "_launcher_path", lambda: str(local_bin / "sonari"))
    # DAEMON_ERR_PATH is SONARI_DIR/"daemon.err.log". supervisor.py's launch_spec()
    # reads it live off the `paths` module (no by-value bind), but any test that
    # calls launch_spec() without explicitly mocking the path (e.g.
    # test_macos_supervisor.py's start_new_session test) would still open the
    # developer's real ~/.sonari/daemon.err.log without this default repoint.
    _setattr(paths, "DAEMON_ERR_PATH", sonari_dir / "daemon.err.log")
    # DAEMON_FAIL_MEMO_PATH is SONARI_DIR/"daemon.fail_memo" bound at import (same
    # trap as LOCK_PATH above): client.py's `from sonari.paths import
    # DAEMON_FAIL_MEMO_PATH` is a by-value bind, so without repointing both the
    # paths module's copy AND the client module's copy, ensure_daemon() tests
    # would read/write the developer's real ~/.sonari/daemon.fail_memo.
    _setattr(paths, "DAEMON_FAIL_MEMO_PATH", sonari_dir / "daemon.fail_memo")
    _setattr(client_mod, "DAEMON_FAIL_MEMO_PATH", sonari_dir / "daemon.fail_memo")
    import sonari.daemon.host as daemon_host
    import sonari.daemon.bootstrap as daemon_bootstrap
    _setattr(daemon_host, "LOCK_PATH", sonari_dir / "daemon.lock")
    # SPEAK_FAIL_MEMO_PATH is SONARI_DIR/"speak.fail_memo" bound at import (same
    # trap as DAEMON_FAIL_MEMO_PATH above): daemon/host.py's `from sonari.paths
    # import SPEAK_FAIL_MEMO_PATH` is a by-value bind, so without repointing both
    # the paths module's copy AND host's copy, speak-failure-memo tests would
    # read/write the developer's real ~/.sonari/speak.fail_memo. doctor.py reads
    # it live off `paths.SPEAK_FAIL_MEMO_PATH` (no by-value bind of its own), so
    # the paths-module repoint alone covers it there.
    _setattr(paths, "SPEAK_FAIL_MEMO_PATH", sonari_dir / "speak.fail_memo")
    _setattr(daemon_host, "SPEAK_FAIL_MEMO_PATH", sonari_dir / "speak.fail_memo")
    _setattr(daemon_bootstrap, "SINGLETON_PATH", sonari_dir / "daemon.singleton")
    _setattr(daemon_bootstrap, "_SINGLETON", None)
    # KOKORO_VENV is SONARI_DIR/"venv" bound at import (same trap as APP_DIR/
    # STATE_PATH above): kokoro_provision.uninstall_kokoro() rmtree()s it, and
    # every existing test that reaches that call currently only stays safe by
    # remembering to patch this locally. Without this repoint, a new test
    # that forgets the local patch deletes the developer's real ~/.sonari/venv
    # (a multi-hundred-MB neural-voice environment).
    _setattr(paths, "KOKORO_VENV", sonari_dir / "venv")
    # RAISE_BIN_PATH is SONARI_DIR/"sonari-raise" bound at import (same trap):
    # MacRaiseBackend.build() writes the compiled helper there. Its sibling
    # HOTKEYD_BIN_PATH already gets this treatment above; this one was simply
    # missed when introduced later -- without this repoint, a rebuild
    # overwrites the developer's real compiled helper and silently drops the
    # macOS Automation grant they already approved (a rebuild changes the
    # binary's cdhash).
    _setattr(paths, "RAISE_BIN_PATH", sonari_dir / "sonari-raise")
    # SPEECHD_LAUNCH_AGENT_PATH / HOTKEYD_LAUNCH_AGENT_PATH are
    # ~/Library/LaunchAgents/com.sonari.{speechd,hotkeyd}.plist. Real files:
    # install() writes + launchctl-loads them, uninstall() launchctl-unloads
    # + os.remove()s them. Both supervisor.py and hotkeys.py bind their own
    # module-level LAUNCH_AGENT_PATH from these BY VALUE at import (same trap
    # as APP_DIR/INSTALL_RECORD_PATH above), so patching paths.* alone would
    # not redirect them -- repoint each module's copy too. Sibling of
    # sonari_dir (not inside it): a real LaunchAgents dir is not Sonari-owned,
    # same reasoning as local_bin above.
    launch_agents_dir = sibling_root / "LaunchAgents"
    _setattr(
        paths, "SPEECHD_LAUNCH_AGENT_PATH",
        launch_agents_dir / "com.sonari.speechd.plist")
    _setattr(
        paths, "HOTKEYD_LAUNCH_AGENT_PATH",
        launch_agents_dir / "com.sonari.hotkeyd.plist")
    _setattr(
        _sup, "LAUNCH_AGENT_PATH",
        str(launch_agents_dir / "com.sonari.speechd.plist"))
    import sonari.platform.macos.hotkeys as _hotkeys
    _setattr(
        _hotkeys, "LAUNCH_AGENT_PATH",
        str(launch_agents_dir / "com.sonari.hotkeyd.plist"))
