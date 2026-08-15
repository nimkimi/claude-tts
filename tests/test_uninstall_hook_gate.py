"""I5 (whole-branch review finding): from uninstall()'s first destructive step
until it returns, bin/sonari-hook's ensure_daemon gate must stay CLOSED.

T19 (tests/test_hook_install_gate.py) gates the spawn on install.json OR
APP_DIR -- BOTH must read absent for the hook to treat Sonari as uninstalled
(a missing record ALONE is the ordinary recoverable state for a live user,
see test_a_missing_record_alone_must_NOT_disable_lazy_relaunch there). T20's
daemon-stop ran BEFORE either signal was removed, so for the whole uninstall
run -- including the death-proof window -- a hook event from any OTHER live
Claude Code session could respawn a fresh daemon. "Exists only through T19's
and T20's composition," per the finding.

These tests fire the REAL bin/sonari-hook (no install_record/paths mocking,
matching test_hook_install_gate.py's runpy idiom but WITHOUT its record/
app_dir mocks) so it observes exactly what uninstall() has actually written
to disk at the moment a hook lands mid-teardown.
"""
import runpy
import sys
from pathlib import Path
from unittest import mock

import pytest

from sonari import install_record, paths
from sonari.cli import install as install_cmd

HOOK = str(Path(__file__).resolve().parent.parent / "bin" / "sonari-hook")


def _fire_hook(monkeypatch):
    """Invoke bin/sonari-hook in-process. Only ensure_daemon/send/handle_event
    are mocked -- install_record and paths are left real."""
    monkeypatch.setattr(sys, "argv", ["sonari-hook", "Notification"])
    monkeypatch.setattr(sys, "stdin", mock.MagicMock(
        buffer=mock.MagicMock(read=lambda: b"{}")))
    with mock.patch("sonari.client.ensure_daemon") as ensure, \
         mock.patch("sonari.client.send"), \
         mock.patch("sonari.hooks_entry.handle_event",
                    return_value=[{"type": "prose", "text": "hi"}]):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(HOOK, run_name="__main__")
        assert exc.value.code == 0
    return ensure


def _installed():
    """A genuinely-installed Sonari: a real install.json + a real APP_DIR,
    both under conftest's per-test isolated SONARI_DIR."""
    install_record.write_install_record(
        python="/py", python_version="3.11", plugin_root="/plugin",
        app_path=str(paths.APP_DIR), plugin_version="0.10.1")
    paths.APP_DIR.mkdir(parents=True, exist_ok=True)


def test_a_hook_firing_while_the_daemon_stop_runs_spawns_nothing(monkeypatch):
    """The invariant. Must FAIL against today's order: install.json/APP_DIR
    removal happens strictly AFTER stop_daemon() returns, so both signals are
    still live while stop_daemon runs and the hook's OR-gate reads "installed"."""
    _installed()
    spawned = {}

    def _stop_side_effect(*a, **k):
        spawned["ensure"] = _fire_hook(monkeypatch)
        return "stopped"

    with mock.patch("sonari.cli._platform", return_value=mock.MagicMock()), \
         mock.patch("sonari.cli.teardown.stop_daemon",
                    side_effect=_stop_side_effect):
        install_cmd.uninstall()

    spawned["ensure"].assert_not_called()


def test_a_hook_firing_during_the_launchagent_unload_spawns_nothing(monkeypatch):
    """The window opens at the FIRST destructive step, not just at stop_daemon
    -- sup.uninstall() (unloading LaunchAgents) runs before it."""
    _installed()
    spawned = {}

    sup = mock.MagicMock()

    def _unload_side_effect(*a, **k):
        spawned["ensure"] = _fire_hook(monkeypatch)

    sup.uninstall.side_effect = _unload_side_effect

    with mock.patch("sonari.cli._platform",
                     return_value=mock.MagicMock(supervisor=sup)), \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"):
        install_cmd.uninstall()

    spawned["ensure"].assert_not_called()


def test_a_hook_firing_before_any_uninstall_call_still_spawns(monkeypatch):
    """Sanity/characterization: an ordinary hook firing for a genuinely
    installed Sonari (nothing being uninstalled) is unaffected -- confirms
    _fire_hook/_installed exercise the real spawn-allowed path, not a fixture
    bug that would make the invariant tests above pass for the wrong reason."""
    _installed()
    ensure = _fire_hook(monkeypatch)
    ensure.assert_called_once()


def test_a_still_running_survivor_keeps_its_warning_and_lockfile_but_loses_the_record(
        tmp_path, capsys):
    """I4 composition: the gate-closing removal is unconditional -- it must
    run even when the stop below FAILS, since a hook could fire during the
    (still-ongoing) survivor's whole remaining lifetime too. That must not
    regress the I4 rule: LOCK_PATH is kept on a still-running outcome so the
    survivor stays reachable for a later `sonari install` (which recreates
    install.json -- the accepted recovery named in task-3-brief.md) rather
    than being permanently and silently orphaned."""
    _installed()
    lock = paths.LOCK_PATH
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text('{"host": "127.0.0.1", "port": 1, "token": "t", "pid": 4242}',
                     encoding="utf-8")

    with mock.patch("sonari.cli._platform", return_value=mock.MagicMock()), \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="still-running"):
        install_cmd.uninstall()

    out = capsys.readouterr().out
    assert "STILL RUNNING" in out
    assert not paths.INSTALL_RECORD_PATH.exists()  # gate signal #1: gone regardless
    assert not paths.APP_DIR.exists()               # gate signal #2: gone regardless
    assert lock.exists()                             # I4: kept so a retry/install resolves it
