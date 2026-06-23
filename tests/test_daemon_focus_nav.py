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
