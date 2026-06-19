"""Pin-toggle hotkey: pin the current session's voice; toggle again to unpin."""
from sonari.protocol import MsgType, PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _prose(session, delta, index, final):
    return {
        "v": PROTOCOL_VERSION,
        "type": MsgType.PROSE,
        "session": session,
        "delta": delta,
        "index": index,
        "final": final,
    }


def test_pin_toggle_pins_current_and_speaks_folder():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/home/me/myapp")
    daemon.handle_message({"type": "pin_toggle", "session": "fg"})
    assert sessions.pinned() == "fg"
    daemon._speak_loop_once()
    assert speaker.spoken == ["Pinned myapp."]


def test_pin_toggle_again_unpins_and_says_auto():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/home/me/myapp")
    daemon.handle_message({"type": "pin_toggle", "session": "fg"})   # pin
    daemon._speak_loop_once()
    speaker.spoken.clear()
    daemon.handle_message({"type": "pin_toggle", "session": "fg"})   # unpin
    assert sessions.pinned() is None
    daemon._speak_loop_once()
    assert speaker.spoken == ["Auto."]


def test_pinned_session_keeps_voice_when_another_submits():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "pin_toggle", "session": "fg"})  # pin fg
    daemon.handle_message({"type": "set_foreground", "session": "bg"})
    assert sessions.foreground() == "fg"
    assert sessions.is_foreground("fg") is True
    assert sessions.is_foreground("bg") is False


def test_pinned_session_end_falls_back_to_auto():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "pin_toggle", "session": "fg"})
    daemon.handle_message({"type": "session_end", "session": "fg"})
    assert sessions.pinned() is None
    assert sessions.foreground() is None


def test_set_foreground_message_carries_cwd_into_announcement():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message({"type": "set_foreground", "session": "s1", "cwd": "/x/proj"})
    daemon.handle_message({"type": "pin_toggle", "session": "s1"})
    daemon._speak_loop_once()
    assert speaker.spoken == ["Pinned proj."]


def test_pin_toggle_with_no_session_beeps_error_only():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message({"type": "pin_toggle", "session": ""})
    assert sessions.pinned() is None
    assert speaker.earcons == ["error"]      # only the error earcon, nothing else
    assert speaker.spoken == []


def test_pinned_session_keeps_voice_so_bg_prose_accumulates_separately():
    """End-to-end through the daemon's PROSE handler: the pin steers WHICH stream the
    voice plays. While fg is pinned, bg's prose accumulates in bg's own stream (the
    Stage 2 flip — no longer dropped), and the pinned session's prose lands in the
    foreground stream the loop plays. This proves the pin keeps is_foreground on fg,
    so _enqueue + the speak loop route accordingly — not just SessionManager state."""
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "pin_toggle", "session": "fg"})     # pin fg
    daemon._speak_loop_once()                  # drain the "Pinned." announcement
    daemon.handle_message({"type": "set_foreground", "session": "bg"})  # bg submits a prompt
    daemon.handle_message(_prose("bg", "Background sentence here. ", 0, False))
    assert len(queue) == 0                     # not in the pinned/foreground stream
    assert len(stream_queue(daemon, "bg")) == 1   # accumulates in bg's own stream, not dropped
    daemon.handle_message(_prose("fg", "Foreground sentence here. ", 0, False))
    assert len(queue) == 1                     # the pinned session still speaks
    assert queue.pop_next().session == "fg"
