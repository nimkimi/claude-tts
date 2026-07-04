from tests.daemon_helpers import make_daemon


def _two(daemon, sessions):
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")


def test_cycle_next_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    _two(daemon, sessions); sessions.set_foreground("A")
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    assert speaker.pitches == []


def test_cycle_prev_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    _two(daemon, sessions); sessions.set_foreground("A")
    daemon.handle_message({"type": "cycle_session", "direction": "prev"})
    assert speaker.pitches == []


def test_cycle_under_two_sessions_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    assert speaker.pitches == []          # error case: no directional cue


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


def test_answer_allow_chirps_up_deny_chirps_down():
    import threading
    daemon, q, speaker, sessions, _ = make_daemon()
    sessions.set_foreground("S1", cwd="/x/a")
    daemon._pending_decisions["S1"] = {"event": threading.Event(), "behavior": None}
    daemon.handle_message({"type": "answer_permission", "behavior": "allow"})
    daemon._pending_decisions["S1"] = {"event": threading.Event(), "behavior": None}
    daemon.handle_message({"type": "answer_permission", "behavior": "deny"})
    assert speaker.pitches == ["up", "down"]


def test_answer_with_no_pending_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon()
    sessions.set_foreground("A", cwd="/x/a")
    daemon.handle_message({"type": "answer_permission", "behavior": "allow"})
    assert speaker.pitches == []          # error case (no pending): no directional cue
