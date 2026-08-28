"""One representative pin per "Nothing to ..." family, on a muted session.

Fourteen of the branch's `control_cue` sites are empty answers -- the reply a
gesture gives when there is nothing to do. Every one of them is deletable with
the whole suite green, and every one of them goes SILENT on a muted session if
it is: the press produces no sound at all, which is indistinguishable from a
dead hotkey to someone working by ear. Pinning all fourteen individually would
be repetition; one per distinct sentence is what actually discriminates.

Families covered here: "No other response." (navigation.py's response-nav
early return), "Nothing to skip.", "Nothing to repeat.", "Nothing to catch
up." and "Cancelled.". `_nav`'s "Nothing to navigate yet." already has its own
pin in tests/test_muted_read_gestures.py.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md 4.1.
"""
from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _muted_world():
    """B is workspace, speaker AND the muted stream -- every handler below
    resolves its destination from workspace()/speaker(), never from
    msg["session"], so a different muted session would prove nothing."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="B")
    sessions.register("B", cwd="/x/bravo")
    sessions.set_speaker("B")
    daemon._stream("B").stopped = True
    speaker.spoken.clear()
    return daemon, speaker, sessions


def _press(daemon, speaker, msg_type, **kw):
    daemon.handle_message(_msg(msg_type, "B", **kw))
    for _ in range(4):
        daemon._speak_loop_once()
    return speaker.spoken


def _assert_said(spoken, wanted):
    assert any(wanted in (s or "") for s in spoken), (
        "{0!r} was never heard on the muted session -- the press was silent. "
        "Heard: {1}".format(wanted, spoken))


def test_response_nav_says_no_other_response_on_a_muted_session():
    """navigation.py:71, and one of the four sites where Task 5's flip created
    behaviour that never existed before this branch. ONE recorded turn, so
    _nav_response takes its `len(turns) < 2` arm with `turns` non-empty -- the
    "No other response." leg, not the empty-history one."""
    daemon, speaker, _ = _muted_world()
    daemon.history.record("B", "prose", "the only response")
    daemon.history.end_message("B")
    speaker.spoken.clear()
    _assert_said(_press(daemon, speaker, MsgType.NAV, to="prev_response"),
                 "No other response.")


def test_skip_pile_says_nothing_to_skip_on_a_muted_session():
    """playback.py:79. No history anywhere, so there is no addressable pile and
    the handler answers with the cue instead of skipping."""
    daemon, speaker, _ = _muted_world()
    _assert_said(_press(daemon, speaker, MsgType.SKIP_PILE), "Nothing to skip.")


def test_repeat_last_says_nothing_to_repeat_on_a_muted_session():
    """playback.py:348. Nothing has been spoken yet, so _last_utterance is
    None and ⌃⌘R answers rather than replaying."""
    daemon, speaker, _ = _muted_world()
    assert daemon._last_utterance is None
    _assert_said(_press(daemon, speaker, MsgType.REPEAT_LAST),
                 "Nothing to repeat.")


def test_catch_up_says_nothing_to_catch_up_on_a_muted_session():
    """catchup.py:59. No unheard entries ahead of the frontier."""
    daemon, speaker, _ = _muted_world()
    _assert_said(_press(daemon, speaker, MsgType.CATCH_UP),
                 "Nothing to catch up.")


def test_a_second_catch_up_press_says_cancelled_on_a_muted_session():
    """catchup.py:124. ⌃⌘L while a catch-up is in flight is a pure cancel
    (spec 2.9), and the cancel's own acknowledgement is the only thing that
    tells him the press landed -- there is nothing else to hear."""
    daemon, speaker, _ = _muted_world()
    daemon.history.record("B", "prose", "something to catch up on")
    daemon.history.end_message("B")
    # A summarizer that never returns, so the bundle is still "preparing" when
    # the second press arrives. summarizer="off" would resolve to the digest
    # fallback immediately and the second press would find nothing to cancel.
    daemon._catchup = {"id": 1, "target": "B", "folder": "bravo",
                       "slice_end": (0, 0), "digest": "d",
                       "cancel": __import__("threading").Event(),
                       "phase": "preparing", "render_id": None,
                       "ended": False, "ack_id": None}
    speaker.spoken.clear()
    _assert_said(_press(daemon, speaker, MsgType.CATCH_UP), "Cancelled.")
