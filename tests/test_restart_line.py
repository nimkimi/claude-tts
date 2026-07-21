"""D2 §6.4/§6.5: ONE factual restart line — restored piles + restored mutes.
Queue-ordered (it follows the off-queue boot cue, R2 untouched); forward=False
so it can never advance a restored frontier; content-only (no liveness claims);
deferred to first activity when every restored session is muted."""
from sonari.protocol import PROTOCOL_VERSION, MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _restart(src):
    with src._lock:
        src._store.save(src._snapshot_state())
    dst, q, speaker, sessions, config = make_daemon(foreground=None)
    dst._restore_state()
    dst._announce_restored()
    return dst, speaker, sessions


def _seed_pile(daemon, sid):
    """A restored PILE: history extends past the frontier (catch-up-reachable)."""
    daemon.sessions.register(sid, cwd="/x/" + sid)
    daemon.history.record(sid, "prose", "heard")
    daemon.history.end_message(sid)
    daemon._stream(sid).advance_frontier((0, 0))
    daemon.history.record(sid, "prose", "unheard")


def _texts(speaker):
    """Spoken TEXT entries only. Keep-going may bind a prelude to the line
    (Task 12's crossing marker on a cache miss); the FakeSpeaker records a
    prelude play as a None text entry — filter it so these pins survive."""
    return [s for s in speaker.spoken if s]


def test_one_pile_restored_line_speaks_after_restore():
    src, *_ = make_daemon(foreground=None)
    _seed_pile(src, "s1")
    dst, speaker, sessions = _restart(src)
    dst._speak_loop_once()                               # keep-going adopts the line
    assert _texts(speaker) == ["One pile restored."]


def test_two_piles_and_a_muted_session_compose():
    src, *_ = make_daemon(foreground=None)
    _seed_pile(src, "s1")
    _seed_pile(src, "s2")
    src.sessions.register("ws", cwd="/x/ws")
    src._stream("ws").stopped = True
    dst, speaker, sessions = _restart(src)
    dst._speak_loop_once()
    assert _texts(speaker) == ["Two piles restored. ws is muted."]


def test_nothing_restored_stays_silent():
    dst, _q, speaker, sessions, config = make_daemon(foreground=None)
    dst._restore_state()                                 # no state file at all
    dst._announce_restored()
    dst._speak_loop_once()
    assert speaker.spoken == []
    assert dst._restore_line is None


def test_restart_line_never_advances_a_restored_frontier():
    src, *_ = make_daemon(foreground=None)
    _seed_pile(src, "s1")
    dst, speaker, sessions = _restart(src)
    dst._speak_loop_once()
    assert _texts(speaker) == ["One pile restored."]
    assert dst._streams["s1"].frontier == (0, 0)         # content-only: untouched


def test_all_muted_defers_the_line_and_the_submit_delivers_it():
    src, *_ = make_daemon(foreground=None)
    src.sessions.register("ws", cwd="/x/ws")
    src._stream("ws").stopped = True
    dst, speaker, sessions = _restart(src)
    assert dst._restore_line == "ws is muted."           # no playable stream at boot
    dst._speak_loop_once()
    assert speaker.spoken == []
    # First activity: the user submits ON the muted session (the E4b drive) —
    # the real hook pair, SET_FOREGROUND then FLUSH.
    dst.handle_message(_msg(MsgType.SET_FOREGROUND, "ws", cwd="/x/ws"))
    dst.handle_message(_msg(MsgType.FLUSH, "ws"))
    assert dst._restore_line is None
    dst._speak_loop_once()                               # held branch: pause-exempt line
    assert "ws is muted." in speaker.spoken
    assert dst._streams["ws"].stopped is True            # the mute itself held


def test_all_muted_line_also_delivers_on_session_start():
    src, *_ = make_daemon(foreground=None)
    src.sessions.register("ws", cwd="/x/ws")
    src._stream("ws").stopped = True
    dst, speaker, sessions = _restart(src)
    dst.handle_message(_msg(MsgType.SET_FOREGROUND, "n1", cwd="/x/n1"))
    dst.handle_message(_msg(MsgType.SESSION_START, "n1", cwd="/x/n1"))
    assert dst._restore_line is None
    texts = [it.text for it in dst._stream("n1").queue._items]
    assert "ws is muted." in texts
