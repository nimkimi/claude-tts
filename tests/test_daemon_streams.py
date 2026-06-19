from sonari.protocol import MsgType, PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(mtype, session, **extra):
    d = {"v": PROTOCOL_VERSION, "type": mtype, "session": session}
    d.update(extra)
    return d


def _prose(daemon, session, text, index=0, final=False):
    daemon.handle_message(_msg(MsgType.PROSE, session, delta=text, index=index, final=final))


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


def _drain(daemon):
    """Run the speak loop until the foreground stream's queue is empty (no thread)."""
    for _ in range(1000):
        fg = daemon.sessions.foreground()
        st = daemon._streams.get(fg)
        if st is None or len(st.queue) == 0:
            break
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


# --- jump_waiting handler (Task 2) -------------------------------------------

def test_jump_waiting_switches_to_background_and_announces_folder():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("b", cwd="/work/backend")
    _prose(daemon, "b", "All done. ")                  # b accumulates in the background
    assert len(stream_queue(daemon, "b")) >= 1
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "b"
    assert speaker.cancels == 1                          # cut-on-switch
    assert stream_queue(daemon, "b")._items[0].text == "Jumping to backend."

def test_jump_waiting_prefers_a_blocked_session():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("b", cwd="/x/proseonly")
    sessions.register("c", cwd="/x/blocked")
    _prose(daemon, "b", "just text. ")
    daemon.handle_message(_msg(MsgType.CHOICE, "c",
                               questions=[{"question": "Pick?",
                                           "options": [{"label": "One"}, {"label": "Two"}]}]))
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "c"                  # blocked outranks prose-only

def test_jump_waiting_excludes_current_foreground_backlog():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _prose(daemon, "a", "my own backlog. ")             # only the foreground has backlog
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "a"
    assert queue._items[-1].text == "No session waiting."

def test_jump_waiting_skips_a_muted_background_session():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._stream("b").muted = True
    _prose(daemon, "b", "secret. ")
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "a"
    assert queue._items[-1].text == "No session waiting."

def test_jump_waiting_clears_an_active_pin():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon.handle_message(_msg(MsgType.PIN_TOGGLE, "a"))   # pin a
    sessions.register("b", cwd="/x/backend")
    _prose(daemon, "b", "ready. ")
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.pinned() is None
    assert sessions.foreground() == "b"


# --- waiting earcon (Task 3) --------------------------------------------------

def test_background_prose_fires_one_waiting_earcon():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _prose(daemon, "b", "first. second. third. ")       # b is background
    assert speaker.earcons.count("waiting") == 1         # once per turn, not per sentence

def test_foreground_prose_does_not_fire_waiting():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _prose(daemon, "a", "hello. world. ")
    assert "waiting" not in speaker.earcons

def test_muted_background_does_not_fire_waiting():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._stream("b").muted = True
    _prose(daemon, "b", "x. y. ")
    assert "waiting" not in speaker.earcons

def test_waiting_rearms_after_new_prompt():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _prose(daemon, "b", "turn one. ")
    assert speaker.earcons.count("waiting") == 1
    daemon.handle_message(_msg(MsgType.FLUSH, "b"))      # new prompt to b (still background)
    _prose(daemon, "b", "turn two. ")
    assert speaker.earcons.count("waiting") == 2


# --- session attribution ("who's speaking?") (Task 4) ------------------------

def test_no_folder_prefix_on_the_first_utterance_single_session():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("a", cwd="/x/frontend")
    _prose(daemon, "a", "one. two. ")
    _drain(daemon)                                       # speak loop processes a's items
    assert speaker.spoken == ["one.", "two."]            # never labeled — single session

def test_voice_announces_folder_when_switching_sessions():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("a", cwd="/x/frontend")
    sessions.register("b", cwd="/x/backend")
    _prose(daemon, "a", "alpha. ")
    _drain(daemon)                                       # _last_spoken -> a
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "b", cwd="/x/backend"))
    _prose(daemon, "b", "beta. ")
    _drain(daemon)
    assert "backend. beta." in speaker.spoken            # folder prefix on the switch

def test_jump_preamble_does_not_double_announce_the_folder():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("a", cwd="/x/frontend")
    sessions.register("b", cwd="/x/backend")
    _prose(daemon, "a", "alpha. ")
    _drain(daemon)                                       # _last_spoken -> a
    _prose(daemon, "b", "beta. ")                        # b accumulates
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    _drain(daemon)
    assert "Jumping to backend." in speaker.spoken
    assert "beta." in speaker.spoken                     # the prose itself is NOT prefixed
    assert "backend. beta." not in speaker.spoken        # no double-announce


