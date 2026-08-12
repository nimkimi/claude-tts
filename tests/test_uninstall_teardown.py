import inspect
import re
import json
import os
import signal
from unittest import mock

from sonari.cli import teardown


def _lockfile(tmp_path, pid=4242):
    p = tmp_path / "daemon.lock"
    p.write_text(json.dumps({"host": "127.0.0.1", "port": 1, "token": "t",
                             "pid": pid}), encoding="utf-8")
    return p


def test_no_lockfile_means_nothing_to_stop(tmp_path):
    with mock.patch("sonari.paths.LOCK_PATH", tmp_path / "absent.lock"):
        assert teardown.stop_daemon() == "not-running"


def test_sigterm_is_sent_to_the_pid_from_the_lockfile(tmp_path):
    lock = _lockfile(tmp_path, pid=4242)
    with mock.patch("sonari.paths.LOCK_PATH", lock), \
         mock.patch("os.kill") as kill, \
         mock.patch.object(teardown, "_singleton_free", return_value=True):
        assert teardown.stop_daemon() == "stopped"
    kill.assert_called_once_with(4242, signal.SIGTERM)


def test_sigterm_not_sigkill_so_the_shutdown_flush_runs(tmp_path):
    """host.py:1346-1361 turns SIGTERM into SP6's clean flush. SIGKILL would
    truncate the pile the user is about to be asked about."""
    lock = _lockfile(tmp_path)
    with mock.patch("sonari.paths.LOCK_PATH", lock), \
         mock.patch("os.kill") as kill, \
         mock.patch.object(teardown, "_singleton_free", return_value=True):
        teardown.stop_daemon()
    assert signal.SIGKILL not in [c[0][1] for c in kill.call_args_list]


def test_a_survivor_is_reported_not_papered_over(tmp_path):
    lock = _lockfile(tmp_path)
    with mock.patch("sonari.paths.LOCK_PATH", lock), \
         mock.patch("os.kill"), \
         mock.patch.object(teardown, "_singleton_free", return_value=False), \
         mock.patch("time.sleep"):
        assert teardown.stop_daemon(timeout=0.1) == "still-running"


def test_an_already_dead_pid_is_not_an_error(tmp_path):
    lock = _lockfile(tmp_path)
    with mock.patch("sonari.paths.LOCK_PATH", lock), \
         mock.patch("os.kill", side_effect=ProcessLookupError()), \
         mock.patch.object(teardown, "_singleton_free", return_value=True):
        assert teardown.stop_daemon() == "stopped"


def test_a_zero_or_negative_pid_is_never_signalled(tmp_path):
    """A corrupt lockfile could hold pid 0 (a process-group broadcast on
    POSIX) or a negative pid (targets an entire process group). Neither may
    ever reach os.kill."""
    lock = _lockfile(tmp_path, pid=0)
    with mock.patch("sonari.paths.LOCK_PATH", lock), \
         mock.patch("os.kill") as kill, \
         mock.patch.object(teardown, "_singleton_free", return_value=True):
        assert teardown.stop_daemon() == "not-running"
    kill.assert_not_called()


def test_a_pid_too_large_for_the_os_does_not_crash_uninstall(tmp_path):
    """os.kill raises OverflowError (NOT OSError) for a pid that does not fit
    in C's pid_t -- a corrupt lockfile with a huge pid must not crash
    stop_daemon; an impossible pid names no process, same as one already gone."""
    lock = _lockfile(tmp_path, pid=10 ** 20)
    with mock.patch("sonari.paths.LOCK_PATH", lock), \
         mock.patch("os.kill", side_effect=OverflowError()), \
         mock.patch.object(teardown, "_singleton_free", return_value=True):
        assert teardown.stop_daemon() == "stopped"


def test_the_proof_window_outlasts_the_daemons_own_shutdown_budget():
    """Found live: uninstall said "STILL RUNNING" while pgrep showed zero.

    Not a flake — arithmetic. host.py's shutdown burns `speak_thread.join(
    timeout=5.0)` in full whenever the speak thread is inside proc.wait(), and
    the persistence-thread join before it has no timeout at all, so a daemon
    that is MID-UTTERANCE at SIGTERM needs > 5.0 s to exit. A 5.0 s proof
    window always lost that race and told an eyes-free user the opposite of the
    truth about whether his daemon was gone. The proof must outlast the
    shutdown it is proving.
    """
    from sonari.daemon import host as host_mod
    src = inspect.getsource(host_mod.SpeechDaemon.run)
    m = re.search(r"speak_thread\.join\(timeout=([0-9.]+)\)", src)
    assert m, "speak_thread.join(timeout=...) moved — re-derive the budget"
    daemon_budget = float(m.group(1))
    proof_window = inspect.signature(teardown.stop_daemon).parameters["timeout"].default
    assert proof_window > daemon_budget, (
        f"stop_daemon's proof window ({proof_window}s) must exceed the daemon's "
        f"own graceful-shutdown budget ({daemon_budget}s), or a daemon that is "
        f"speaking when SIGTERM lands is reported as still-running after it died")
