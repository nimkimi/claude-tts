"""Rows 1, 2, 2b, 3: a read gesture on a muted session must deliver the read.

Precedent already cuts this way -- repeat-last re-speaks a whole utterance
through a mute and catch-up reads a whole summary through one. Nav, reread-
options and jump-to-decision are the same gesture class; they were never
enrolled.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md 4.4 M3, table 5.
"""
from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _muted(daemon, sessions, sid, cwd):
    sessions.register(sid, cwd=cwd)
    daemon._stream(sid).stopped = True


def test_jump_decision_speaks_the_ask_on_a_muted_target():
    """THE reproduced S1. Today: nothing at all, while voice_state reads
    'flowing'. Un-mute and the ask is destroyed; the only thing spoken is
    'Resumed.' The ask is never heard, at any point, ever."""
    # foreground MUST be B: on_jump_decision resolves its target from
    # sessions.workspace(), never from msg["session"]. With foreground="A" the
    # handler takes the MISS path and this test proves nothing at all.
    daemon, _, speaker, sessions, _ = make_daemon(foreground="B")
    _muted(daemon, sessions, "B", "/x/bravo")
    daemon._enqueue("B", "permission",
                    "A question needs your answer. - at the terminal.", True)
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.JUMP_DECISION, "B"))
    for _ in range(4):
        daemon._speak_loop_once()
    assert any("needs your answer" in (s or "") for s in speaker.spoken), (
        "the ask was never heard: {0}".format(speaker.spoken)
    )


def test_jump_decision_does_not_claim_the_head_on_a_live_session():
    """The HIT-path claim in on_jump_decision is gated on `st.stopped` --
    on a LIVE (un-muted) session the head item must NOT be marked as a
    control cue, or the un-muted path stops being byte-identical to today
    (a different held-branch precedence and pop path). Nothing in
    test_jump_decision_miss.py's un-muted HIT test checks this flag."""
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="B")
    daemon._enqueue("B", "permission",
                    "A question needs your answer. - at the terminal.", True)
    assert not daemon._stream("B").stopped
    daemon.handle_message(_msg(MsgType.JUMP_DECISION, "B"))
    head = queue._items[0]
    assert head.is_decision
    assert not head.control_cue, (
        "on_jump_decision claimed the head item as a control cue on a LIVE "
        "session -- the claim must be gated on stopped"
    )


def test_reread_options_answers_on_a_muted_session_with_stored_options():
    """`on_reread_options`'s TEXT branch (options present) -- distinct from
    the fallback branch pinned below, which fires only when `st.options` is
    unset. `daemon._stream(session).options = ...` is the exact seam
    on_reread_options reads (decisions.py:256); the other three write sites
    write it identically."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="B")
    _muted(daemon, sessions, "B", "/x/bravo")
    daemon._stream("B").options = "1) yes  2) no"
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.REREAD_OPTIONS, "B"))
    for _ in range(3):
        daemon._speak_loop_once()
    assert any("1) yes" in (s or "") for s in speaker.spoken), (
        "ctrl-cmd-O on a muted session with stored options said nothing: {0}"
        .format(speaker.spoken)
    )


def test_reread_options_answers_on_a_muted_session():
    daemon, _, speaker, sessions, _ = make_daemon(foreground="B")
    _muted(daemon, sessions, "B", "/x/bravo")
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.REREAD_OPTIONS, "B"))
    for _ in range(3):
        daemon._speak_loop_once()
    assert speaker.spoken, "ctrl-cmd-O on a muted session said nothing"


def test_nav_answers_and_reads_on_a_muted_session():
    daemon, _, speaker, sessions, _ = make_daemon(foreground="B")
    _muted(daemon, sessions, "B", "/x/bravo")
    # start_turn is the ONLY thing that bumps the turn id -- without it both
    # messages land in one turn, _nav_response takes its `len(turns) < 2`
    # early return, and the test exercises the boundary cue instead of the
    # seek-and-play content this task actually changes.
    daemon.history.record("B", "prose", "the older response")
    daemon.history.end_message("B")
    daemon.history.start_turn("B")
    daemon.history.record("B", "prose", "the newer response")
    daemon.history.end_message("B")
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.NAV, "B", to="prev_response"))
    for _ in range(5):
        daemon._speak_loop_once()
    # `speaker.spoken` truthy alone is satisfied by the orientation cue
    # ("Oldest response.") ALONE -- that cue already became control_cue=True
    # in Task 5, so it is heard through the mute even before this task's fix.
    # Assert the actual seek-and-play CONTENT, which is what this task
    # enrolls; a weaker check here would pass unchanged and prove nothing
    # about the fix this test exists to pin.
    assert any("the older response" in (s or "") for s in speaker.spoken), (
        "nav on a muted session read the cue but not the content: {0}".format(
            speaker.spoken
        )
    )


def test_nav_message_step_reads_on_a_muted_session():
    """Pins `_nav`'s OWN seek-and-play loop (~51) -- distinct from
    `_nav_response`'s (pinned above). to='prev'/'next' steps the message
    cursor within the anchored response rather than jumping a whole response,
    and the brief enrolls it separately; nothing above exercises this path,
    so it was unpinned and unproven without this test."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="B")
    _muted(daemon, sessions, "B", "/x/bravo")
    daemon.history.record("B", "prose", "the first message")
    daemon.history.end_message("B")
    daemon.history.record("B", "prose", "the second message")
    daemon.history.end_message("B")
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.NAV, "B", to="prev"))
    for _ in range(5):
        daemon._speak_loop_once()
    assert any("the first message" in (s or "") for s in speaker.spoken), (
        "nav (message-step) on a muted session said nothing at all: {0}".format(
            speaker.spoken
        )
    )


def test_nav_empty_history_cue_answers_on_a_muted_session():
    """Pins `_nav`'s empty-history cue (~27) -- distinct from the seek-and-
    play loop above, which never runs when there is nothing to navigate.
    No prose/history recorded at all, so `ids` is empty and the handler
    takes the early-return branch."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="B")
    _muted(daemon, sessions, "B", "/x/bravo")
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.NAV, "B", to="prev"))
    for _ in range(3):
        daemon._speak_loop_once()
    assert any("Nothing to navigate yet." in (s or "") for s in speaker.spoken), (
        "nav's empty-history cue on a muted session said nothing: {0}".format(
            speaker.spoken
        )
    )


def test_a_decision_announcement_stays_silent_on_a_muted_session():
    """REGRESSION PIN, must pass before and after. A decision ARRIVING on a
    muted session is narration, not a gesture answer -- staying silent is what
    the mute is for."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="B")
    _muted(daemon, sessions, "B", "/x/bravo")
    speaker.spoken.clear()
    daemon._enqueue("B", "permission", "A question needs your answer.", True)
    for _ in range(3):
        daemon._speak_loop_once()
    assert speaker.spoken == [], (
        "a decision arriving on a muted session broke the mute"
    )
