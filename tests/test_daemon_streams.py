from sonari.protocol import MsgType, PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(mtype, session, **extra):
    d = {"v": PROTOCOL_VERSION, "type": mtype, "session": session}
    d.update(extra)
    return d


def test_flush_resets_playback_but_keeps_mute():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    # mute A (sticky) and give it open/streaming + buffered state
    daemon.handle_message(_msg(MsgType.MUTE, "A"))
    # Raise minqueue so prose buffer accumulates instead of immediately draining
    config["minqueue"] = 5
    daemon.handle_message(_msg(MsgType.PROSE, "A", delta="hello there. ", index=0, final=False))
    st = daemon._stream("A")
    assert st.muted is True
    # Verify buffer is non-empty before FLUSH (so post-FLUSH == [] actually tests reset)
    assert st.prose_buffer != []

    daemon.handle_message(_msg(MsgType.FLUSH, "A"))

    st = daemon._stream("A")
    assert st.prose_buffer == []
    assert st.muted is True              # sticky preserved across a new prompt


def test_session_end_drops_the_whole_stream():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.PROSE, "A", delta="some text. ", index=0, final=False))
    assert "A" in daemon._streams

    daemon.handle_message(_msg(MsgType.SESSION_END, "A"))

    # whole stream gone — including assembler + nav_cursor, which the old
    # SESSION_END leaked (spec §4.3 cleanup-divergence fix).
    assert "A" not in daemon._streams


def test_no_legacy_per_session_containers_remain():
    daemon, *_ = make_daemon()
    for attr in ("_assemblers", "_prose_buffer", "_options", "_captured_msg",
                 "_open_msg", "_nav_cursor", "_muted_sessions",
                 "_warned_immediate", "_guided_sessions"):
        assert not hasattr(daemon, attr), f"legacy container {attr} still present"


# --- the flip: per-stream enqueue + foreground-driven speak loop -------------

def _pump_one(daemon):
    """Run exactly one speak-loop iteration (no thread)."""
    daemon._speak_loop_once()


def test_enqueue_lands_in_the_sessions_own_stream_queue():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._enqueue("a", "prose", "for a", False)
    daemon._enqueue("b", "prose", "for b", False)
    assert [i.text for i in stream_queue(daemon, "a")._items] == ["for a"]
    assert [i.text for i in stream_queue(daemon, "b")._items] == ["for b"]


def test_speak_loop_plays_only_the_foreground_stream():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._enqueue("a", "prose", "alpha", False)
    daemon._enqueue("b", "prose", "beta", False)   # background — must wait
    _pump_one(daemon)
    assert speaker.spoken == ["alpha"]
    assert len(stream_queue(daemon, "b")) == 1      # beta untouched


def test_background_accumulates_then_is_heard_after_switching_foreground():
    # Symptom 1 + 3a regression: B's output while A is foreground is NOT lost.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._enqueue("b", "prose", "beta-1", False)
    daemon._enqueue("b", "prose", "beta-2", False)
    _pump_one(daemon)
    assert speaker.spoken == []                      # nothing foreground to say
    sessions.set_foreground("b")                     # user switches to B
    _pump_one(daemon)
    _pump_one(daemon)
    assert speaker.spoken == ["beta-1", "beta-2"]    # heard, in order


def test_muted_foreground_item_is_dropped_but_exempt_is_spoken():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._stream("a").muted = True
    daemon._enqueue("a", "prose", "silenced", False)
    daemon._enqueue("a", "prose", "muted-cue", False, mute_exempt=True)
    _pump_one(daemon)   # drops "silenced"
    _pump_one(daemon)   # speaks the exempt cue
    assert speaker.spoken == ["muted-cue"]


def test_catch_up_routes_cross_session_backlog_into_the_foreground_stream():
    # Stage 2 pins catch_up's new behavior so Stages 3-6 can't silently regress it:
    # the unheard from ANOTHER session is replayed under the foreground voice and
    # heard, with its history entries marked heard.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon.handle_message(_msg(MsgType.PROSE, "b", delta="B unheard. ",
                               index=0, final=True))   # background, accumulates
    daemon.handle_message(_msg(MsgType.CATCH_UP, "a"))
    assert [i.text for i in stream_queue(daemon, "a")._items] == [
        "Catching up on another session.", "B unheard."]
    while len(stream_queue(daemon, "a")):
        _pump_one(daemon)
    assert speaker.spoken[-1] == "B unheard."
    assert daemon.history.unheard("b") == []          # entry marked heard
