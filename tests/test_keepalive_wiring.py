"""Keep-alive policy wiring: SESSION_START spawns, pending-only roster does not,
SESSION_END arms the hold, the tick notices ghosts, shutdown terminates.
Uses make_daemon + the manager's injectable seams."""
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

    def popen(cmd):
        proc = FakeProc(cmd)
        spawned.append(proc)
        return proc

    daemon.keepalive._popen = popen
    daemon.keepalive._timer_factory = FakeTimer
    return spawned


def test_session_start_with_live_session_spawns_player():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SESSION_START))
    assert len(spawned) == 1
    assert daemon.keepalive.status() == "running"


def test_restored_pending_only_roster_does_not_spawn():
    # THE load-bearing policy test: registration alone is not liveness.
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    # load_state's real shape (sessions.py §4.4) is id -> {"folder", "number"},
    # not id -> folder: a restored id lands in _provisional => "pending".
    sessions.load_state({"ghost": {"folder": "folder", "number": 1}})
    daemon._keepalive_recheck()
    assert spawned == []
    assert daemon.keepalive.status() == "idle"


def test_session_end_arms_hold_not_immediate_stop():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SESSION_START))
    daemon.handle_message(_msg(MsgType.SESSION_END))
    assert not spawned[0].terminated
    assert daemon.keepalive.status() == "hold"


def test_tick_notices_dead_tty_ghost(monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SESSION_START))
    assert daemon.keepalive.status() == "running"
    # The session's tty dies with no SESSION_END: liveness flips lazily.
    monkeypatch.setattr("sonari.sessions.SessionManager.is_live",
                        lambda self, s: False)
    daemon._keepalive_recheck()
    assert daemon.keepalive.status() == "hold"    # event never came; tick caught it


def test_keepalive_disabled_by_config_never_spawns():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    config["keepalive_enabled"] = False
    daemon.keepalive.set_enabled(False)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SESSION_START))
    assert spawned == []
    assert daemon.keepalive.status() == "disabled"


def test_recheck_never_raises_into_the_speak_loop(monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    monkeypatch.setattr("sonari.sessions.SessionManager.session_ids",
                        lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    daemon._keepalive_recheck()                   # must swallow, not raise
