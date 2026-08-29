import importlib
import os
import time
from unittest import mock

from sonari import client as client_mod

# NOTE: deliberately not `from sonari.paths import DAEMON_FAIL_MEMO_PATH` at
# module scope -- that binds the pre-monkeypatch real ~/.sonari path once at
# collection time (the same by-value-import trap tests/conftest.py's isolation
# fixture works around for LOCK_PATH etc.). Read client_mod.DAEMON_FAIL_MEMO_PATH
# live inside each test instead, so it resolves to that test's tmp_path.


def test_polling_backs_off_rather_than_hammering_at_50ms():
    client_mod.reset_failure_memo()
    sleeps = []
    with mock.patch.object(client_mod, "_connectable", return_value=False), \
         mock.patch.object(client_mod, "ensure_running"), \
         mock.patch("time.sleep", side_effect=lambda s: sleeps.append(s)):
        client_mod.ensure_daemon(timeout=1.0)
    assert len(sleeps) >= 2
    assert sleeps == sorted(sleeps), f"intervals did not grow: {sleeps}"
    assert sleeps[-1] > sleeps[0]


def test_a_recent_failure_short_circuits_the_next_call_across_a_fresh_process():
    """bin/sonari-hook fires as a brand-new OS process per hook event (every
    entry in hooks/hooks.json is `type: command`), so no Python object can
    survive between real ensure_daemon() calls -- only the filesystem can.
    Reloading the client module between calls simulates that process boundary:
    it resets any module-level state to its import-time value, so this test
    only passes if the memo lives on disk. This is the exact gap that let the
    original in-process-global design's tests go green over a fix that was a
    no-op in production (every real call happens in its own process).
    """
    client_mod.reset_failure_memo()
    with mock.patch.object(client_mod, "_connectable", return_value=False), \
         mock.patch.object(client_mod, "ensure_running") as spawn, \
         mock.patch("time.sleep"):
        client_mod.ensure_daemon(timeout=1.0)
    assert spawn.call_count == 1

    importlib.reload(client_mod)   # simulate the next hook event's fresh process

    with mock.patch.object(client_mod, "_connectable", return_value=False), \
         mock.patch.object(client_mod, "ensure_running") as spawn2, \
         mock.patch("time.sleep"):
        client_mod.ensure_daemon(timeout=1.0)   # memo still hot on disk
    assert spawn2.call_count == 0, "respawned despite a fresh on-disk failure memo"


def test_the_memo_expires_so_recovery_is_still_possible():
    client_mod.reset_failure_memo()
    with mock.patch.object(client_mod, "_connectable", return_value=False), \
         mock.patch.object(client_mod, "ensure_running") as spawn, \
         mock.patch("time.sleep"):
        client_mod.ensure_daemon(timeout=1.0)
    assert spawn.call_count == 1

    # Age the on-disk memo past its TTL, then simulate the next hook event's
    # fresh process picking it up.
    stale = time.time() - client_mod._MEMO_S - 1
    os.utime(client_mod.DAEMON_FAIL_MEMO_PATH, (stale, stale))
    importlib.reload(client_mod)

    with mock.patch.object(client_mod, "_connectable", return_value=False), \
         mock.patch.object(client_mod, "ensure_running") as spawn2, \
         mock.patch("time.sleep"):
        client_mod.ensure_daemon(timeout=1.0)
    assert spawn2.call_count == 1, "an expired memo blocked recovery"


def test_success_clears_the_memo():
    client_mod.reset_failure_memo()
    with mock.patch.object(client_mod, "_connectable", return_value=False), \
         mock.patch.object(client_mod, "ensure_running"), \
         mock.patch("time.sleep"):
        client_mod.ensure_daemon(timeout=1.0)
    assert client_mod.DAEMON_FAIL_MEMO_PATH.exists(), "setup: a failure should leave a memo"

    with mock.patch.object(client_mod, "_connectable", return_value=True):
        client_mod.ensure_daemon(timeout=1.0)
    assert not client_mod.DAEMON_FAIL_MEMO_PATH.exists(), "the memo must clear on the next success"


def test_a_memo_write_failure_never_raises():
    """This runs inside a Claude Code hook (bin/sonari-hook); an exception here
    breaks the user's whole Claude Code session, not just Sonari. Point the memo
    at a path whose parent is a plain FILE (not a directory) so mkdir/touch hit a
    real OSError, and confirm ensure_daemon() still returns normally."""
    blocked = client_mod.DAEMON_FAIL_MEMO_PATH.parent.parent / "blocker_file"
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_text("not a directory")
    bogus_memo_path = blocked / "daemon.fail_memo"

    client_mod.reset_failure_memo()
    with mock.patch.object(client_mod, "DAEMON_FAIL_MEMO_PATH", bogus_memo_path), \
         mock.patch.object(client_mod, "_connectable", return_value=False), \
         mock.patch.object(client_mod, "ensure_running") as spawn, \
         mock.patch("time.sleep"):
        client_mod.ensure_daemon(timeout=1.0)   # must not raise
    assert spawn.call_count == 1   # the OSError-prone memo path was actually reached
