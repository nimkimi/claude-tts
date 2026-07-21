from tests.daemon_helpers import make_daemon


def _two(daemon, sessions):
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")


def test_chooser_step_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    _two(daemon, sessions); sessions.set_foreground("A")
    daemon.handle_message({"type": "chooser_step", "direction": "next"})
    daemon.handle_message({"type": "chooser_step", "direction": "prev"})
    assert speaker.pitches == []


def test_chooser_commit_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    _two(daemon, sessions); sessions.set_foreground("A")
    daemon.handle_message({"type": "chooser_step", "direction": "next"})
    daemon.handle_message({"type": "chooser_commit"})
    assert speaker.pitches == []


def test_chooser_error_paths_do_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message({"type": "chooser_digit", "digit": 9})   # unknown number
    assert speaker.pitches == []          # error case: earcon, never a directional cue


def _seed(daemon, s="fg"):
    h = daemon.history
    h.record(s, "prose", "m0"); h.end_message(s)
    h.record(s, "prose", "m1")


def test_nav_next_prev_do_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    _seed(daemon)
    daemon.handle_message({"type": "nav", "to": "next", "session": "fg"})
    daemon.handle_message({"type": "nav", "to": "prev", "session": "fg"})
    assert speaker.pitches == []


def test_nav_first_last_do_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    _seed(daemon)
    daemon.handle_message({"type": "nav", "to": "first", "session": "fg"})
    daemon.handle_message({"type": "nav", "to": "last", "session": "fg"})
    assert speaker.pitches == []


def test_nav_response_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    h = daemon.history
    h.record("fg", "prose", "t0"); h.end_message("fg"); h.start_turn("fg")
    h.record("fg", "prose", "t1")
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})
    daemon.handle_message({"type": "nav", "to": "next_response", "session": "fg"})
    assert speaker.pitches == []


def test_answer_allow_deny_do_not_call_pitch_directly():
    # the directional chirp now binds as the confirm's prelude (see
    # tests/test_decisions_answer.py), not a direct pitch() call
    import threading
    daemon, q, speaker, sessions, _ = make_daemon()
    sessions.set_foreground("S1", cwd="/x/a")
    daemon._pending_decisions["S1"] = {"event": threading.Event(), "behavior": None}
    daemon.handle_message({"type": "answer_permission", "behavior": "allow"})
    daemon._pending_decisions["S1"] = {"event": threading.Event(), "behavior": None}
    daemon.handle_message({"type": "answer_permission", "behavior": "deny"})
    assert speaker.pitches == []


def test_answer_with_no_pending_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon()
    sessions.set_foreground("A", cwd="/x/a")
    daemon.handle_message({"type": "answer_permission", "behavior": "allow"})
    assert speaker.pitches == []          # error case (no pending): no directional cue
