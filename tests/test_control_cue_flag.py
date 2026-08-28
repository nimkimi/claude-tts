"""M1: one concept where there were two booleans.

A control cue is the utterance a deliberate operator gesture produces as its
own answer. It exists because he pressed a key; it is not narrated content,
and it is delivered regardless of whether the stream it lands on is held.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md 4.1, 4.4 M1.
"""
import pytest

from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType
from sonari.queue import SpeechItem, SpeechQueue


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_speech_item_carries_one_control_cue_field():
    item = SpeechItem(id=1, session="A", kind="prose", text="x",
                      is_decision=False, control_cue=True)
    assert item.control_cue is True
    assert not hasattr(item, "mute_exempt")
    assert not hasattr(item, "pause_exempt")


def test_pop_control_cue_scans_past_ordinary_content():
    q = SpeechQueue()
    q.enqueue(SpeechItem(id=1, session="A", kind="prose", text="narration",
                         is_decision=False))
    q.enqueue(SpeechItem(id=2, session="A", kind="prose", text="Stopped.",
                         is_decision=False, control_cue=True))
    got = q.pop_control_cue()
    assert got is not None and got.text == "Stopped."
    assert len(q) == 1
    assert q.pop_control_cue() is None


def test_a_control_cue_is_never_folder_prefixed():
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    daemon._enqueue("A", "prose", "narration", False)
    daemon._speak_loop_once()
    daemon._enqueue("B", "prose", "Rate 225.", False, control_cue=True)
    sessions.set_speaker("B")
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Rate 225.", "a control cue took a folder prefix"


def test_playback_resume_clears_stopped_before_the_cue():
    """Guard: playback.py's on_stop_session resume branch sets
    `st.stopped = False` before enqueueing "Resumed." (control_cue=True), so
    the cue's own target stream is provably not stopped by the time it is
    delivered. Asserts the outcome the guard controls -- the stream's
    stopped flag -- not audibility, since a control cue speaks either way
    post-migration. Must fail if that assignment is replaced with a no-op.
    """
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    daemon._stream("A").stopped = True
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))   # resume
    assert daemon._stream("A").stopped is False


def test_keep_going_never_adopts_a_stopped_stream():
    """Guard: _select_keep_going skips every st.stopped stream, so the session
    it adopts provably is not stopped when the resume cue is enqueued to it.

    The world is foreground=None DELIBERATELY. Stopping the current speaker
    instead sends _speak_loop_once into its held branch, which returns
    unconditionally -- selection never runs and the test proves nothing about
    this guard. (That is exactly how the first version of this test was wrong.)

    A is enqueued FIRST, so it holds the lower, longest-waiting id and would win
    selection if the stopped-skip were removed. That is what makes this test
    non-vacuous rather than merely green.
    """
    daemon, _, speaker, sessions, _ = make_daemon(foreground=None)
    for sid in ("A", "B"):
        sessions.register(sid, cwd="/x/" + sid)
    daemon._enqueue("A", "prose", "stopped content", False)
    daemon._enqueue("B", "prose", "background content", False)
    daemon._stream("A").stopped = True
    for _ in range(4):
        daemon._speak_loop_once()
    assert sessions.speaker() == "B"
    assert "background content" in speaker.spoken
    assert "stopped content" not in speaker.spoken


def test_waiting_target_skips_a_stopped_stream():
    """Guard: _waiting_target (focus.py:22) excludes st.stopped from
    eligibility -- covers all three focus.py sites at once (each reads the
    one target it returns). B is registered first (so it would win the
    insertion-order tiebreak among equally-ranked prose candidates) and has
    its stream stopped; C is registered second, not stopped. If the
    st.stopped clause were removed from _waiting_target's predicate, B would
    become the target instead of C -- that is what makes this non-vacuous.
    """
    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    for sid in ("B", "C"):
        sessions.register(sid, cwd="/x/" + sid)
    daemon._enqueue("B", "prose", "stopped content", False)
    daemon._enqueue("C", "prose", "waiting content", False)
    daemon._stream("B").stopped = True
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "A"))
    assert sessions.foreground() == "C"


def test_announce_resume_is_not_armed_on_a_stopped_stream():
    """Guard: the announce_resume arm site (lifecycle.py's on_set_foreground,
    inside `if voice_state == "quiet-hold": ... if st is None or not
    st.stopped:`) only arms the flag when the target stream is not stopped.

    This proves ARM-TIME behaviour ONLY -- it says nothing about delivery.
    Nothing clears announce_resume if the stream is stopped again between
    arming and delivery (a known, accepted edge for this task's zero-
    behaviour-change scope; see the delivery-time comments at
    lifecycle.py/prose.py, and Task 10 which owns the marker lifecycle).

    Must fail if the st.stopped check is removed from that arm condition.
    """
    daemon, _, speaker, sessions, _ = make_daemon(foreground=None)
    sessions.register("A", cwd="/x/alpha")
    daemon.voice_state = "quiet-hold"
    daemon._stream("A").stopped = True
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "A"))
    assert daemon._stream("A").announce_resume is False
