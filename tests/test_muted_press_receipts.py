"""Every declared gesture, pressed on a muted session, makes a sound.

Behavioural and not static, because static analysis provably cannot close this
class here: _readback takes **kw, so an AST walk over its own _enqueue site
sees nothing wrong -- and that is the site probe P4 proved was broken. The
repo's own thesis is that the tests pin the call, not the ear.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md 4.2.
"""
import pytest

from tests.daemon_helpers import make_daemon
from sonari.keymap import ACTIONS, CONTROL_GESTURES
from sonari.protocol import MsgType


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _declared_control_cues():
    return sorted(
        name
        for name, meta in {**ACTIONS, **CONTROL_GESTURES}.items()
        if meta["control_cue"]
    )


def _world():
    """A world where every gesture has something to answer with.

    B is foreground AND speaker, because most handlers resolve their delivery
    target from workspace()/speaker()/foreground() and NEVER from
    msg["session"]. If the muted session is not the one the gesture actually
    reaches, this whole receipt degrades into "pressing a key made some sound
    somewhere" -- which was already true before any of this work, and would
    stay true if the bug were reintroduced. That is the exact failure this
    file exists to prevent, so it must not be reproduced inside it.

    A and C stay live and unmuted so the gestures that deliberately look for
    ANOTHER session have somewhere to look.
    """
    daemon, _, speaker, sessions, _ = make_daemon(verbosity="everything",
                                                  foreground="B")
    for sid, cwd in (("A", "/x/alpha"), ("B", "/x/bravo"), ("C", "/x/charlie")):
        sessions.register(sid, cwd=cwd)
    sessions.set_speaker("B")
    # Two REAL turns per session: start_turn is the only thing that bumps the
    # turn id, and without it _nav_response takes its len(turns) < 2 early
    # return and never exercises cross-turn content delivery.
    for sid in ("A", "B"):
        daemon.history.record(sid, "prose", "older response")
        daemon.history.end_message(sid)
        daemon.history.start_turn(sid)
        daemon.history.record(sid, "prose", "newer response")
        daemon.history.end_message(sid)
    daemon._enqueue("C", "prose", "c has content", False)
    return daemon, speaker, sessions


# Per-gesture preconditions. Each returns THE SESSION THAT MUST BE MUTED for
# this gesture to be tested honestly -- i.e. the one the handler will actually
# deliver to. An action with no branch below falls through to B, which is both
# workspace() and speaker() in _world(); a gesture that delivers anywhere else
# MUST be given its own branch, because that fallthrough is a default and not
# a guard. What forces a NEW gesture to be looked at at all is the
# `assert action in _MESSAGE` in the test body -- that assert is the
# enumeration that does not exist today.
def _arm(action, daemon, sessions):
    if action in ("jump_decision", "reread_options", "approve", "deny"):
        import threading

        item_id = daemon._enqueue("B", "permission",
                                  "A question needs your answer.", True)
        # The real store shape (decisions.py:196-197), not an approximation:
        # approve/deny read ["behavior"], jump-decision's miss path and
        # reread-options' fallback both read ["text"].
        daemon._pending_decisions["B"] = {
            "event": threading.Event(), "behavior": None,
            "text": "A question needs your answer.", "item_id": item_id,
        }
        st = daemon._streams.get("B")
        if st is not None:
            st.options = "yes, or no"
        return "B"
    if action in ("chooser_commit", "chooser_digit"):
        # These must land on a session OTHER than the chooser's origin --
        # committing back onto the origin is a documented silent no-op, and a
        # test that lands there passes for the wrong reason. Origin is B, so
        # the landing target is A, and A is what must be muted (this is the
        # spec's row 4: the commit-onto-muted landing cue).
        daemon._stream("A")
        daemon.handle_message(_msg(MsgType.CHOOSER_STEP, "B", direction="next"))
        # The commit RELEASES the voice (chooser.py:224), so keep-going will
        # adopt any live stream with a pile and make a sound that has nothing
        # to do with the muted landing cue -- and both assertions would still
        # pass with the landing cue's control_cue flag deleted. Leave it
        # nothing to adopt. C stays registered and live, so it is still a
        # chooser candidate; only its pile goes.
        daemon._stream("C").queue.clear()
        # ...and B's own queue too, which is the leak the C-drain alone missed.
        # The CHOOSER_STEP two lines up enqueues the one-shot browse hint
        # ("Hold the chord and tap Tab to browse...") onto B, and no speak loop
        # runs during _arm, so it is STILL QUEUED when the test clears
        # speaker.spoken and presses. B is unmuted and IS speaker(), so it
        # drains and `spoken or earcons` cannot tell ['alpha.', hint] from
        # [hint] -- these two parameters stayed GREEN with the landing cue's
        # control_cue flag reverted. Measured at pop time the landing cue is
        # held correctly and never spoken; only this hint made the noise.
        daemon._stream("B").queue.clear()
        return "A"
    if action in ("chooser_step_next", "chooser_step_prev"):
        daemon._stream("A")
        return "B"          # _deliver_preview speaks to speaker() == B
    if action == "repeat_last":
        daemon._enqueue("B", "prose", "something to repeat", False)
        daemon._speak_loop_once()
        return "B"
    if action == "catch_up":
        return "B"          # on_catch_up reads unheard history off workspace()
    if action == "skip_pile":
        daemon._enqueue("B", "prose", "a pile to skip", False)
        return "B"
    if action == "jump_waiting":
        # jump_waiting deliberately EXCLUDES workspace(), so it can never land
        # on the muted workspace. Drain C so there IS no waiting target; the
        # handler then answers "No session waiting." into speaker() == B.
        daemon._stream("C").queue.clear()
        return "B"
    if action == "stop_session":
        # Same class as jump_waiting above. This row's receipt is the audible
        # "Resumed." that Fork 4's un-mute enqueues -- but ⌃⌘S also sets
        # voice_state back to "flowing" (playback.py's start branch), so
        # keep-going adopts C's unrelated backlog and speaks THAT. Measured
        # with the "Resumed." enqueue deleted outright: 25 passed, because the
        # sound observed was C's 'c has content'. Drain C so the only thing
        # left that can make a noise is the gesture's own answer.
        daemon._stream("C").queue.clear()
        return "B"
    if action == "os_focus":
        # on_os_focus only cues when workspace() actually CHANGES, and a signal
        # only resolves to a session that has a registered Identity. Without
        # this the gesture is a silent no-op and the case fails on arrival.
        from sonari.sessions import Identity

        sessions.set_identity("A", Identity(term_program="iTerm.app",
                                            tty="/dev/ttys002",
                                            iterm_session_id="w0t0p0"))
        return "B"
    return "B"


