import threading

from sonari.protocol import MsgType
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    d = {"v": 1, "type": t, "session": session}
    d.update(kw)
    return d


class RecordingRaiseService:
    """Stands in for RaiseService; records calls, lets the test drive results."""
    def __init__(self, will=True):
        self._will = will
        self._gen = 0
        self.attempts = []        # (identity, generation)
        self.last_on_failure = None

    def will_attempt(self, identity):
        return self._will and identity is not None

    def bump_generation(self):
        self._gen += 1
        return self._gen

    def raise_async(self, identity, generation, on_failure=None):
        self.attempts.append((identity, generation))
        self.last_on_failure = on_failure


def _ident():
    return Identity(term_program="Apple_Terminal", tty="/dev/ttys9")


def test_session_start_stores_identity():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon._setup_health = lambda v: ("ok", None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x",
                               term_program="Apple_Terminal", tty="/dev/ttys9",
                               iterm_session_id=""))
    ident = sessions.identity("s1")
    assert ident is not None and ident.tty == "/dev/ttys9"


def test_jump_attempts_raise_with_target_identity():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("b", cwd="/work/backend")
    sessions.set_identity("b", _ident())
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    daemon.handle_message(_msg(MsgType.PROSE, "b", delta="hi. ", index=0, final=True))
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "b"
    assert len(rs.attempts) == 1
    ident, gen = rs.attempts[0]
    assert ident.tty == "/dev/ttys9" and gen >= 1
    # preamble unchanged when a raise will be attempted
    assert stream_queue(daemon, "b")._items[0].text == "Jumping to backend."


def test_jump_adds_cue_when_no_raise_will_happen():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("b", cwd="/work/backend")  # no identity set
    rs = RecordingRaiseService(will=False)
    daemon.raise_service = rs
    daemon.handle_message(_msg(MsgType.PROSE, "b", delta="hi. ", index=0, final=True))
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert rs.attempts == []
    assert stream_queue(daemon, "b")._items[0].text == \
        "Jumping to backend. Bring it forward to type."


def test_non_raising_jump_still_bumps_generation_so_it_supersedes():
    # FIX 1: the generation must advance on EVERY jump, not only raising ones.
    # Trace a double-jump A->B where B is non-followable (no identity). If the bump
    # lives inside `if will_raise:`, B does NOT advance the generation, so an
    # in-flight raise(A) tagged with A's generation stays "current" and wrongly
    # yanks focus back to A while the voice is on B. The bump must run on every jump.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("a", cwd="/work/alpha")
    sessions.set_identity("a", _ident())          # A is followable -> will raise
    sessions.register("b", cwd="/work/bravo")     # B has NO identity -> non-followable
    rs = RecordingRaiseService(will=True)          # will=True, but will_attempt gates on identity
    daemon.raise_service = rs

    # Jump 1: foreground excludes "fg", A has backlog -> target A (raising).
    daemon.handle_message(_msg(MsgType.PROSE, "a", delta="ay. ", index=0, final=True))
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "fg"))
    assert sessions.foreground() == "a"

    # Jump 2: now fg=A is excluded, B has backlog -> target B (NON-raising).
    daemon.handle_message(_msg(MsgType.PROSE, "b", delta="be. ", index=0, final=True))
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "b"

    # The load-bearing assertion (mutation-meaningful): B's non-raising jump bumped
    # the generation, so the counter advanced TWICE. If the bump is moved back inside
    # `if will_raise:`, B does not bump and this is 1 -> the test FAILS.
    assert rs._gen == 2
    # raise_async fired exactly once (only for A), tagged with A's now-STALE gen 1.
    assert len(rs.attempts) == 1
    ident, gen = rs.attempts[0]
    assert ident.tty == "/dev/ttys9" and gen == 1


def test_raise_failure_callback_enqueues_cue():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("b", cwd="/work/backend")
    sessions.set_identity("b", _ident())
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    daemon.handle_message(_msg(MsgType.PROSE, "b", delta="hi. ", index=0, final=True))
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    # simulate the async raise reporting failure
    assert rs.last_on_failure is not None
    rs.last_on_failure()
    texts = [it.text for it in stream_queue(daemon, "b")._items]
    assert "Bring backend forward to type." in texts
