from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon


def _drain(queue):
    items = []
    while True:
        it = queue.pop_next()
        if it is None:
            break
        items.append(it)
    return items


def test_os_focus_message_resolves_focused_session():
    daemon, _q, _s, sessions, _c = make_daemon(foreground="a")
    sessions.register("a", cwd="/work/frontend")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    daemon.handle_message({"type": "os_focus",
                           "term_program": "Apple_Terminal", "tty": "/dev/ttys001"})
    assert sessions.focused_session() == "a"


def test_os_focus_false_message_clears_focus():
    daemon, _q, _s, sessions, _c = make_daemon(foreground="a")
    sessions.register("a", cwd="/work/frontend")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    daemon.handle_message({"type": "os_focus",
                           "term_program": "Apple_Terminal", "tty": "/dev/ttys001"})
    daemon.handle_message({"type": "os_focus", "focused": False})
    assert sessions.focused_session() is None


def _seed(daemon, session):
    h = daemon.history
    h.record(session, "prose", session + "-m0"); h.end_message(session)
    h.record(session, "prose", session + "-m1")


def test_nav_targets_os_focused_session_not_foreground():
    # B last-prompted (foreground/voice), but A's terminal is OS-focused.
    daemon, _q, _s, sessions, _c = make_daemon(foreground="a")
    sessions.register("a", cwd="/work/frontend")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sessions.register("b", cwd="/work/api")
    sessions.set_identity("b", Identity(term_program="Apple_Terminal", tty="/dev/ttys002"))
    sessions.set_foreground("b")
    _seed(daemon, "a")
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")

    daemon.handle_message({"type": "nav", "to": "prev"})

    a_texts = [it.text for it in _drain(daemon._stream("a").queue)]
    assert a_texts and a_texts[-1] == "a-m1"          # A was navigated
    assert _drain(daemon._stream("b").queue) == []     # B untouched
    assert sessions.foreground() == "a"                # voice moved to A
    assert a_texts[0] == "frontend."                   # cross-session folder cue, first


def test_nav_falls_back_to_foreground_when_no_os_focus():
    daemon, queue, _s, sessions, _c = make_daemon(foreground="fg")
    _seed(daemon, "fg")
    # no set_os_focus -> focused_session() is None
    daemon.handle_message({"type": "nav", "to": "prev"})
    # _seed records 2 messages (m0 ended, m1 live); prev steps to m0 and seek-and-plays
    # forward from there, so both messages land in the queue.
    assert [it.text for it in _drain(queue)] == ["fg-m0", "fg-m1"]
    assert sessions.foreground() == "fg"


def test_within_focused_session_nav_no_voice_move_no_cue():
    daemon, queue, _s, sessions, _c = make_daemon(foreground="fg")
    sessions.register("fg", cwd="/work/frontend")
    sessions.set_identity("fg", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sessions.set_foreground("fg")
    sessions.pin_toggle()                              # pin fg
    _seed(daemon, "fg")
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")  # focus == voice
    daemon.handle_message({"type": "nav", "to": "prev"})
    texts = [it.text for it in _drain(queue)]
    # _seed records 2 messages; prev from latest steps to m0 and seek-and-plays forward.
    # Exact-equality confirms no "frontend." cue was prepended (within-session, not crossed).
    assert texts == ["fg-m0", "fg-m1"]
    assert sessions.pinned() == "fg"                   # within-session nav preserves the pin


def test_cross_session_nav_overrides_pin():
    daemon, _q, _s, sessions, _c = make_daemon(foreground="b")
    sessions.register("a", cwd="/work/frontend")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sessions.register("b", cwd="/work/api")
    sessions.set_foreground("b"); sessions.pin_toggle()   # pin b
    assert sessions.pinned() == "b"
    _seed(daemon, "a")
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")
    daemon.handle_message({"type": "nav", "to": "prev"})
    assert sessions.foreground() == "a"
    assert sessions.pinned() is None                   # cross-session nav clears the pin (like jump)
