# tests/test_whereami_v2.py (new)
"""Where-am-i grammar v2 (spec 2026-07-16-sonari-whereami-grammar-v2, owner-
delegated): sentence entries, positive unity ("Voice and keyboard:"), inline
decision role word sorted first, age as the word "stale", quiet collapse,
diverged Keyboard clause carrying its own pile."""
from sonari.protocol import PROTOCOL_VERSION
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _where(daemon, speaker, session):
    daemon.handle_message(_msg("where_am_i", session))
    daemon._speak_loop_once()
    return speaker.spoken[-1]


def _liveness(monkeypatch):
    from sonari import ttyutil
    monkeypatch.setattr(ttyutil, "tty_alive", lambda tty: True)


def _focus_on(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))
    sessions.set_os_focus(term_program="Apple_Terminal", tty=tty)


def _spec_roster():
    """The spec's worked-example roster: 1 board (2 unheard), 2 jam (pointers,
    playing), 3 hackimi (5 unheard, stale), 4 docs (quiet), 5 edrum (muted),
    6 syncward (decision + 3 unheard). Clock staged so ONLY hackimi is stale."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="board")
    sessions.register("board", cwd="/x/board")
    now = [1000.0]
    daemon.history._clock = lambda: now[0]
    for s in ("jam", "hackimi", "docs", "edrum", "syncward"):
        sessions.register(s, cwd="/x/" + s)
    sessions.set_foreground("jam", cwd="/x/jam")
    sessions.set_speaker("jam")
    for i in range(5):                              # hackimi's pile: old
        daemon.history.record("hackimi", "prose", "h{0}.".format(i))
    now[0] = 1900.0                                 # board/syncward piles: fresh
    for i in range(2):
        daemon.history.record("board", "prose", "b{0}.".format(i))
    for i in range(3):
        daemon.history.record("syncward", "prose", "s{0}.".format(i))
    daemon._pending_decisions["syncward"] = {"text": "Bash: rm x"}
    daemon._stream("edrum").stopped = True
    now[0] = 1950.0                                 # hackimi age 950 > 900; others 50
    return daemon, queue, speaker, sessions


def test_unified_main_oracle_byte_exact():
    daemon, queue, speaker, sessions = _spec_roster()
    assert _where(daemon, speaker, "jam") == (
        "Voice and keyboard: jam 2, playing. "
        "Also: 6 syncward, decision, 3 unheard. 1 board, 2 unheard. "
        "3 hackimi, 5 unheard, stale. 5 edrum, muted. Plus one quiet."
    )


def test_diverged_oracle_keyboard_carries_its_own_pile(monkeypatch):
    _liveness(monkeypatch)
    daemon, queue, speaker, sessions = _spec_roster()
    _focus_on(sessions, "hackimi", "/dev/ttysH")    # keyboard drifts to hackimi
    assert _where(daemon, speaker, "jam") == (
        "Voice: jam 2, playing. Keyboard: hackimi 3, 5 unheard, stale. "
        "Also: 6 syncward, decision, 3 unheard. 1 board, 2 unheard. "
        "5 edrum, muted. Plus one quiet."
    )


def test_all_other_sessions_quiet_says_all_quiet():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("fg", cwd="/x/fg")
    sessions.register("b", cwd="/x/b")
    sessions.register("c", cwd="/x/c")
    assert _where(daemon, speaker, "fg") == "Voice and keyboard: fg 1, playing. All quiet."


def test_zero_other_sessions_keeps_the_trained_absence():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("fg", cwd="/x/fg")
    assert _where(daemon, speaker, "fg") == "Voice and keyboard: fg 1, playing."


def test_pointer_session_decision_appends_to_the_lead():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("fg", cwd="/x/fg")
    daemon._pending_decisions["fg"] = {"text": "Bash: rm x"}
    assert _where(daemon, speaker, "fg") == "Voice and keyboard: fg 1, playing, decision."


def test_real_blocking_permission_marks_decision_and_leads():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("fg", cwd="/x/fg")
    sessions.register("b", cwd="/x/b")
    sessions.register("c", cwd="/x/c")
    daemon.history.record("c", "prose", "c line.")
    daemon.handle_message(_msg("permission_request", "b", tool="Bash",
                               summary="rm -rf build"))
    out = _where(daemon, speaker, "fg")
    also = out.split("Also: ", 1)[1]
    assert also.startswith("2 b, decision, 1 waiting")    # queued ask -> waiting kept
    assert "3 c, 1 unheard" in also                       # pile follows the decision


def test_muted_only_sorts_after_piles():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("fg", cwd="/x/fg")
    sessions.register("b", cwd="/x/b")                    # number 2: muted only
    sessions.register("c", cwd="/x/c")                    # number 3: pile
    daemon._stream("b").stopped = True
    daemon.history.record("c", "prose", "c line.")
    assert _where(daemon, speaker, "fg") == (
        "Voice and keyboard: fg 1, playing. Also: 3 c, 1 unheard. 2 b, muted."
    )


def test_two_quiet_sessions_collapse_with_a_word_count():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("fg", cwd="/x/fg")
    for s in ("b", "c", "d"):
        sessions.register(s, cwd="/x/" + s)
    daemon.history.record("b", "prose", "b line.")
    assert _where(daemon, speaker, "fg") == (
        "Voice and keyboard: fg 1, playing. Also: 2 b, 1 unheard. Plus two quiet."
    )


def test_fresh_pile_is_not_stale():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("fg", cwd="/x/fg")
    now = [1000.0]
    daemon.history._clock = lambda: now[0]
    sessions.register("b", cwd="/x/b")
    daemon.history.record("b", "prose", "b line.")
    now[0] = 1100.0                                       # 100s < 900s threshold
    assert "stale" not in _where(daemon, speaker, "fg")


def test_idle_voice_names_the_keyboard_positively():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("fg", cwd="/x/fg")
    sessions.register("b", cwd="/x/b")
    daemon.history.record("b", "prose", "b line.")
    sessions.set_speaker(None)                            # loop idle
    daemon.handle_message(_msg("where_am_i", "fg"))
    texts = [it.text for it in queue._items]
    assert texts[0] == "Nothing playing. Keyboard: fg 1. Also: 2 b, 1 unheard."


def test_entries_are_sentences_never_semicolons():
    daemon, queue, speaker, sessions = _spec_roster()
    out = _where(daemon, speaker, "jam")
    assert ";" not in out
    assert out.count(". ") >= 4                           # hard boundaries between entries


def test_clause_order_decision_then_waiting_then_unheard_then_stale():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("fg", cwd="/x/fg")
    now = [1000.0]
    daemon.history._clock = lambda: now[0]
    sessions.register("b", cwd="/x/b")
    entry = daemon.history.record("b", "prose", "queued.")
    daemon._enqueue("b", "prose", "queued.", False, entry=entry)
    daemon.history.record("b", "prose", "cut.")
    daemon._pending_decisions["b"] = {"text": "x"}
    now[0] = 2000.0
    out = _where(daemon, speaker, "fg")
    assert "Also: 2 b, decision, 1 waiting, 1 unheard, stale." in out


def test_ten_plus_quiet_sessions_degrade_to_many_never_a_digit():
    """Reviewer fix: above the word map the collapse count must stay digit-free
    ("many"), never revive a spoken numeral."""
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("fg", cwd="/x/fg")
    for i in range(10):                                   # 10 quiet others
        sessions.register("q{0}".format(i), cwd="/x/q{0}".format(i))
    daemon.history.record("q0", "prose", "line.")         # q0 reports; 9 stay quiet
    for i in range(3):                                    # push quiet count past the map
        sessions.register("r{0}".format(i), cwd="/x/r{0}".format(i))
    out = _where(daemon, speaker, "fg")
    assert out.endswith("Plus many quiet.")
    assert "12 quiet" not in out
