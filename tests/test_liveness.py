"""D3 spec §2: liveness() is the ONE three-state composition
('live' | 'pending' | 'dead'); is_live() is its derived binary. This suite
pins the truth table so no consumer can drift to a second predicate."""
from __future__ import annotations

import pytest

from sonari import ttyutil
from sonari.protocol import MsgType, PROTOCOL_VERSION
from sonari.sessions import Identity, SessionManager
from tests.daemon_helpers import make_daemon


def _liveness(monkeypatch, dead):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff not in `dead`."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def test_liveness_three_state_truth_table(monkeypatch):
    _liveness(monkeypatch, dead=set())

    # restored id -> "pending"
    sessions = SessionManager()
    sessions.load_state({"s1": {"folder": "alpha", "number": 1}})
    assert sessions.liveness("s1") == "pending"

    # tty-evicted id -> "dead" (driven via the real eviction loop: two
    # set_identity calls claiming one tty)
    sessions = SessionManager()
    sessions.register("a")
    sessions.register("b")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sessions.set_identity("b", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    assert sessions.liveness("a") == "dead"

    # registered id whose captured NON-EMPTY tty has tty_alive False -> "dead"
    sessions = SessionManager()
    sessions.register("c")
    sessions.set_identity("c", Identity(term_program="Apple_Terminal", tty="/dev/ttys002"))
    _liveness(monkeypatch, dead={"/dev/ttys002"})
    assert sessions.liveness("c") == "dead"

    # registered id, empty/unknown identity -> "live" (fail-open unchanged)
    _liveness(monkeypatch, dead=set())
    sessions = SessionManager()
    sessions.register("d")
    assert sessions.liveness("d") == "live"

    # registered id, non-empty tty, node exists -> "live"
    sessions = SessionManager()
    sessions.register("e")
    sessions.set_identity("e", Identity(term_program="Apple_Terminal", tty="/dev/ttys003"))
    assert sessions.liveness("e") == "live"

    # after unregister() of a restored id -> NOT "pending" (the quarantine
    # bit must not leak past unregistration)
    sessions = SessionManager()
    sessions.load_state({"s1": {"folder": "alpha", "number": 1}})
    sessions.unregister("s1")
    assert sessions.liveness("s1") != "pending"


def test_is_live_is_the_derived_binary(monkeypatch):
    _liveness(monkeypatch, dead=set())

    # restored id
    sessions = SessionManager()
    sessions.load_state({"s1": {"folder": "alpha", "number": 1}})
    assert sessions.is_live("s1") == (sessions.liveness("s1") == "live")

    # tty-evicted id
    sessions = SessionManager()
    sessions.register("a")
    sessions.register("b")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sessions.set_identity("b", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    assert sessions.is_live("a") == (sessions.liveness("a") == "live")

    # registered id whose captured NON-EMPTY tty has tty_alive False
    sessions = SessionManager()
    sessions.register("c")
    sessions.set_identity("c", Identity(term_program="Apple_Terminal", tty="/dev/ttys002"))
    _liveness(monkeypatch, dead={"/dev/ttys002"})
    assert sessions.is_live("c") == (sessions.liveness("c") == "live")

    # registered id, empty/unknown identity
    _liveness(monkeypatch, dead=set())
    sessions = SessionManager()
    sessions.register("d")
    assert sessions.is_live("d") == (sessions.liveness("d") == "live")

    # registered id, non-empty tty, node exists
    sessions = SessionManager()
    sessions.register("e")
    sessions.set_identity("e", Identity(term_program="Apple_Terminal", tty="/dev/ttys003"))
    assert sessions.is_live("e") == (sessions.liveness("e") == "live")

    # after unregister() of a restored id
    sessions = SessionManager()
    sessions.load_state({"s1": {"folder": "alpha", "number": 1}})
    sessions.unregister("s1")
    assert sessions.is_live("s1") == (sessions.liveness("s1") == "live")


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# Every session-authored MsgType (D3 spec R1): a message FROM a quarantined
# session is proof its hook is talking to the daemon, so dispatch must clear
# quarantine regardless of which handler ultimately runs. Minimal-but-valid
# payloads, cribbed from each type's own handler tests.
_SESSION_AUTHORED = [
    (MsgType.PROSE, {"index": 0, "final": True, "delta": "hello"}),
    (MsgType.TOOL, {"tool": "Bash", "summary": "ls"}),
    (MsgType.CHOICE, {"questions": [{"question": "Q?"}]}),
    (MsgType.PLAN, {"text": "Do X."}),
    (MsgType.PERMISSION, {"action": "rm -rf"}),
    (MsgType.PERMISSION_REQUEST, {"tool": "Bash", "summary": "ls"}),
    (MsgType.EARCON, {"kind": ""}),
    (MsgType.SET_FOREGROUND, {"cwd": "/x/repo"}),
    (MsgType.FLUSH, {}),
]


def test_prose_from_quarantined_session_clears_quarantine():
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"s1": {"folder": "repo", "number": 1}})
    with daemon._state.transaction():
        daemon.handle_message(_msg(MsgType.PROSE, "s1", index=0, final=True, delta="hello"))
    assert daemon.sessions.is_provisional("s1") is False
    assert daemon.sessions.liveness("s1") == "live"   # identity None -> fail-open


@pytest.mark.parametrize("mtype,kwargs", _SESSION_AUTHORED)
def test_session_authored_message_clears_quarantine(mtype, kwargs):
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"s1": {"folder": "repo", "number": 1}})
    with daemon._state.transaction():
        daemon.handle_message(_msg(mtype, "s1", **kwargs))
    assert daemon.sessions.is_provisional("s1") is False


def test_witness_ping_never_clears_quarantine():
    # The real hotkeyd ping is session-less; a fake "session" field here proves
    # the type exemption itself (not just the absence of a session key).
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"s1": {"folder": "repo", "number": 1}})
    with daemon._state.transaction():
        daemon.handle_message(_msg(MsgType.WITNESS_PING, "s1"))
    assert daemon.sessions.is_provisional("s1") is True


def test_other_session_traffic_never_clears_a_silent_quarantine():
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"s1": {"folder": "repo", "number": 1},
                                "s2": {"folder": "other", "number": 2}})
    for _ in range(3):
        with daemon._state.transaction():
            daemon.handle_message(_msg(MsgType.PROSE, "s2", index=0, final=True, delta="hi"))
    assert daemon.sessions.liveness("s1") == "pending"
