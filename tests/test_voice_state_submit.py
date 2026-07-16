# tests/test_voice_state_submit.py (new)
"""W5 (spec §6): the missing enum write UNDER Policy A — the speak loop's held
branch gates on the STREAM's .stopped (host.py:451-453), not the enum, so a
Policy-A submit could leave voice_state='quiet-hold' while the voice audibly
talks (and keep the keep-going gate closed, host.py:485)."""
from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_take_voice_submit_lifts_quiet_hold_to_flowing():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))  # idle -> takes voice
    assert daemon.voice_state == "flowing"


def test_denied_submit_never_lifts():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon._enqueue("fg", "prose", "still talking.", False)  # speaker fg non-quiescent
    daemon.handle_message(_msg("set_foreground", "b", cwd="/x/b"))    # denied: register-only
    assert daemon.voice_state == "quiet-hold"
    assert sessions.foreground() == "fg"


def test_stopped_all_is_never_lifted():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "stopped-all"
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))
    assert daemon.voice_state == "stopped-all"     # the master quiet is deliberate


def test_muted_self_submit_keeps_the_hold_honest():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon._stream("fg").stopped = True            # the speaker muted itself (⌃⌘S)
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))
    assert daemon.voice_state == "quiet-hold"      # "on hold" remains true


def test_where_am_i_says_playing_after_the_lift():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.voice_state = "quiet-hold"
    daemon.handle_message(_msg("set_foreground", "fg", cwd="/x/fg"))
    daemon.handle_message(_msg("where_am_i", "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Voice and keyboard: fg 1, playing."   # derivation unchanged; input no longer stale
