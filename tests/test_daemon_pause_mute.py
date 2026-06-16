"""Alt+Down play/pause (global) and Alt+Up sticky per-session mute."""
from tests.daemon_helpers import make_daemon


def test_pause_toggle_cancels_current():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    assert not daemon._paused.is_set()
    daemon.handle_message({"type": "pause", "session": "fg"})
    assert daemon._paused.is_set() and speaker.cancels == 1
    daemon.handle_message({"type": "pause", "session": "fg"})
    assert not daemon._paused.is_set()


def test_speak_loop_holds_while_paused_then_resumes():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._enqueue("fg", "prose", "hello", False)
    daemon._paused.set()
    daemon._wake.set()                    # so the pause wait returns at once
    daemon._speak_loop_once()
    assert speaker.spoken == [] and len(queue) == 1   # held, not consumed
    daemon._paused.clear()
    daemon._speak_loop_once()
    assert speaker.spoken == ["hello"]


def test_mute_drops_speech_but_unmute_resumes():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "mute", "session": "fg"})
    assert "fg" in daemon._muted_sessions
    daemon._speak_loop_once()             # speak the "Session muted." confirmation
    speaker.spoken.clear()
    daemon._enqueue("fg", "prose", "secret", False)
    daemon._speak_loop_once()
    assert speaker.spoken == []           # real content: dropped, not spoken
    daemon.handle_message({"type": "mute", "session": "fg"})
    assert "fg" not in daemon._muted_sessions
    daemon._speak_loop_once()             # "Session unmuted."
    speaker.spoken.clear()
    daemon._enqueue("fg", "prose", "hello", False)
    daemon._speak_loop_once()
    assert speaker.spoken == ["hello"]


def test_muting_flushes_pending_user_content():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._enqueue("fg", "prose", "queued", False)
    daemon.handle_message({"type": "mute", "session": "fg"})
    texts = []
    while True:
        it = queue.pop_next()
        if it is None:
            break
        texts.append(it.text)
    assert "queued" not in texts and texts == ["Session muted."]


def test_mute_speaks_muted_and_unmuted_confirmations():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "mute", "session": "fg"})
    daemon._speak_loop_once()                 # "Session muted." is mute_exempt
    assert "Session muted." in speaker.spoken
    daemon._enqueue("fg", "prose", "secret", False)
    daemon._speak_loop_once()
    assert "secret" not in speaker.spoken     # real content still muted
    daemon.handle_message({"type": "mute", "session": "fg"})
    daemon._speak_loop_once()
    assert "Session unmuted." in speaker.spoken


def test_pause_replays_interrupted_item_on_resume():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._enqueue("fg", "prose", "interrupted sentence", False)
    item = queue.pop_next()
    daemon._current_item = item               # pretend it's mid-play
    daemon.handle_message({"type": "pause", "session": "fg"})
    assert daemon._paused.is_set() and daemon._paused_item is item
    daemon.handle_message({"type": "pause", "session": "fg"})   # resume
    assert not daemon._paused.is_set()
    assert queue.pop_next() is item           # re-queued at the front to pick back up


def test_new_prompt_clears_pause():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._paused.set(); daemon._paused_item = object()
    daemon.handle_message({"type": "flush", "session": "fg"})
    assert not daemon._paused.is_set() and daemon._paused_item is None