def test_minqueue_waiting_earcon_fires_at_flush_not_on_chunk():
    # With minqueue>1 the "waiting" earcon must fire from _flush_prose_buffer
    # (when the prose actually reaches the queue), NOT when the chunk arrives.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    config["minqueue"] = 3
    # Send ONE sentence (fewer than minqueue=3) to background session "b".
    # At this point the buffer is not yet flushed, so earcon must NOT have fired.
    _prose(daemon, "b", "one sentence. ")
    assert "waiting" not in speaker.earcons, (
        "earcon fired on chunk production, not at flush: earcons={0}".format(speaker.earcons)
    )
    assert len(stream_queue(daemon, "b")) == 0, (
        "queue should still be empty before flush: {0}".format(len(stream_queue(daemon, "b")))
    )
    # Trigger the turn-boundary flush via the turn_done earcon for session "b".
    daemon.handle_message(_msg(MsgType.EARCON, "b", kind="turn_done"))
    assert speaker.earcons.count("waiting") == 1, (
        "expected exactly 1 waiting earcon after flush, got: {0}".format(speaker.earcons)
    )
    assert len(stream_queue(daemon, "b")) > 0, (
        "queue must be non-empty after flush"
    )


def test_jump_waiting_with_no_foreground_fires_error_earcon():
    # JUMP_WAITING with foreground=None and nothing waiting must play the
    # error earcon only (no "No session waiting." prose, which requires a
    # foreground session to speak through).
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "irrelevant"))
    assert speaker.earcons == ["error"], (
        "expected ['error'], got: earcons={0}".format(speaker.earcons)
    )
    assert speaker.spoken == [], (
        "expected no spoken text, got: {0}".format(speaker.spoken)
    )


def test_backlog_cap_evicts_oldest_prose_and_drops_its_pending_heard():
    # A capped background stream must drop the evicted item's _pending_heard entry,
    # else the cap bounds the queue but leaks the pending dict (defeating the bound).
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon._backlog_cap = 2
    daemon._streams.clear()                  # rebuild streams under the small cap
    entries = [daemon.history.record("bg", "prose", "p{0}".format(i)) for i in range(3)]
    for i, e in enumerate(entries):
        daemon._enqueue("bg", "prose", "p{0}".format(i), False, entry=e)
    bg = daemon._stream("bg").queue
    assert len(bg) == 2                                    # capped
    # Confirm oldest item was evicted: entries[0] is no longer tracked in _pending_heard
    # (HistoryEntry has no .id; we key off the entry object as a value in the pending dict)
    assert entries[0] not in daemon._pending_heard.values()  # evicted entry's marker dropped (no leak)
    assert entries[1] in daemon._pending_heard.values()      # survivor retained
    assert entries[2] in daemon._pending_heard.values()      # survivor retained


def test_attribution_survives_pause_on_switch():
    # Regression: if a PAUSE interrupts the first post-switch utterance, the
    # _last_spoken_session commit must be rolled back so the resumed utterance
    # still carries the folder prefix.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("a", cwd="/x/frontend")
    sessions.register("b", cwd="/x/backend")
    # Prime _last_spoken_session on "a"
    _prose(daemon, "a", "alpha. ")
    _drain(daemon)
    # Switch foreground to b, enqueue prose that should be attributed "backend."
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "b", cwd="/x/backend"))
    _prose(daemon, "b", "beta. ")
    # Monkeypatch speaker so that the utterance from b causes a mid-speech pause,
    # exactly mirroring test_pause_during_speech_requeues_interrupted_item.
    original_speak = speaker.speak

    def interrupted_speak(text, cancel_epoch=None):
        speaker.spoken.append(text)
        daemon._paused.set()   # pause arrives mid-synthesis
        return False           # not completed

    speaker.speak = interrupted_speak
    daemon._speak_loop_once()   # pops "beta." (prefixed as "backend. beta."), but interrupted
    # Restore normal speak and clear the paused flag (direct clear, no PAUSE handler
    # so we don't inject "Resumed." into spoken)
    daemon._paused.clear()
    speaker.spoken.clear()      # only care about what the RESUMED speak produces
    speaker.speak = lambda t, cancel_epoch=None: (speaker.spoken.append(t) or True)
    daemon._speak_loop_once()   # re-pops the re-queued item; must be prefixed again
    assert "backend. beta." in speaker.spoken, (
        "Attribution dropped after pause-on-switch: got {0}".format(speaker.spoken)
    )