_MESSAGE = {
    "nav_next": (MsgType.NAV, {"to": "next"}),
    "nav_prev": (MsgType.NAV, {"to": "prev"}),
    "nav_prev_response": (MsgType.NAV, {"to": "prev_response"}),
    "nav_next_response": (MsgType.NAV, {"to": "next_response"}),
    "stop_session": (MsgType.STOP_SESSION, {}),
    "stop_all": (MsgType.STOP_ALL, {}),
    "jump_waiting": (MsgType.JUMP_WAITING, {}),
    "jump_decision": (MsgType.JUMP_DECISION, {}),
    "repeat_last": (MsgType.REPEAT_LAST, {}),
    "chooser_step_next": (MsgType.CHOOSER_STEP, {"direction": "next"}),
    "chooser_step_prev": (MsgType.CHOOSER_STEP, {"direction": "prev"}),
    "where_am_i": (MsgType.WHERE_AM_I, {}),
    # NOTE the key is "behavior" and approve's value is "allow" -- taken from
    # keymap.ACTIONS' own message literals, which is the only source that
    # cannot drift from what hotkeyd actually sends.
    "approve": (MsgType.ANSWER_PERMISSION, {"behavior": "allow"}),
    "deny": (MsgType.ANSWER_PERMISSION, {"behavior": "deny"}),
    "faster": (MsgType.SET_RATE, {"delta": 25}),
    "slower": (MsgType.SET_RATE, {"delta": -25}),
    "reread_options": (MsgType.REREAD_OPTIONS, {}),
    "cycle_verbosity": (MsgType.CYCLE_VERBOSITY, {}),
    "skip_pile": (MsgType.SKIP_PILE, {}),
    "catch_up": (MsgType.CATCH_UP, {}),
    "learn_mode": (MsgType.LEARN_MODE, {}),
    "query_actions": (MsgType.QUERY_ACTIONS, {}),
    "chooser_commit": (MsgType.CHOOSER_COMMIT, {}),
    "chooser_digit": (MsgType.CHOOSER_DIGIT, {"digit": 2}),
    "os_focus": (MsgType.OS_FOCUS, {"term_program": "iTerm.app",
                                    "tty": "/dev/ttys002",
                                    "iterm_session_id": "w0t0p0",
                                    "focused": True}),
}


