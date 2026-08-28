"""R5's two new STATUS fields. Read-only diagnostics; no behaviour change.

`keepalive` on the wire is a STRING (disabled|degraded|running|hold|idle). The
age exists -- _players holds (proc, spawned_at) against a monotonic clock --
but nothing exposed it, so an orphaned player reads as "running" and the row
is green.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md 6.0.
"""
from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType
from sonari.sessions import Identity


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_oldest_player_age_is_none_with_no_players():
    daemon, _, _, _, _ = make_daemon()
    assert daemon.keepalive.oldest_player_age() is None


def test_oldest_player_age_reads_the_oldest_spawn(monkeypatch):
    daemon, _, _, _, _ = make_daemon()
    ka = daemon.keepalive
    now = [1000.0]
    monkeypatch.setattr(ka, "_clock", lambda: now[0])
    with ka._lock:
        ka._players.append((object(), 700.0))
        ka._players.append((object(), 950.0))
    assert ka.oldest_player_age() == 300.0


def test_status_carries_the_keepalive_age_and_per_session_liveness():
    """`live` must TRACK is_live, not merely be a bool. Presence-and-type
    assertions are satisfied by a hard-coded True, and a literal here turns
    Task 13's wedge row RED on a dead session's backlog -- the exact case the
    field exists to exclude. So drive one genuinely dead session through the
    wire: a captured tty whose device node does not exist is `dead` by
    ttyutil.tty_alive, while a session with no identity at all stays fail-open
    `live`.
    """
    daemon, _, _, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("D", cwd="/x/dead")
    sessions.set_identity("D", Identity(term_program="Apple_Terminal",
                                        tty="/dev/ttys-does-not-exist"))
    daemon._stream("D")
    st = daemon.handle_message(_msg(MsgType.STATUS, "A"))
    assert "keepalive_oldest_player_age_s" in st
    assert st["sessions"], st
    live = {s["session"]: s["live"] for s in st["sessions"]}
    assert live == {"A": True, "D": False}, st["sessions"]
