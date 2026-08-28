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
    task exists to fix, reintroduced at the site added to fix it.

    playback.py's own comment at that site promises BOTH survive: the
    `claim_announce` marker AND the `announce_deferred` flag. Asserting only
    the marker leaves half the promise unpinned -- moving
    `st.announce_deferred = False` above the is_live short-circuit (which
    leaves the live path untouched) loses the announce for that dead session's
    whole life with the entire suite still green. Both halves, therefore."""
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
    assert daemon._stream("NEW").stopped is False, (
        "setup: the press did not take the resume branch"
    )
    assert sessions.claim_announce("NEW") is True, (
        "the announce was burned into a stream the speak loop then releases"
    )
    assert daemon._stream("NEW").announce_deferred is True, (
        "the deferred flag was burned, so no later start can ever deliver it"
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
    newly takes host.py's W12-capture branch (`elif completed and not
    item.control_cue`) that it was excluded from before Step 5. Pinning the
    resulting behaviour: ctrl-cmd-R after a deferred announce now replays it,
    where before Step 5 it could not (the announce was excluded from capture).

    The _attributed_text / _last_spoken_session axis ALSO moved, and an earlier
    version of this docstring claimed it did not. Both halves of that, because
    the half-truth is what misled three separate readers:

    - The stated REASONING is correct. `names_session=True` takes its branch
      ahead of the `control_cue` elif, so at a FIXED sha, flipping the flag
      alone changes nothing on this axis.
    - The CONCLUSION was still false, because Step 5 changed the PATH, not just
      the flag. Before Step 5 the announce was a control cue, so it was popped
      by _pop_held_control_cue and spoken by the held branch, which calls
      speaker.speak(item.text, ...) DIRECTLY -- _attributed_text was never
      entered at all, and its `_last_spoken_session = item.session` write never
      happened.

    The observable is therefore the NEXT utterance from that session, not this
    one: probing the announce's own text returns '2, new.' in both worlds and
    reads falsely as "unaffected". Measured across the task, that axis moved
    from ['new. new follow up'] / lss=A to ['new follow up'] / lss=NEW. The
    CODE IS CORRECT -- HEAD suppresses exactly the double-announce that
    _attributed_text's own comment exists to prevent, and which the old
    behaviour actually produced. It is pinned by
    test_the_announce_claims_the_prefix_axis_for_its_own_session below."""
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