# The gesture's OWN answer, for the rows where `spoken or earcons` provably
# cannot tell it from a second sound the same press legitimately makes.
#
# `_arm` closes this leak by DRAINING the competitor wherever the competitor is
# unrelated (chooser_commit's browse hint, stop_session's keep-going adoption).
# These three cannot be drained: the other sound is produced by the gesture
# itself, and removing it would stop exercising the branch under test.
#
#   nav_prev_response / nav_next_response -- _world() records two real turns so
#   _nav_response does not take its `len(turns) < 2` early return, which is
#   exactly what leaves the seek-and-play CONTENT (navigation.py:107) available
#   to satisfy the row. Measured: navigation.py:102's orientation cue flipped to
#   control_cue=False leaves the full suite green, and the sound observed is
#   'older response'. Clearing the history to remove it would send the press
#   down the no-turns branch instead.
#
#   catch_up -- _world() sets summarizer off, so ⌃⌘L's acknowledgement
#   (catchup.py:80) and the digest fallback (catchup.py:183) BOTH speak, and
#   they masked each other: silencing either alone left the suite green and only
#   silencing both turned the row red. Both are named here, so each is now
#   pinned on its own.
#
# Substring, not equality: a held control cue can still carry a folder prefix,
# and this is a receipt about WHICH utterance was delivered, not about framing.
_OWN_ANSWER = {
    "nav_prev_response": ("Oldest response.",),
    "nav_next_response": ("Back to the latest.",),
    "catch_up": ("Catching up 2 items in bravo.",
                 "Summary unavailable. Last: newer response."),
}


def test_the_enumeration_matches_the_declared_control_cues_exactly():
    """The `assert action in _MESSAGE` below sees ADDITIONS but is structurally
    blind to REMOVALS. Dropping an action's control_cue flag deletes its
    PARAMETER, so the row simply vanishes and this file passes with one fewer
    test -- a receipt quietly retiring itself is the failure mode that lets a
    gesture go silent again. Equality is the only form that sees both
    directions from inside this file.
    """
    assert set(_MESSAGE) == set(_declared_control_cues())


@pytest.mark.parametrize("action", _declared_control_cues())
def test_every_gesture_answers_on_a_muted_session(action):
    assert action in _MESSAGE, (
        "action {0!r} declares control_cue but this receipt has no message for "
        "it. Add one -- an unenumerated gesture is exactly how the eight "
        "silent sites happened.".format(action)
    )
    daemon, speaker, sessions = _world()
    muted = _arm(action, daemon, sessions)
    daemon._stream(muted).stopped = True
    speaker.spoken.clear()
    speaker.earcons.clear()
    msg_type, extra = _MESSAGE[action]
    daemon.handle_message(_msg(msg_type, "B", **extra))
    for _ in range(6):
        daemon._speak_loop_once()
    assert speaker.spoken or speaker.earcons, (
        "{0} pressed on a muted session produced no sound at all".format(action)
    )
    for wanted in _OWN_ANSWER.get(action, ()):
        assert any(wanted in said for said in speaker.spoken), (
            "{0}: something was spoken, but not this gesture's own answer "
            "{1!r}. Heard: {2}. This row passes on ANY sound, and for this "
            "gesture the press makes a second one -- so without this "
            "assertion the answer itself can go silent with the suite "
            "green.".format(action, wanted, speaker.spoken)
        )
    # The receipt only means something if the sound came out of a HELD stream.
    if action == "stop_session":
        # The one exemption, and it is RATIFIED, not a waiver. Fork 4
        # (playback.py:116-127): ctrl-cmd-S on a stopped WORKSPACE deliberately
        # STARTS it -- R7 "start the session you navigated to", which is the
        # only way a chooser-committed mute is keyboard-startable at all.
        # playback.py:146 is the daemon's ONLY runtime un-mute and stop_session
        # is the one parameter that reaches it, so this target is provably
        # un-muted by the very gesture under test; asserting it still stopped
        # would be asserting the regression that comment exists to prevent
        # (and tests/test_sp3_cycle.py already pins the opposite). What is
        # proved here is that the press was RECEIVED while stopped and answered
        # -- its receipt is the audible "Resumed.", not a held-stream delivery.
        assert daemon._stream(muted).stopped is False, (
            "stop_session: the muted workspace was not started, so Fork 4's "
            "asymmetric target (playback.py:124) has regressed"
        )
    else:
        assert daemon._stream(muted).stopped is True, (
            "{0}: the target was un-muted during delivery, so this proves "
            "nothing about the held path".format(action)
        )
