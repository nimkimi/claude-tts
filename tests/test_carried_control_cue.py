"""The three sites that CARRY a control_cue flag instead of choosing one.

Spec 4.1 names them as the mechanical consumers: a chooser cancel, ⌃⌘W and
⌃⌘R each barge in on the utterance in flight and re-queue it, and each copies
`control_cue` off the captured item rather than re-deciding it. All three
share one five-line comment and, until this file, had zero tests between them:
every one is deletable with the whole suite green.

What the carry protects is a silent loss. An item enqueued as a control cue is
the answer to a deliberate press, and it is the held branch -- not the ordinary
drain -- that voices it while its stream is stopped. Drop the flag on the way
back into the queue and an answer that was mid-sentence when the barge-in
arrived becomes ordinary content on a muted stream: it never speaks again, and
nothing says so.

The assertions are at queue level rather than on the speaker, because the carry
IS the property under test -- whether that re-queued item is later voiced
depends on when the stream is stopped, and pinning delivery instead would prove
the held branch works, which other files already do.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md 4.1.
"""
from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType
from sonari.queue import SpeechItem


IN_FLIGHT = "an answer that was mid-sentence"


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _world():
    """A and B registered: the chooser needs candidates to step through, and
    ⌃⌘W's diverged/undiverged phrasing needs somewhere to diverge to."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    # Mid-play simulation by assigning _current_item directly: the suite's
    # established idiom (test_chooser.py, test_catchup_burn.py) -- a real tick
    # against a FakeSpeaker completes and releases the claim within the tick.
    daemon._current_item = SpeechItem(id=901, session="A", kind="prose",
                                      text=IN_FLIGHT, is_decision=False,
                                      control_cue=True)
    return daemon, speaker, sessions


def _assert_carried(daemon, gesture):
    requeued = [it for it in daemon._stream("A").queue._items
                if it.text == IN_FLIGHT]
    assert requeued, (
        "{0} did not re-queue the interrupted item at all: {1}".format(
            gesture, [it.text for it in daemon._stream("A").queue._items]))
    assert requeued[0].control_cue is True, (
        "{0} re-queued the interrupted answer with control_cue dropped. It was "
        "enqueued as a control cue and is now ordinary content: on a stopped "
        "stream the held branch will never pick it up, so the answer is lost "
        "silently.".format(gesture))


def test_where_am_i_carries_the_flag_of_the_item_it_interrupted():
    """control.py's on_where_am_i requeue."""
    daemon, _, _ = _world()
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, "A"))
    _assert_carried(daemon, "where_am_i")


def test_repeat_last_carries_the_flag_of_the_item_it_interrupted():
    """playback.py's on_repeat_last requeue. _last_utterance must be set or the
    handler answers "Nothing to repeat." and returns before the requeue."""
    daemon, _, _ = _world()
    daemon._last_utterance = ("something said earlier", None)
    daemon.handle_message(_msg(MsgType.REPEAT_LAST, "A"))
    _assert_carried(daemon, "repeat_last")


def test_a_chooser_cancel_carries_the_flag_of_the_item_it_captured():
    """chooser.py's _restore_and_clear. Opening the chooser CAPTURES the
    in-flight item (parked, not queued); cancelling restores it."""
    daemon, _, _ = _world()
    daemon.handle_message(_msg(MsgType.CHOOSER_STEP, "A", direction="next"))
    assert daemon._chooser is not None and daemon._chooser.captured is not None
    daemon.handle_message(_msg(MsgType.CHOOSER_CANCEL, "A"))
    assert daemon._chooser is None
    _assert_carried(daemon, "chooser_cancel")
