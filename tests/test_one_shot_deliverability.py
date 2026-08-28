"""Rows 6 and 7: claim a one-shot on deliverability, not on attempt.

A session created under stop-all is born stopped, its announce marker is
consumed inside the `if`, and the queued items are destroyed at the next
ctrl-cmd-S. Probe P5: claim_announce spent=True, NEWq 2 -> 0, spoken []. Never
heard, ever, for that session's whole life.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md 4.5.

Also carries CARRIED-INPUTS.md Task 10 Input 1 (R21): `announce_resume` is the
same class of one-shot -- armed at SET_FOREGROUND (Policy-A quiet-hold lift)
on an ARM-TIME proof only ("this stream is not stopped right now"), delivered
later at FLUSH/SESSION_START. Nothing previously invalidated the claim if the
SAME stream was stopped again in the arm-to-deliver window, so a stale
"Resumed." could land on a stream he just re-muted -- and because it is a
control_cue, the held branch speaks it THROUGH that very mute. Fixed in
playback.py's two stop paths (on_stop_session's stopping branch, on_stop_all).

And the named ⌃⌘R risk from the dispatch: Step 5 removes control_cue from the
SESSION_START announce, so it newly qualifies for the W12 _last_utterance
capture (host.py:1564) it was excluded from before. Pinned below.
"""
from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_a_session_born_under_stop_all_is_not_announced_at_birth():
    """R7 lasting quiet: he pressed ctrl-cmd-M meaning silence everything, and
    a session opening in that window announcing itself is precisely what he
    asked not to happen."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_ALL, "A"))
    for _ in range(3):
        daemon._speak_loop_once()
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.SESSION_START, "NEW", cwd="/x/new"))
    for _ in range(3):
        daemon._speak_loop_once()
    assert speaker.spoken == [], "a new session broke the lasting quiet"


def test_a_session_born_under_stop_all_is_silent_even_when_it_does_not_take_the_voice():
    """The voice-BUSY half of row 6. With another session still holding content,
    Policy A declines to hand the newborn the voice, so it is never the speaker --
    this is the world M2's widened scan newly reaches, and the one the sibling
    test above structurally cannot see. Measured silent at bc2b743, spoken at
    b321b8b."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    daemon._enqueue("A", "prose", "a backlog", False)   # keeps the voice busy on A
    daemon.handle_message(_msg(MsgType.STOP_ALL, "A"))
    for _ in range(3):
        daemon._speak_loop_once()
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.SESSION_START, "NEW", cwd="/x/new"))
    assert sessions.speaker() != "NEW", "setup: the newborn must NOT hold the voice"
    for _ in range(3):
        daemon._speak_loop_once()
    assert speaker.spoken == [], "a non-speaker newborn broke the lasting quiet"


def test_the_announce_marker_survives_an_undeliverable_enqueue():
    """The marker must stay UNCLAIMED so the announce can still happen."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_ALL, "A"))
    daemon.handle_message(_msg(MsgType.SESSION_START, "NEW", cwd="/x/new"))
    assert sessions.claim_announce("NEW") is True, (
        "the one-shot was burned before it could ever be delivered"
    )


def test_the_session_names_itself_the_first_time_he_can_hear_it():
    """Delivered at the ctrl-cmd-S start that first makes it audible."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_ALL, "A"))
    for _ in range(3):
        daemon._speak_loop_once()      # empty A's queue so the voice is idle
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.SESSION_START, "NEW", cwd="/x/new"))
    for _ in range(3):
        daemon._speak_loop_once()
    speaker.spoken.clear()
    # The drain above is load-bearing. on_stop_session NEVER reads
    # msg["session"] -- it resolves its target from workspace()
    # (playback.py:121-127). Leave "All stopped." queued on A and
    # _voice_busy_elsewhere("NEW") is True, so SESSION_START takes its
    # register-only branch, _foreground stays "A", the press starts A and
    # announces "1, Another session." with no "new" in it -- and the failure
    # points at playback.py's brand-new block instead of at this setup. Name
    # the arming so a regression in it is named rather than mis-attributed.
    assert sessions.workspace() == "NEW"
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "NEW"))   # start
    for _ in range(4):
        daemon._speak_loop_once()
    assert any("new" in (s or "").lower() for s in speaker.spoken), (
        "the session never named itself: {0}".format(speaker.spoken)
    )


def test_a_dead_sessions_announce_stays_armed_at_a_start():
    """The resume calls `_sanction_dead_read(fg, whole=False)`, which sanctions
    exactly ONE pop -- and "Resumed." consumes it (host.py:150-157). A second
    item on a dead stream is then stranded: it was enqueued with no `entry=`,
    so it is in no history transcript and catch-up cannot recover it, while
    `claim_announce` is one-shot until `unregister`. Claiming here would burn
    the marker for the rest of that session's life -- the exact failure this
    task exists to fix, reintroduced at the site added to fix it."""
    from sonari.sessions import Identity

    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_ALL, "A"))
    for _ in range(3):
        daemon._speak_loop_once()
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.SESSION_START, "NEW", cwd="/x/new"))
    # A captured tty whose device node does not exist is `dead` by
    # ttyutil.tty_alive; a session with no identity at all stays fail-open
    # live. Fork 4 routes ⌃⌘S onto exactly such a stopped workspace.
    sessions.set_identity("NEW", Identity(term_program="Apple_Terminal",
                                          tty="/dev/ttys-does-not-exist"))
    assert sessions.workspace() == "NEW"
    assert sessions.is_live("NEW") is False
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "NEW"))   # start
    for _ in range(4):
        daemon._speak_loop_once()
    assert sessions.claim_announce("NEW") is True, (
        "the announce was burned into a stream the speak loop then releases"
    )


