"""W11 (spec §12, Block-1 ratified): the hands act on three pointers but ⌃⌘W
teaches two — the nameless third (foreground) stops being a gesture target.
Felt only under live focus divergence; without an OS-focus signal every
retargeted surface behaves byte-identically to today."""
from sonari.protocol import PROTOCOL_VERSION
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _liveness(monkeypatch, dead=()):
    from sonari import ttyutil
    monkeypatch.setattr(ttyutil, "tty_alive", lambda tty: tty not in dead)


def _focus_on(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))
    sessions.set_os_focus(term_program="Apple_Terminal", tty=tty)


def test_jump_excludes_the_workspace_not_the_stale_foreground(monkeypatch):
    _liveness(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("C", cwd="/x/C")
    _focus_on(sessions, "C", "/dev/ttysC")         # you clicked C: workspace=C
    sessions.set_speaker("B")                      # keep-going drifted the voice
    daemon._enqueue("A", "prose", "a waits.", False)
    daemon._enqueue("C", "prose", "c waits.", False)
    daemon.handle_message(_msg("jump_waiting", "A"))
    # Old exclude was foreground()=A -> ⌃⌘J could "jump" to C, the terminal
    # already in front of you. New: C (workspace) is excluded, A is reachable.
    assert sessions.speaker() == "A"


def test_empty_cue_routes_to_the_workspace_when_no_speaker(monkeypatch):
    _liveness(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("C", cwd="/x/C")
    _focus_on(sessions, "C", "/dev/ttysC")
    sessions.set_speaker(None)                     # loop idle
    daemon.handle_message(_msg("jump_waiting", "A"))
    assert [it.text for it in stream_queue(daemon, "C")._items] == ["No session waiting."]
    assert len(stream_queue(daemon, "A")._items) == 0


def test_rate_confirmation_lands_on_the_workspace(monkeypatch):
    from sonari.daemon.features import control
    monkeypatch.setattr(control, "save_config", lambda cfg: None)
    _liveness(monkeypatch)
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("C", cwd="/x/C")
    _focus_on(sessions, "C", "/dev/ttysC")
    daemon._enqueue("C", "prose", "already waiting.", False)
    daemon.handle_message(_msg("set_rate", "A", delta=25))
    assert any("Rate " in it.text for it in stream_queue(daemon, "C")._items)
    assert len(stream_queue(daemon, "A")._items) == 0
    # M-E: a LIVE destination gets at_front=False — today's path, back-append
    # behind whatever was already queued, never jumping the line.
    items = [it.text for it in stream_queue(daemon, "C")._items]
    assert items[0] == "already waiting."


def test_degenerate_no_focus_case_is_byte_identical(monkeypatch):
    from sonari.daemon.features import control
    monkeypatch.setattr(control, "save_config", lambda cfg: None)
    daemon, queue, speaker, sessions, config = make_daemon()   # no OS focus at all
    daemon.handle_message(_msg("set_rate", "fg", delta=25))
    assert any("Rate " in it.text for it in queue._items)      # falls back to foreground
