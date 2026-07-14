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


def _browse(daemon):
    daemon.handle_message(_msg(MsgType.CHOOSER_STEP, "", direction="next"))


def _commit(daemon):
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))


# --- 1. the chooser skips a dead-tty phantom and lands on the next LIVE session ---
def test_chooser_skips_dead_tty_phantom_lands_on_next_live(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")   # phantom
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    _browse(daemon); _commit(daemon)
    assert sessions.speaker() == "C"          # candidates [A,C]; step -> C, phantom skipped


# --- 2. R7: a MUTED (stopped) session with a LIVE tty stays chooser-reachable ---
def test_chooser_keeps_muted_but_live_session_reachable(monkeypatch):
    _liveness(monkeypatch, dead=set())
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    daemon._stream("B").stopped = True        # muted, but its terminal is open
    _browse(daemon); _commit(daemon)
    assert sessions.workspace() == "B"        # muted-live stays reachable (not filtered)


# --- 3. muted + dead tty -> filtered (muted-live vs muted-dead distinguished) ---
def test_chooser_filters_muted_and_dead_session(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    daemon._stream("B").stopped = True        # muted AND terminal closed
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    _browse(daemon); _commit(daemon)
    assert sessions.speaker() == "C"          # muted+dead B filtered; landed on live C


# --- 4. empty-tty session -> NOT filtered (fail-open) ---
def test_chooser_does_not_filter_empty_tty_session(monkeypatch):
    _liveness(monkeypatch, dead=set())
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "")   # empty tty
    _browse(daemon); _commit(daemon)
    assert sessions.workspace() == "B"        # empty tty fail-open -> stays reachable


# --- 5. anchor-is-the-phantom: the dead origin is excluded; browsing lands live ---
def test_chooser_when_anchor_is_phantom_lands_on_live(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysA"})   # the origin A itself is dead
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    _browse(daemon)
    assert daemon._chooser.candidates == ["B", "C"]   # A excluded entirely
    _commit(daemon)
    assert sessions.speaker() == "C"          # index 1 of [B, C]; never A
    assert sessions.workspace() != "A"


# --- 6. 1 live + 1 phantom -> the phantom can never land (D6: degenerate browse) ---
def test_chooser_one_live_one_phantom_never_lands_the_phantom(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    _browse(daemon)
    assert daemon._chooser.candidates == ["A"]    # the phantom is not even browsable
    _commit(daemon)
    assert sessions.foreground() == "A"           # no-op landing; B never satisfied


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