def test_maybe_hint_leaves_the_key_open_on_a_stopped_stream():
    """The docstring's own promise, finally implemented."""
    from sonari.daemon.features.teaching import maybe_hint, HINTS

    daemon, _, speaker, sessions, _ = make_daemon(verbosity="everything",
                                                  foreground="B")
    sessions.register("B", cwd="/x/bravo")
    daemon._stream("B").stopped = True
    key = sorted(HINTS)[0]
    maybe_hint(daemon, key, "B")
    assert key not in daemon._hinted, (
        "the hint was burned into a stream that could not speak it"
    )
    daemon._stream("B").stopped = False
    maybe_hint(daemon, key, "B")
    assert key in daemon._hinted


# --- CARRIED-INPUTS Task 10 Input 1 (R21): announce_resume is the same class
# of one-shot, on the STOP side rather than the birth side. ---

def test_announce_resume_is_invalidated_by_a_stop_before_delivery():
    """The arm-time guard at lifecycle.py's on_set_foreground (`st is None or
    not st.stopped`) proves only ARM-TIME deliverability. Before this fix
    nothing invalidated announce_resume if the SAME stream was stopped again
    in the arm-to-deliver window: SET_FOREGROUND -> ctrl-cmd-S -> FLUSH would
    land a stale "Resumed." on a stream he just re-muted, and because it is a
    control_cue the held branch speaks it THROUGH that very mute."""
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "fg", cwd="/x/fg"))
    assert daemon._stream("fg").announce_resume is True         # armed
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "fg"))     # ctrl-cmd-S: re-stop fg
    assert daemon._stream("fg").stopped is True
    assert daemon._stream("fg").announce_resume is False, (
        "a stale claim survived the very stop that falsified it"
    )
    daemon.handle_message(_msg(MsgType.FLUSH, "fg"))
    assert all(it.text != "Resumed." for it in queue._items), (
        "FLUSH voiced a stale Resumed. through the mute he just pressed"
    )


def test_announce_resume_is_invalidated_by_stop_all_before_delivery():
    """The master-quiet variant of R21. on_stop_all's loop stops EVERY stream
    but (before this fix) touched none of their armed announce_resume flags,
    so an armed flag survived ctrl-cmd-M and FLUSH still landed "Resumed." on
    a stream now held by the master quiet -- voiced by the same control_cue
    held branch."""
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "fg", cwd="/x/fg"))
    assert daemon._stream("fg").announce_resume is True         # armed
    daemon.handle_message(_msg(MsgType.STOP_ALL, "fg"))
    assert daemon._stream("fg").stopped is True
    assert daemon._stream("fg").announce_resume is False, (
        "a stale claim survived stop-all"
    )
    daemon.handle_message(_msg(MsgType.FLUSH, "fg"))
    assert all(it.text != "Resumed." for it in queue._items), (
        "FLUSH voiced a stale Resumed. through the master quiet"
    )


# --- Named risk from the dispatch: Step 5 moves the announce off control_cue,
# so it newly qualifies for W12's _last_utterance capture. ---

def test_the_deferred_announce_is_captured_by_repeat_last():
    """Step 5 removes control_cue from the SESSION_START announce, so the item
    newly takes host.py:1564's W12-capture branch (`elif completed and not
    item.control_cue`) that it was excluded from before Step 5. Checked
    separately (not asserted here): the folder-prefix/_last_spoken_session
    axis at host.py:665 is UNAFFECTED, because names_session already takes
    that branch ahead of the control_cue test -- only the repeat axis moves.
    Pinning the resulting behaviour per the dispatch's instruction: ctrl-cmd-R
    after a deferred announce now replays it, where before Step 5 it could
    not (the announce was excluded from capture)."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_ALL, "A"))
    for _ in range(3):
        daemon._speak_loop_once()
    daemon.handle_message(_msg(MsgType.SESSION_START, "NEW", cwd="/x/new"))
    for _ in range(3):
        daemon._speak_loop_once()
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "NEW"))   # start; delivers the deferred announce
    for _ in range(4):
        daemon._speak_loop_once()
    expected = "{0}, {1}.".format(sessions.number("NEW"), sessions.folder("NEW"))
    assert expected in speaker.spoken, "setup: the announce itself was never heard"
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.REPEAT_LAST, "NEW"))
    daemon._speak_loop_once()
    assert speaker.spoken == [expected], (
        "ctrl-cmd-R did not replay the deferred announce: {0}".format(speaker.spoken)
    )
