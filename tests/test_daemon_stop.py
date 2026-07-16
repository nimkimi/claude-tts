"""Per-session stop/start (⌃⌘S) and stop-all (⌃⌘M) — the per-session control core."""
from tests.daemon_helpers import make_daemon


def test_stop_toggles_the_foreground_stopped_flag():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    assert daemon._stream("fg").stopped is False
    daemon.handle_message({"type": "stop_session", "session": "fg"})
    assert daemon._stream("fg").stopped is True
    daemon.handle_message({"type": "stop_session", "session": "fg"})
    assert daemon._stream("fg").stopped is False


def test_stop_holds_loop_voices_cue_and_resume_drops_pile_quietly():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._enqueue("fg", "prose", "hello", False)
    daemon.handle_message({"type": "stop_session", "session": "fg"})
    daemon._speak_loop_once()                  # stopped: only the pause-exempt cue voices
    assert speaker.spoken == ["Stopped."]
    daemon._speak_loop_once()                  # nothing else exempt -> held
    assert speaker.spoken == ["Stopped."]
    assert "hello" not in speaker.spoken and len(queue) == 1   # backlog retained
    daemon.handle_message({"type": "stop_session", "session": "fg"})   # resume (D2 quiet resume)
    daemon._speak_loop_once()
    assert speaker.spoken == ["Stopped.", "Resumed."]           # confirmation only
    daemon._speak_loop_once()
    assert speaker.spoken == ["Stopped.", "Resumed."]           # pre-start pile dropped, NOT replayed (D2)
    assert len(queue) == 0                                      # "hello" cleared, not queued


def test_stop_during_speech_requeues_interrupted_item():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._enqueue("fg", "prose", "interrupted sentence", False)

    def interrupted(text, cancel_epoch=None):
        speaker.spoken.append(text)
        daemon._stream("fg").stopped = True    # stop arrived mid-utterance
        return False                           # ... and cancelled it

    speaker.speak = interrupted
    daemon._speak_loop_once()
    assert speaker.spoken == ["interrupted sentence"]
    assert daemon._current_item is None
    assert len(queue) == 1 and queue.pop_next().text == "interrupted sentence"


def test_stop_preserves_heard_marker_for_the_replay():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    entry = daemon.history.record("fg", "prose", "hello")
    daemon._enqueue("fg", "prose", "hello", False, entry=entry)

    def interrupted(text, cancel_epoch=None):
        speaker.spoken.append(text)
        daemon._stream("fg").stopped = True
        return False

    speaker.speak = interrupted
    daemon._speak_loop_once()
    assert entry.heard is False
    assert entry in daemon._pending_heard.values()   # preserved for the replay
    daemon._stream("fg").stopped = False
    speaker.speak = lambda t, cancel_epoch=None: (speaker.spoken.append(t) or True)
    daemon._speak_loop_once()
    assert entry.heard is True


def test_stopped_session_does_not_auto_read_on_landing():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    daemon._enqueue("B", "prose", "b content", False)
    daemon._stream("B").stopped = True
    sessions.set_foreground("B")               # "land on" B
    daemon._speak_loop_once()
    assert speaker.spoken == []                # stopped -> held, no auto-read


def test_stop_is_sticky_across_a_new_prompt():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._stream("fg").stopped = True
    daemon.handle_message({"type": "flush", "session": "fg"})   # a new prompt
    assert daemon._stream("fg").stopped is True                 # NOT auto-resumed


def test_stop_all_stops_every_session_and_confirms():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    daemon._enqueue("A", "prose", "a", False)
    daemon._enqueue("B", "prose", "b", False)
    daemon.handle_message({"type": "stop_all", "session": "A"})
    assert daemon._stream("A").stopped is True and daemon._stream("B").stopped is True
    daemon._speak_loop_once()
    assert speaker.spoken == ["All stopped."]


def test_stop_all_is_one_way_each_session_returns_via_its_own_stop_key():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    daemon._enqueue("B", "prose", "b", False)
    daemon.handle_message({"type": "stop_all", "session": "A"})
    sessions.set_foreground("B")
    daemon._speak_loop_once()
    assert "b" not in speaker.spoken          # landing on B does NOT auto-read it
    daemon.handle_message({"type": "stop_session", "session": "B"})   # ⌃⌘S brings B back (D2 quiet resume)
    daemon._speak_loop_once()                 # "Resumed."
    daemon._speak_loop_once()                 # pre-start pile dropped, not replayed (D2)
    assert "b" not in speaker.spoken          # D2: quiet resume does not flood the backlog
    assert "Resumed." in speaker.spoken
