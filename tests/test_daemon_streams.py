from sonari.protocol import MsgType, PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(mtype, session, **extra):
    d = {"v": PROTOCOL_VERSION, "type": mtype, "session": session}
    d.update(extra)
    return d


def _prose(daemon, session, text, index=0, final=False):
    daemon.handle_message(_msg(MsgType.PROSE, session, delta=text, index=index, final=final))


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


# --- jump_waiting handler (Task 2) -------------------------------------------

def test_jump_waiting_switches_to_background_and_announces_folder():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("b", cwd="/work/backend")
    _prose(daemon, "b", "All done. ")                  # b accumulates in the background
    assert len(stream_queue(daemon, "b")) >= 1
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "b"
    assert speaker.cancels == 1                          # cut-on-switch
    # No identity set -> will_attempt is False -> the bring-forward cue is appended.
    # (On macOS the lazy _raise() builds a real MacRaiseBackend; the cue is here
    # because identity is None, not because of any Noop backend.)
    assert stream_queue(daemon, "b")._items[0].text == \
        "Jumping to backend. Bring it forward to type."

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

def test_jump_waiting_skips_a_stopped_background_session():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._stream("b").stopped = True
    _prose(daemon, "b", "secret. ")
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "a"
    assert queue._items[-1].text == "No session waiting."

# --- turn-completion ding (Task 3, SP3: waiting retired) ----------------------

def test_muted_session_dings_on_turn_completion():
    # A muted (stopped) BACKGROUND session still dings when its turn completes
    # (R7:192-193 "its output piles, dinging on turn-completion"). The retired
    # `waiting` gate had `not st.stopped` and wrongly silenced it.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._stream("b").stopped = True
    daemon.handle_message(_msg(MsgType.EARCON, "b", kind="turn_done"))
    assert speaker.earcons == ["turn_done"]


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
    # No identity set -> will_attempt is False -> the bring-forward cue is appended.
    # (On macOS the lazy _raise() builds a real MacRaiseBackend; the cue is here
    # because identity is None, not because of any Noop backend.)
    assert "Jumping to backend. Bring it forward to type." in speaker.spoken
    assert "beta." in speaker.spoken                     # the prose itself is NOT prefixed
    assert "backend. beta." not in speaker.spoken        # no double-announce


def test_minqueue_prose_flushes_at_turn_done_no_waiting():
    # minqueue>1: a sub-threshold message is still read at the turn boundary (the
    # turn_done flush). The retired `waiting` earcon no longer fires; a background
    # session's turn_done IS its "landed" ding.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    config["minqueue"] = 3
    _prose(daemon, "b", "one sentence. ")                # bg, below threshold
    assert "waiting" not in speaker.earcons
    assert len(stream_queue(daemon, "b")) == 0           # not flushed yet
    daemon.handle_message(_msg(MsgType.EARCON, "b", kind="turn_done"))
    assert speaker.earcons == ["turn_done"]              # bg dings at completion (req 16)
    assert len(stream_queue(daemon, "b")) > 0            # ... and the buffered prose flushed


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


def test_attribution_survives_stop_on_switch():
    # Regression: if a STOP interrupts the first post-switch utterance, the
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
    # Monkeypatch speaker so that the utterance from b causes a mid-speech stop,
    # exactly mirroring test_stop_during_speech_requeues_interrupted_item.
    original_speak = speaker.speak

    def interrupted_speak(text, cancel_epoch=None):
        speaker.spoken.append(text)
        daemon._stream("b").stopped = True   # stop arrives mid-synthesis
        return False                         # not completed

    speaker.speak = interrupted_speak
    daemon._speak_loop_once()   # pops "beta." (prefixed as "backend. beta."), but interrupted
    # Restore normal speak and clear the stopped flag (direct clear, no STOP_SESSION
    # handler so we don't inject "Resumed." into spoken)
    daemon._stream("b").stopped = False
    speaker.spoken.clear()      # only care about what the RESUMED speak produces
    speaker.speak = lambda t, cancel_epoch=None: (speaker.spoken.append(t) or True)
    daemon._speak_loop_once()   # re-pops the re-queued item; must be prefixed again
    assert "backend. beta." in speaker.spoken, (
        "Attribution dropped after stop-on-switch: got {0}".format(speaker.spoken)
    )


# ---------------------------------------------------------------------------
# Host lock-model pins (Task 3.1)
# ---------------------------------------------------------------------------

def test_daemon_state_is_session_state_wrapping_same_lock():
    # _state must be a SessionState whose ._lock is the SAME object as daemon._lock.
    # SessionState(self._lock) is constructed in __init__, so both sides point at
    # the same threading.Lock — behavior is byte-identical to the direct lock usage.
    from sonari.daemon.state import SessionState
    daemon, *_ = make_daemon()
    assert isinstance(daemon._state, SessionState)
    assert daemon._state._lock is daemon._lock


def test_daemon_ctx_is_ctx_pointing_at_daemon():
    # _ctx must be a Ctx whose .host is the daemon itself.
    from sonari.daemon.context import Ctx
    daemon, *_ = make_daemon()
    assert isinstance(daemon._ctx, Ctx)
    assert daemon._ctx.host is daemon


# ---------------------------------------------------------------------------
# SP1-B2 regression pin: speak loop plays speaker() stream (not foreground())
# ---------------------------------------------------------------------------

def test_speak_loop_plays_speaker_stream():
    """Pin: the loop pops from speaker()'s stream, not foreground()'s.

    In SP1 speaker() == foreground() always, so this passes before and after
    the B2 repoint — that is correct and expected for a behavior-preserving
    refactor pin.  SP2 will diverge speaker() from foreground(), at which
    point this test becomes the gate that forces the loop to follow speaker().
    """
    daemon, queue, speaker, sessions, config = make_daemon(foreground="s0")
    sessions.register("s1", cwd="/x/s1")
    sessions.focus("s1")                       # deliberate setter -> speaker() == "s1"
    daemon._enqueue("s1", "prose", "hello from s1", False)
    daemon._speak_loop_once()
    # substring-tolerant: _attributed_text may prepend a folder label on speaker change
    assert any(s and "hello from s1" in s for s in speaker.spoken)
