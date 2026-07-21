# tests/test_voice_state_submit.py (new)
"""W5 (spec §6): the missing enum write UNDER Policy A — the speak loop's held
branch gates on the STREAM's .stopped (host.py:451-453), not the enum, so a
Policy-A submit could leave voice_state='quiet-hold' while the voice audibly
talks (and keep the keep-going gate closed, host.py:485)."""
from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_take_voice_submit_lifts_quiet_hold_and_speaks_resumed():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    # The REAL hook pair: UserPromptSubmit sends SET_FOREGROUND then FLUSH — the
    # word must land AFTER the flush's clear or it would be wiped (D2 §6.3).
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))
    assert daemon.voice_state == "flowing"
    assert len(queue) == 0                          # deferred: nothing enqueued yet
    daemon.handle_message(_msg("flush", "fg"))
    assert [it.text for it in queue._items] == ["Resumed."]


def test_bare_set_foreground_defers_the_word():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))
    assert daemon.voice_state == "flowing"
    assert daemon._stream("fg").announce_resume is True    # armed, not yet audible
    assert len(queue) == 0


def test_session_start_path_delivers_the_word_without_a_flush():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon.handle_message(_msg("set_foreground", "n1", cwd="/x/n1"))
    daemon.handle_message(_msg("session_start", "n1", cwd="/x/n1"))
    texts = [it.text for it in stream_queue(daemon, "n1")._items]
    assert "Resumed." in texts
    assert daemon._stream("n1").announce_resume is False


def test_denied_submit_never_lifts_or_speaks():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon._enqueue("fg", "prose", "still talking.", False)  # speaker fg non-quiescent
    daemon.handle_message(_msg("set_foreground", "b", cwd="/x/b"))    # denied: register-only
    daemon.handle_message(_msg("flush", "b"))
    assert daemon.voice_state == "quiet-hold"
    assert sessions.foreground() == "fg"
    assert all(it.text != "Resumed." for it in stream_queue(daemon, "b")._items)


def test_stopped_all_is_never_lifted_and_stays_wordless():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "stopped-all"
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))
    daemon.handle_message(_msg("flush", "fg"))
    assert daemon.voice_state == "stopped-all"     # the master quiet is deliberate
    assert all(it.text != "Resumed." for it in queue._items)


def test_muted_self_submit_keeps_the_hold_honest_and_wordless():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon._stream("fg").stopped = True            # the speaker muted itself (⌃⌘S)
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))
    daemon.handle_message(_msg("flush", "fg"))
    assert daemon.voice_state == "quiet-hold"      # "on hold" remains true
    assert all(it.text != "Resumed." for it in queue._items)


def test_where_am_i_says_playing_after_the_lift():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))
    daemon.handle_message(_msg("where_am_i", "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Voice and keyboard: fg 1, playing."   # derivation unchanged; input no longer stale
