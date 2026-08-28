"""Rows 4, 5, 8: a confirmation is the answer to a press and must be heard.

Row 5 is the whole disease in four lines: ctrl-cmd-= and ctrl-cmd-V go through
the SAME helper. One caller passes the flags, the other does not. The rate
nudge is silent; the verbosity nudge speaks.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md table 5.
"""
from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_a_rate_nudge_reads_back_while_muted():
    """Today the rate changes to 225 and is persisted, and he is not told."""
    daemon, _, speaker, sessions, config = make_daemon(foreground="B")
    sessions.register("B", cwd="/x/bravo")
    daemon._stream("B").stopped = True
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.SET_RATE, "B", delta=25))
    for _ in range(3):
        daemon._speak_loop_once()
    assert any("Rate" in (s or "") for s in speaker.spoken), (
        "the rate changed silently: {0}".format(speaker.spoken)
    )


def test_a_verbosity_nudge_still_reads_back_while_muted():
    """The in-run control. It spoke before and must still speak."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="B")
    sessions.register("B", cwd="/x/bravo")
    daemon._stream("B").stopped = True
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.CYCLE_VERBOSITY, "B"))
    for _ in range(3):
        daemon._speak_loop_once()
    assert any("Verbosity" in (s or "") for s in speaker.spoken)


def test_chooser_commit_onto_a_muted_target_announces_the_landing():
    """Row 4 / probe P1. Fork-2 is untouched: the workspace stays on the muted
    target, the voice is still released, the target is still not un-muted --
    but the landing is now audible."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("C", cwd="/x/charlie")
    daemon._stream("A").stopped = True
    daemon._enqueue("C", "prose", "c active", False)
    daemon.handle_message(_msg(MsgType.CHOOSER_STEP, "B", direction="next"))
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, "B"))
    for _ in range(4):
        daemon._speak_loop_once()
    # Assert the LANDING text itself ("alpha."), not just that something was
    # spoken -- C's own already-queued "c active" is picked up by keep-going
    # once the voice is released (Fork-2), and a bare `assert speaker.spoken`
    # is satisfied by that leak even when the landing cue is silenced.
    assert any("alpha" in (s or "") for s in speaker.spoken), (
        "the chooser landed on a muted session in silence: {0}".format(speaker.spoken)
    )
    assert daemon._stream("A").stopped is True, "Fork-2 was violated"


def test_a_failed_raise_is_announced_on_a_muted_session():
    """Row 8. Today he is left believing the terminal came forward, and types
    into the wrong window."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    daemon._stream("B").stopped = True
    speaker.spoken.clear()
    daemon._raise_failed("B", "bravo")
    for _ in range(3):
        daemon._speak_loop_once()
    assert any("forward to type" in (s or "") for s in speaker.spoken)
