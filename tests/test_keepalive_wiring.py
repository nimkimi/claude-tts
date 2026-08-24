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


def test_dead_tty_ghost_flips_to_hold(monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SESSION_START))
    assert daemon.keepalive.status() == "running"
    # The session's tty dies with no SESSION_END: liveness flips lazily.
    monkeypatch.setattr("sonari.sessions.SessionManager.is_live",
                        lambda self, s: False)
    daemon._keepalive_recheck()
    # The event never came; the recheck's set_active(False) caught it lazily.
    assert daemon.keepalive.status() == "hold"


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


def _direct_daemon(**cfg_overrides):
    """A SpeechDaemon built WITHOUT make_daemon — mirrors test_frontier.py and the
    other 18 direct constructions, which get their keep-alive hermeticity from the
    conftest fixture rather than from make_daemon's explicit seams."""
    from sonari.config import DEFAULTS
    from sonari.daemon import SpeechDaemon
    from sonari.sessions import SessionManager
    from tests.daemon_helpers import FakeSpeaker

    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["summarizer"] = "off"      # SP5: no test may ever reach a real `claude`
    config.update(cfg_overrides)
    daemon = SpeechDaemon(FakeSpeaker(), SessionManager(), config)
    daemon._voices_provider = lambda: []
    return daemon


def test_directly_constructed_daemon_gets_inert_keepalive_seams():
    """The hermeticity guard. 19 tests construct SpeechDaemon directly and reach
    set_active(True); on the DEFAULT seams each launches a real 300 s afplay and
    arms a real 295 s Timer. conftest's _inert_keepalive_seams fixture matches the
    defaults by identity, so a future __init__ refactor could silently stop
    matching — fail by NAME here instead of quietly reverting the whole suite to
    real audio spawns."""
    import subprocess
    import threading

    daemon = _direct_daemon()
    assert daemon.keepalive._popen is not subprocess.Popen
    assert daemon.keepalive._timer_factory is not threading.Timer
    daemon.handle_message(_msg(MsgType.SESSION_START))
    assert daemon.keepalive.status() == "running"
    assert type(daemon.keepalive._players[0][0]).__name__ == "InertKeepaliveProc"


def test_config_disables_keepalive_at_construction():
    """__init__ is the ONE place the config key is read (nothing re-reads it)."""
    daemon = _direct_daemon(keepalive_enabled=False)
    assert daemon.keepalive.status() == "disabled"


def test_lifecycle_handlers_never_reap_only_the_loop_does():
    """THE binding wiring invariant (plan amendment, Task 2 review Important 2):
    tick() can block ~2 s per player reaping a wedged afplay, and the lifecycle
    handlers run under the DAEMON lock — so only the lock-free speak-loop site may
    reap. Pinned by counting calls, because nothing else in the suite would notice
    a handler quietly gaining a tick()."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    ticks = []
    daemon.keepalive.tick = lambda: ticks.append(1)

    daemon.handle_message(_msg(MsgType.SESSION_START))
    daemon.handle_message(_msg(MsgType.SESSION_END))
    assert ticks == []                            # both handlers: set_active only

    daemon._keepalive_recheck(reap=True)          # the speak-loop site
    assert len(ticks) == 1


def test_recheck_logs_the_first_failure_then_stays_silent(monkeypatch, capsys):
    """A silent swallow at 10 Hz is undiagnosable; a traceback at 10 Hz is a log
    flood. Fire once, like _witness_alarmed."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    capsys.readouterr()                           # drop any construction noise
    monkeypatch.setattr("sonari.sessions.SessionManager.session_ids",
                        lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    daemon._keepalive_recheck()
    assert "boom" in capsys.readouterr().err
    daemon._keepalive_recheck()
    daemon._keepalive_recheck()
    assert capsys.readouterr().err == ""
