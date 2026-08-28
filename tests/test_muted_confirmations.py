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
    assert any("Rate 225." in (s or "") for s in speaker.spoken), (
        "the rate changed silently: {0}".format(speaker.spoken)
    )
    assert config["rate"] == 225, "the docstring's own claim of persistence, unchecked"


def test_a_verbosity_nudge_still_reads_back_while_muted():
    """The in-run control. It spoke before and must still speak."""
    daemon, _, speaker, sessions, config = make_daemon(foreground="B")
    sessions.register("B", cwd="/x/bravo")
    daemon._stream("B").stopped = True
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.CYCLE_VERBOSITY, "B"))
    for _ in range(3):
        daemon._speak_loop_once()
    assert any("Verbosity medium." in (s or "") for s in speaker.spoken)
    assert config["verbosity"] == "medium"


def test_a_rate_readback_on_a_live_workspace_stays_unprefixed_and_out_of_repeat():
    """LIVE-path pin (fix round 1). `control_cue` is overloaded in host.py:
    besides "audible through a mute" it ALSO means "chrome, exclude from
    _last_utterance / cross-session prefix" (host.py:1564 and :669). Row 5's
    hoist makes control_cue=True unconditional for every _readback, so on a
    LIVE (un-muted) workspace this now also means: no cross-session folder
    prefix, and the readback must NOT clobber _last_utterance -- exactly how
    the sibling verbosity readback already behaved at BASE (both are
    settings chrome, not content he asked to hear repeated on ctrl-cmd-R).
    _last_spoken_session is primed to a DIFFERENT session ("A") so the
    prefix branch is live; if it fired, "bravo." would prepend."""
    daemon, _, speaker, sessions, config = make_daemon(foreground="B")
    sessions.register("B", cwd="/x/bravo")
    daemon._last_spoken_session = "A"
    daemon._last_utterance = ("alpha content", None)
    assert not daemon._stream("B").stopped
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.SET_RATE, "B", delta=25))
    for _ in range(3):
        daemon._speak_loop_once()
    assert any(s == "Rate 225." for s in speaker.spoken), (
        "the rate readback should be unprefixed chrome: {0}".format(speaker.spoken)
    )
    assert not any("bravo." in (s or "") for s in speaker.spoken), (
        "the rate readback picked up a cross-session folder prefix it should "
        "not have: {0}".format(speaker.spoken)
    )
    assert daemon._last_utterance == ("alpha content", None), (
        "the rate readback clobbered _last_utterance / ctrl-cmd-R capture: {0}".format(
            daemon._last_utterance
        )
    )


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
