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
    daemon._enqueue("fg", "prose", "secret", False)
    daemon._speak_loop_once()
    assert speaker.spoken == []           # muted: dropped, not spoken
    daemon.handle_message({"type": "mute", "session": "fg"})
    assert "fg" not in daemon._muted_sessions
    daemon._enqueue("fg", "prose", "hello", False)
    daemon._speak_loop_once()
    assert speaker.spoken == ["hello"]


def test_muting_flushes_pending_queue():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._enqueue("fg", "prose", "queued", False)
    daemon.handle_message({"type": "mute", "session": "fg"})
    assert len(queue) == 0
