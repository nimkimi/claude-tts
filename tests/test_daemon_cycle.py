"""Cycle sessions (⌃⌘Tab / ⌃⌘⇧Tab) — roster navigation in insertion order."""
import sonari.ttyutil as ttyutil
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon
from tests.test_daemon_focus_follow import RecordingRaiseService


def test_cycle_next_moves_voice_to_the_next_session_and_cues_it():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    sessions.set_foreground("A")
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    assert sessions.foreground() == "B"
    assert speaker.cancels == 1                  # barge-in: the switch is immediate
    daemon._speak_loop_once()
    assert speaker.spoken == ["bravo."]          # self-naming folder cue at the front


def test_cycle_next_wraps_from_last_to_first():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A")
    sessions.register("B")
    sessions.set_foreground("B")                 # last in the roster
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    assert sessions.foreground() == "A"          # wraps to the first


def test_cycle_prev_wraps_from_first_to_last():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A")
    sessions.register("B")
    sessions.register("C")
    sessions.set_foreground("A")                 # first in the roster
    daemon.handle_message({"type": "cycle_session", "direction": "prev"})
    assert sessions.foreground() == "C"          # wraps to the last


def test_cycle_with_fewer_than_two_sessions_errors_and_does_not_switch():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    # only A is registered (via make_daemon's set_foreground)
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    assert speaker.earcons == ["error"]          # confirm fired; never a silent no-op
    assert sessions.foreground() == "A"          # unchanged


def test_cycle_raises_target_window(monkeypatch):
    # §8 cycle CHANGE row (R5/R12): cycle is a deliberate navigation, so the
    # workspace (terminal window) must follow, mirroring on_jump_waiting's raise.
    # B's tty is a fictional path (no real device node) -- fake it live so W1's
    # liveness filter (focus.py) doesn't treat this raise-behavior test as a phantom.
    monkeypatch.setattr(ttyutil, "tty_alive", lambda tty: True)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    sessions.register("B", cwd="/x/B")
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttysB"))
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    assert sessions.foreground() == "B"          # cycle moved the voice to B
    assert len(rs.attempts) == 1                 # cycle attempted a raise
    ident, gen = rs.attempts[0]
    assert ident.tty == "/dev/ttysB" and gen >= 1
