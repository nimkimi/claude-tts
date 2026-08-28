import threading

import sonari.ttyutil as ttyutil
from tests.daemon_helpers import make_daemon


def _liveness(monkeypatch, dead=()):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff not in `dead`."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _hit(daemon, folder, path="/cache/sp.aiff"):
    daemon._spearcons.available[folder] = path
    return path


def test_chooser_commit_uses_spearcon_audio_path_on_hit():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha"); sessions.register("B", cwd="/x/bravo")
    sessions.set_foreground("A")
    p = _hit(daemon, "bravo")
    daemon.handle_message({"type": "chooser_step", "direction": "next"})
    daemon.handle_message({"type": "chooser_commit"})
    item = daemon._stream("B").queue._items[0]
    assert item.audio_path == p                       # the LANDING cue is spearcon-capable
    assert item.names_session and item.control_cue
    daemon._speak_loop_once()
    assert speaker.audio_paths == [p]                 # afplayed, not spoken


def test_chooser_commit_falls_back_to_speech_on_miss():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha"); sessions.register("B", cwd="/x/bravo")
    sessions.set_foreground("A")
    daemon.handle_message({"type": "chooser_step", "direction": "next"})
    daemon.handle_message({"type": "chooser_commit"})
    item = daemon._stream("B").queue._items[0]
    assert item.audio_path is None
    daemon._speak_loop_once()
    assert speaker.spoken == ["bravo."]               # unchanged spoken landing cue
    assert "bravo" in daemon._spearcons.generated     # kicked background gen


def test_nav_crossed_folder_cue_uses_spearcon_on_hit():
    from sonari.sessions import Identity
    daemon, q, speaker, sessions, _ = make_daemon(foreground="b")
    sessions.register("a", cwd="/work/frontend")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sessions.register("b", cwd="/work/api"); sessions.set_foreground("b")
    daemon.history.record("a", "prose", "a-m0"); daemon.history.end_message("a")
    daemon.history.record("a", "prose", "a-m1")
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")
    p = _hit(daemon, "frontend")
    daemon.handle_message({"type": "nav", "to": "prev", "session": "a"})
    cue = daemon._stream("a").queue._items[0]
    assert cue.audio_path == p and cue.names_session


def test_jump_decision_crossed_cue_uses_spearcon_on_hit(monkeypatch):
    _liveness(monkeypatch)                        # D3: pin "a" live, independent of this machine's ttys
    from sonari.sessions import Identity
    daemon, q, speaker, sessions, _ = make_daemon(foreground="b")
    sessions.register("a", cwd="/work/frontend")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sessions.register("b", cwd="/work/api"); sessions.set_foreground("b")
    daemon._enqueue("a", "prose", "needs you", True)   # W4: a queued decision — a real HIT
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")
    p = _hit(daemon, "frontend")
    daemon.handle_message({"type": "jump_decision", "session": "a"})
    cue = daemon._stream("a").queue._items[0]
    assert cue.audio_path == p and cue.names_session


def test_jump_waiting_uses_spearcon_then_keeps_actionable_suffix_on_hit():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/fg")
    sessions.register("bk", cwd="/x/backend")
    daemon._enqueue("bk", "prose", "needs you", True)        # a decision -> jump target
    p = _hit(daemon, "backend")
    daemon.handle_message({"type": "jump_waiting", "session": "fg"})
    items = daemon._stream("bk").queue._items
    assert items[0].audio_path == p and items[0].names_session   # spearcon first
    # "Bring it forward to type." retained as speech when not raising (no audio_path)
    assert any(it.text == "Bring it forward to type." and it.audio_path is None
               for it in items)
    assert not any("Jumping to" in it.text for it in items)      # verb dropped (ear-tunable)


def test_where_am_i_no_spearcon_split_single_cue_on_hit():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    p = _hit(daemon, "work")                      # spearcon available, but ⌃⌘W no longer splits
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice and keyboard: work 1, playing."]
    assert p not in speaker.audio_paths           # the folder spearcon is NOT played for ⌃⌘W


def test_where_am_i_single_cue_on_miss():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice and keyboard: work 1, playing."]   # unchanged on miss


def test_session_start_pregenerates_in_background():
    daemon, q, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message({"type": "session_start", "session": "s1", "cwd": "/x/proj"})
    assert "proj" in daemon._spearcons.pregenerated
