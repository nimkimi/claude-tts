"""End-of-turn earcons (turn_done/ready) are queued behind speech so the beep
follows the reading instead of cutting it; decision/error earcons stay instant."""
from tests.daemon_helpers import make_daemon


def test_turn_done_earcon_plays_after_pending_speech():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._enqueue("fg", "prose", "last sentence", False)
    daemon.handle_message({"type": "earcon", "kind": "turn_done"})
    assert speaker.earcons == []                 # NOT played instantly
    daemon._speak_loop_once()
    assert speaker.spoken == ["last sentence"] and speaker.earcons == []
    daemon._speak_loop_once()
    assert speaker.earcons == ["turn_done"]      # plays after the speech


def test_decision_earcon_is_instant():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._enqueue("fg", "prose", "prose", False)
    daemon.handle_message({"type": "earcon", "kind": "choice"})
    assert speaker.earcons == ["choice"]         # instant alert, not queued


def test_turn_done_earcon_fires_even_when_muted():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "mute", "session": "fg"})
    daemon.handle_message({"type": "earcon", "kind": "turn_done"})
    # drain the queue: muted speech drops, but the mute-exempt earcon still plays
    for _ in range(5):
        daemon._speak_loop_once()
    assert "turn_done" in speaker.earcons
