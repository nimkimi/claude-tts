"""SET_KEEPALIVE: live toggle without a daemon restart, persisted to config.

Amendment (2026-08-24, binding): the handler runs under the daemon lock and
must NEVER call the manager — set_enabled(False) reaps on the calling thread,
and a handler-side reap would stall every socket message, hotkey and
speak-loop claim behind it on a hung child. The handler ONLY mutates config
and persists it; application happens at the lock-free speak-loop site,
inside `_keepalive_recheck`'s `if reap:` branch. Tests below therefore split
each toggle into two steps: handle_message (config + persistence only, and
the manager must be provably untouched) then a driven
`daemon._keepalive_recheck(reap=True)` (the manager effect)."""
from unittest import mock

from sonari.config import DEFAULTS
from sonari.protocol import PROTOCOL_VERSION, MsgType
from tests.daemon_helpers import make_daemon
from tests.test_keepalive_manager import FakeProc, FakeTimer


def _msg(t, session="s1", **kw):
    m = {"v": PROTOCOL_VERSION, "type": t, "session": session}
    m.update(kw)
    return m


def _seam(daemon):
    FakeTimer.instances = []
    spawned = []
    daemon.keepalive._popen = lambda cmd: spawned.append(FakeProc(cmd)) or spawned[-1]
    daemon.keepalive._timer_factory = FakeTimer
    return spawned


def test_default_is_enabled():
    assert DEFAULTS["keepalive_enabled"] is True


def test_set_keepalive_off_mutates_and_persists_but_does_not_terminate():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SESSION_START))
    assert len(spawned) == 1
    with mock.patch("sonari.daemon.features.control.save_config") as save:
        daemon.handle_message(_msg(MsgType.SET_KEEPALIVE, enabled=False))
    save.assert_called_once_with(config)
    assert config["keepalive_enabled"] is False
    # Single-writer discipline: the handler alone must NOT reach the manager.
    assert not spawned[0].terminated
    assert daemon.keepalive.status() == "running"
    # Application happens only at the lock-free speak-loop tick.
    daemon._keepalive_recheck(reap=True)
    assert spawned[0].terminated
    assert daemon.keepalive.status() == "disabled"


def test_set_keepalive_on_reapplies_policy_only_at_the_next_tick():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SET_KEEPALIVE, enabled=False))
    daemon._keepalive_recheck(reap=True)           # apply the disable
    daemon.handle_message(_msg(MsgType.SESSION_START))
    assert spawned == []
    assert daemon.keepalive.status() == "disabled"
    daemon.handle_message(_msg(MsgType.SET_KEEPALIVE, enabled=True))
    # Handler alone: config flips, manager does not — no restart needed to prove it.
    assert config["keepalive_enabled"] is True
    assert spawned == []
    assert daemon.keepalive.status() == "disabled"
    daemon._keepalive_recheck(reap=True)
    assert len(spawned) == 1                       # no new SESSION_START needed


def test_non_bool_payload_is_ignored():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    with mock.patch("sonari.daemon.features.control.save_config") as save:
        daemon.handle_message(_msg(MsgType.SET_KEEPALIVE, enabled="maybe"))
    save.assert_not_called()
    assert config["keepalive_enabled"] is True


def test_handler_never_calls_the_manager():
    """Direct proof of the amendment's core invariant: make every manager
    mutator explode, then drive the handler through both directions. If the
    handler reaches the manager at all, the test fails loudly instead of
    relying on indirect state checks."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)

    def _boom(*a, **kw):
        raise AssertionError("handler must not call the keepalive manager")

    daemon.keepalive.set_enabled = _boom
    daemon.keepalive.set_active = _boom
    daemon.keepalive.tick = _boom
    daemon.handle_message(_msg(MsgType.SET_KEEPALIVE, enabled=False))
    daemon.handle_message(_msg(MsgType.SET_KEEPALIVE, enabled=True))
    daemon.handle_message(_msg(MsgType.SET_KEEPALIVE, enabled="maybe"))