def test_the_announce_claims_the_prefix_axis_for_its_own_session():
    """The _attributed_text axis the test above used to claim was unaffected.

    The observable is the NEXT utterance from that session, never the announce
    itself -- the announce reads '2, new.' whether or not it entered
    _attributed_text, which is exactly how "unaffected" got believed. Because
    the announce now travels the ordinary path with names_session=True, it
    claims _last_spoken_session for NEW, so the follow-up is spoken bare.
    Before Step 5 it was a control cue popped by the held branch and spoken
    directly, _last_spoken_session stayed at A, and the follow-up came out as
    'new. new follow up' -- the double-announce _attributed_text's own comment
    exists to suppress."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_ALL, "A"))
    for _ in range(3):
        daemon._speak_loop_once()
    daemon.handle_message(_msg(MsgType.SESSION_START, "NEW", cwd="/x/new"))
    for _ in range(3):
        daemon._speak_loop_once()
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "NEW"))   # start; delivers it
    for _ in range(4):
        daemon._speak_loop_once()
    assert daemon._state._last_spoken_session == "NEW", (
        "the announce did not claim the prefix axis for its own session")
    speaker.spoken.clear()
    daemon._enqueue("NEW", "prose", "new follow up", False)
    for _ in range(3):
        daemon._speak_loop_once()
    assert speaker.spoken == ["new follow up"], (
        "the follow-up was prefixed, so the announce is being double-spoken: "
        "{0}".format(speaker.spoken))


def test_repeat_last_after_an_ordinary_start_replays_the_announce(monkeypatch):
    """The COMMON path of the ⌃⌘R capture change -- the deferred path is pinned
    two tests up, and only that one was pinned when this landed.

    After ANY ordinary (non-deferred) session start, ⌃⌘R now replays the
    announce rather than the real content that preceded it, because the
    announce is no longer a control cue and so newly qualifies for W12 capture.
    This DETECTS that behaviour; it does not endorse it. It is on the owner's
    audition list, and the levers are not what they look like: restoring
    control_cue on the announce does NOT re-invert spec row 6 (the arm gate
    carries that) and DOES hand back the R7 born-live-then-muted window. If it
    is ever ruled against, the fix is a separate repeat-exemption axis, not
    this flag.

    The install nag is patched out because it would otherwise be the last
    ordinary utterance and would itself become what ⌃⌘R replays.
    """
    from sonari.daemon.features import lifecycle

    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon._enqueue("A", "prose", "the real content he cares about", False)
    for _ in range(3):
        daemon._speak_loop_once()
    daemon.handle_message(_msg(MsgType.SESSION_START, "NEW", cwd="/x/new"))
    for _ in range(4):
        daemon._speak_loop_once()
    expected = "{0}, {1}.".format(sessions.number("NEW"), sessions.folder("NEW"))
    assert expected in speaker.spoken, (
        "setup: the ordinary announce was never heard: {0}".format(speaker.spoken))
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.REPEAT_LAST, "NEW"))
    daemon._speak_loop_once()
    assert speaker.spoken == [expected], (
        "ctrl-cmd-R replayed something other than the announce: {0}".format(
            speaker.spoken))


def test_the_install_nag_leaves_the_throttle_open_on_a_stopped_stream(monkeypatch):
    """The install nag is one of this task's three charter sites and shipped
    with NO receipt: `if st.stopped: return` in _maybe_guide_setup could be
    deleted outright with the whole suite green. The three existing
    setup-health tests only prove the nag still fires on a NON-stopped stream.

    The nag is throttled by a one-shot `guided` flag. Fire it into a stream
    that cannot speak and the flag burns anyway, so the one chance to tell him
    Sonari is not installed is spent on silence -- and he is the user least
    able to notice that nothing was said. So on a stopped stream the throttle
    must be left OPEN for the next audible start."""
    from sonari.daemon.features import lifecycle

    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    monkeypatch.setattr(lifecycle, "_setup_health",
                        lambda v: ("not_installed", "RUN slash sonari install"))
    daemon.handle_message(_msg(MsgType.STOP_ALL, "A"))
    for _ in range(3):
        daemon._speak_loop_once()
    daemon.handle_message(_msg(MsgType.SESSION_START, "NEW", cwd="/x/new"))
    assert daemon._stream("NEW").stopped is True          # setup: born muted
    assert daemon._stream("NEW").guided is False, (
        "the nag was throttled into a stream that could not speak it")


def test_a_quiet_verbosity_start_leaves_the_deferred_announce_armed():
    """The verbosity clause in the deferred-delivery gate, pinned in the
    RESTRICTIVE direction. The existing tests only prove the announce IS
    delivered when verbosity allows it; nothing proved that a start under
    `quiet` leaves the flag armed rather than burning it.

    Both halves matter and they are separate assertions: quiet must not SPEAK
    the announce, and it must not spend it either. Turning the voice down is
    not a decision to forgo the session's name forever -- the next audible
    start is still owed it."""
    daemon, _, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_ALL, "A"))
    for _ in range(3):
        daemon._speak_loop_once()
    speaker.spoken.clear()
    daemon.handle_message(_msg(MsgType.SESSION_START, "NEW", cwd="/x/new"))
    assert daemon._stream("NEW").announce_deferred is True     # setup: armed
    config["verbosity"] = "quiet"          # he turns the voice down before starting it
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "NEW"))   # start
    for _ in range(4):
        daemon._speak_loop_once()
    assert not any("new" in (s or "").lower() for s in speaker.spoken), (
        "a quiet start spoke the announce: {0}".format(speaker.spoken))
    assert daemon._stream("NEW").announce_deferred is True, (
        "quiet burned the flag instead of leaving it for an audible chance")
