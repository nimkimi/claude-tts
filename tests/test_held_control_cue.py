"""M2: a press can land anywhere, including a stopped stream that is not --
or is no longer -- the speaker.

Counterfactual C1: Fork-2's commit-onto-muted does set_speaker(None) and THEN
enqueues the landing cue to the muted target, so the speaker-scoped scan could
never reach its own landing cue. Flipping the flag on every item in that
queue and ticking twenty times still produced SPOKEN=[].
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md 4.4 M2.
"""
from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType
from sonari.queue import SpeechItem, SpeechQueue


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_oldest_control_cue_id_peeks_without_removing():
    q = SpeechQueue()
    q.enqueue(SpeechItem(id=7, session="A", kind="prose", text="narration",
                         is_decision=False))
    q.enqueue(SpeechItem(id=9, session="A", kind="prose", text="Stopped.",
                         is_decision=False, control_cue=True))
    assert q.oldest_control_cue_id() == 9
    assert len(q) == 2
    assert SpeechQueue().oldest_control_cue_id() is None


def test_a_control_cue_on_a_stopped_non_speaker_stream_is_voiced():
    """C1, directly. speaker() is None; the cue must still be heard."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    daemon._stream("B").stopped = True
    sessions.set_speaker(None)
    daemon._enqueue("B", "prose", "bravo.", False, control_cue=True)
    daemon._speak_loop_once()
    assert speaker.spoken == ["bravo."], (
        "a control cue on a stopped non-speaker stream was never reached"
    )


def test_the_oldest_control_cue_wins_across_two_stopped_streams():
    """Oldest-first by the daemon-global monotonic id -- the same ordering key
    _select_keep_going already uses, so there is no new ordering concept."""
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    for s in ("B", "C"):
        sessions.register(s, cwd="/x/" + s)
        daemon._stream(s).stopped = True
    sessions.set_speaker(None)
    daemon._enqueue("C", "prose", "charlie first", False, control_cue=True)
    daemon._enqueue("B", "prose", "bravo second", False, control_cue=True)
    daemon._speak_loop_once()
    daemon._speak_loop_once()
    assert speaker.spoken == ["charlie first", "bravo second"]


def test_ordinary_content_on_a_stopped_stream_is_still_held():
    """M2 widens WHICH stopped streams are scanned, not WHAT is eligible.
    Narration on a muted session stays silent -- that is what the mute is for.
    """
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    daemon._stream("B").stopped = True
    sessions.set_speaker(None)
    daemon._enqueue("B", "prose", "narration", False)
    daemon._speak_loop_once()
    assert speaker.spoken == []
    assert len(daemon._stream("B").queue) == 1


def test_a_non_stopped_background_stream_is_untouched():
    """KEEP-GREEN sanity check, NOT part of the RED set.

    This passes before the restructure too (today the held branch is gated on
    the speaker's own stream, so B is never scanned either way). It is here to
    pin that M2 does not start draining live background streams -- D8 law 1,
    verbal never bypasses the queue.
    """
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    daemon._enqueue("B", "prose", "background cue", False, control_cue=True)
    daemon._enqueue("A", "prose", "speaker content", False)
    daemon._speak_loop_once()
    assert speaker.spoken == ["speaker content"], (
        "a control cue on a LIVE background stream jumped the queue"
    )
