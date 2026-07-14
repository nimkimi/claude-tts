"""Where am I (⌃⌘W) — terse spoken status with barge-in + interjection-resume (§7)."""
from tests.daemon_helpers import make_daemon


def test_where_am_i_speaks_terse_status():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/Users/me/work")
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice: work 1, Playing. 0 waiting, 0 muted."]
    assert speaker.cancels == 1                   # barge-in fires (always-confirm)


def test_where_am_i_unknown_folder_says_unknown_session():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "where_am_i", "session": "fg"})   # no cwd -> folder None
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice: Unknown session 1, Playing. 0 waiting, 0 muted."]


def test_where_am_i_reports_stopped_state():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon._stream("fg").stopped = True
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()                     # pause_exempt cue voices even when stopped
    assert speaker.spoken == ["Voice: work 1, Stopped. 0 waiting, 0 muted."]


def test_where_am_i_counts_waiting_background_sessions():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon._enqueue("bg1", "prose", "x", False)              # waiting
    daemon._enqueue("bg2", "prose", "y", False)              # waiting
    daemon._stream("bg3").stopped = True                     # stopped -> NOT counted
    daemon._enqueue("bg3", "prose", "z", False)
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice: work 1, Playing. 2 waiting, 1 muted."]


def test_where_am_i_no_foreground_errors():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message({"type": "where_am_i"})
    assert speaker.earcons == ["error"]


def test_where_am_i_with_nothing_in_flight_still_barges_in():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    assert daemon._current_item is None
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    assert speaker.cancels == 1                   # §7 barge-in even with nothing playing
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice: work 1, Playing. 0 waiting, 0 muted."]


def test_where_am_i_resumes_interrupted_item_after_the_status_cue():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon._enqueue("fg", "prose", "interrupted sentence", False)
    fired = {"done": False}

    def interrupting(text, cancel_epoch=None):
        speaker.spoken.append(text)
        if not fired["done"]:
            fired["done"] = True
            daemon.handle_message({"type": "where_am_i", "session": "fg"})  # ⌃⌘W mid-utterance
            return False                          # ... and cancelled it
        return True

    speaker.speak = interrupting
    daemon._speak_loop_once()                     # speaks the item; ⌃⌘W barges in, cancels
    assert speaker.spoken == ["interrupted sentence"]
    assert daemon._current_item is None
    daemon._speak_loop_once()                     # the status cue plays FIRST
    assert speaker.spoken[-1] == "Voice: work 1, Playing. 0 waiting, 0 muted."
    daemon._speak_loop_once()                     # then reading resumes from the item's start
    assert speaker.spoken[-1] == "interrupted sentence"


def test_where_am_i_preserves_heard_marker_of_the_resumed_item():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    entry = daemon.history.record("fg", "prose", "hello")
    daemon._enqueue("fg", "prose", "hello", False, entry=entry)

    def interrupting(text, cancel_epoch=None):
        speaker.spoken.append(text)
        daemon.handle_message({"type": "where_am_i", "session": "fg"})
        speaker.speak = lambda t, cancel_epoch=None: (speaker.spoken.append(t) or True)
        return False

    speaker.speak = interrupting
    daemon._speak_loop_once()                     # hello interrupted by ⌃⌘W
    assert entry.heard is False
    assert entry in daemon._pending_heard.values()   # carried onto the re-queued item
    daemon._speak_loop_once()                     # status cue
    daemon._speak_loop_once()                     # hello resumes -> completes -> marked heard
    assert entry.heard is True
