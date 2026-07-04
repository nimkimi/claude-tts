import sonari.ttyutil as ttyutil
from sonari.protocol import MsgType
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _liveness(monkeypatch, dead):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff its tty not in `dead`."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _ident(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


# --- 1. cycle skips a dead-tty phantom and lands on the next LIVE session ---
def test_cycle_skips_dead_tty_phantom_lands_on_next_live(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")   # phantom
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert sessions.speaker() == "C"          # roster [A,C]; A(0) -> C, phantom B skipped


# --- 2. R7: a MUTED (stopped) session with a LIVE tty stays cycle-reachable ---
def test_cycle_keeps_muted_but_live_session_reachable(monkeypatch):
    _liveness(monkeypatch, dead=set())
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    daemon._stream("B").stopped = True        # muted, but its terminal is open
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert sessions.workspace() == "B"        # muted-live stays reachable (not filtered)


# --- 3. muted + dead tty -> filtered (muted-live vs muted-dead distinguished) ---
def test_cycle_filters_muted_and_dead_session(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    daemon._stream("B").stopped = True        # muted AND terminal closed
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert sessions.speaker() == "C"          # muted+dead B filtered; landed on live C


# --- 4. empty-tty session -> NOT filtered (fail-open) ---
def test_cycle_does_not_filter_empty_tty_session(monkeypatch):
    _liveness(monkeypatch, dead=set())
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "")   # empty tty
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert sessions.workspace() == "B"        # empty tty fail-open -> stays reachable


# --- 5. anchor-is-the-phantom: workspace anchor is dead -> cycle still lands on a live
#        session (needs >=2 LIVE besides the phantom, else <2 -> error, see test 6) ---
def test_cycle_when_anchor_is_phantom_lands_on_live(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysA"})   # the anchor A itself is dead
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    # A filtered out of the roster -> cur falls back to 0 over [B,C]; next -> C. Never A.
    assert sessions.speaker() == "C"
    assert sessions.workspace() != "A"


# --- 6. 1 live + 1 phantom -> filtered roster has <2 -> error tone (no phantom landing) ---
def test_cycle_one_live_one_phantom_plays_error_tone(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert speaker.earcons == ["error"]       # <2 live -> error, phantom never satisfies >=2


# --- 7. ⌃⌘J waiting-target skips a phantom that has a backlog, jumps to the live one ---
def test_jump_waiting_skips_phantom_backlog(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    daemon._enqueue("B", "prose", "b backlog", False)     # phantom WITH backlog
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    daemon._enqueue("C", "prose", "c backlog", False)
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))
    assert sessions.speaker() == "C"          # phantom B skipped; jumped to live C
