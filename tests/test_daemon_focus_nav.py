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
    _seed(daemon, "fg")
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")  # focus == voice
    daemon.handle_message({"type": "nav", "to": "prev"})
    texts = [it.text for it in _drain(queue)]
    # _seed records 2 messages; prev from latest steps to m0 and seek-and-plays forward.
    # Exact-equality confirms no "frontend." cue was prepended (within-session, not crossed).
    assert texts == ["fg-m0", "fg-m1"]
    assert sessions.foreground() == "fg"               # within-session nav keeps the voice on fg


def test_jump_waiting_diagnostic_identity_none(capsys):
    # jump_waiting with a target whose identity is None should emit
    # a diagnostic line with identity=none.
    daemon, queue, _s, sessions, _c = make_daemon(foreground="a")
    sessions.register("a", cwd="/work/a")
    _seed(daemon, "a")

    sessions.register("b", cwd="/work/b")
    # Don't set identity for b -> identity will be None
    # Enqueue a message to make b a valid jump target.
    daemon._enqueue("b", "prose", "test message", False)

    sessions.set_foreground("a")

    # Trigger jump_waiting.
    daemon.handle_message({"type": "jump_waiting"})

    captured = capsys.readouterr()
    # Should emit diagnostic with identity=none (since b's identity is None).
    assert "sonari[focus]: jump_waiting target=b identity=none will_raise=" in captured.err


def test_jump_waiting_diagnostic_identity_present(capsys):
    # jump_waiting with a target whose identity is present (has tty) should emit
    # a diagnostic line with identity=present.
    daemon, queue, _s, sessions, _c = make_daemon(foreground="a")
    sessions.register("a", cwd="/work/a")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    _seed(daemon, "a")

    sessions.register("b", cwd="/work/b")
    sessions.set_identity("b", Identity(term_program="Apple_Terminal", tty="/dev/ttys002"))
    daemon._enqueue("b", "prose", "test message", False)

    sessions.set_foreground("a")

    daemon.handle_message({"type": "jump_waiting"})

    captured = capsys.readouterr()
    # Should emit diagnostic with identity=present (since b's identity has a non-empty tty).
    assert "sonari[focus]: jump_waiting target=b identity=present will_raise=" in captured.err


def test_jump_waiting_diagnostic_identity_tty_empty(capsys):
    # jump_waiting with a target whose identity exists but tty is empty should emit
    # a diagnostic line with identity=tty-empty.
    daemon, queue, _s, sessions, _c = make_daemon(foreground="a")
    sessions.register("a", cwd="/work/a")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    _seed(daemon, "a")

    sessions.register("b", cwd="/work/b")
    # Identity with empty tty.
    sessions.set_identity("b", Identity(term_program="Apple_Terminal", tty=""))
    daemon._enqueue("b", "prose", "test message", False)

    sessions.set_foreground("a")

    daemon.handle_message({"type": "jump_waiting"})

    captured = capsys.readouterr()
    # Should emit diagnostic with identity=tty-empty (since b's identity tty is empty).
    assert "sonari[focus]: jump_waiting target=b identity=tty-empty will_raise=" in captured.err
