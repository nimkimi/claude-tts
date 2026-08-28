"""D2 §6.2 (RL3): the repoint tone — fired from the OS-focus FEATURE only when
the resolved workspace session actually changes; never on every click, never on
a no-op refocus; sessions.py stays audio-free (a bool crosses the seam)."""
import json

import pytest

from sonari import config
from sonari.protocol import PROTOCOL_VERSION, MsgType
from sonari.sessions import Identity, SessionManager
from test_earcon_defaults import LEGACY_SIX
from tests.daemon_helpers import make_daemon


def _msg(t, session="", **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _focus(tty):
    return _msg(MsgType.OS_FOCUS, term_program="Apple_Terminal", tty=tty)


def _register(sessions, sid, tty):
    sessions.register(sid, cwd="/x/" + sid)
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


def test_click_that_moves_the_workspace_fires_repoint():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _register(sessions, "a", "/dev/ttys001")
    _register(sessions, "b", "/dev/ttys002")
    daemon.handle_message(_focus("/dev/ttys002"))       # workspace a -> b
    assert speaker.earcons == ["repoint"]


def test_noop_refocus_stays_silent():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _register(sessions, "a", "/dev/ttys001")
    _register(sessions, "b", "/dev/ttys002")
    daemon.handle_message(_focus("/dev/ttys002"))
    speaker.earcons.clear()
    daemon.handle_message(_focus("/dev/ttys002"))       # same terminal again
    assert speaker.earcons == []


def test_click_resolving_to_the_current_workspace_is_silent():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _register(sessions, "a", "/dev/ttys001")
    daemon.handle_message(_focus("/dev/ttys001"))       # pin lands where you already are
    assert speaker.earcons == []


def test_blur_that_keeps_the_same_workspace_is_silent():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _register(sessions, "a", "/dev/ttys001")
    daemon.handle_message(_focus("/dev/ttys001"))
    speaker.earcons.clear()
    daemon.handle_message(_msg(MsgType.OS_FOCUS, focused=False))   # falls back to fg == a
    assert speaker.earcons == []


def test_blur_that_moves_the_workspace_back_fires_repoint():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    _register(sessions, "a", "/dev/ttys001")
    _register(sessions, "b", "/dev/ttys002")
    daemon.handle_message(_focus("/dev/ttys002"))       # workspace -> b
    speaker.earcons.clear()
    daemon.handle_message(_msg(MsgType.OS_FOCUS, focused=False))   # pin cleared -> back to a
    assert speaker.earcons == ["repoint"]


def test_set_os_focus_reports_the_change_and_stays_audio_free():
    sm = SessionManager()
    sm.set_foreground("a")
    sm.register("b")
    sm.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sm.set_identity("b", Identity(term_program="Apple_Terminal", tty="/dev/ttys002"))
    assert sm.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys002") is True
    assert sm.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys002") is False
    assert sm.set_os_focus(focused=False) is True       # pin cleared: back to foreground


@pytest.mark.expects_silent_cue
def test_repoint_was_silent_on_his_unhealed_legacy_config():
    """What five weeks of silence actually looked like.

    This is the BEFORE state, pinned: a config carrying only his six keys, as
    bootstrap's whole-key guard left it, resolves nothing for repoint.
    """
    daemon, _, speaker, _, _ = make_daemon(foreground="A", earcons=LEGACY_SIX)
    daemon.cue("repoint")
    assert speaker.earcons == []
    assert speaker.silent_cues == ["repoint"]


def test_repoint_is_audible_once_load_config_heals_that_same_config():
    """The AFTER state, driven through the REAL merge rather than hand-seeded.

    This assertion passes FALSELY against the old FakeSpeaker, which appended
    the kind whether or not it resolved. It only means something because D0.2
    made the fake resolve assets the way the real Speaker does -- which is the
    whole "the tests pin the call, not the ear" closure, made concrete.
    """
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text(
        json.dumps({"earcons": LEGACY_SIX}), encoding="utf-8")
    healed = config.load_config()["earcons"]

    daemon, _, speaker, _, _ = make_daemon(foreground="A", earcons=healed)
    daemon.cue("repoint")
    assert speaker.earcons == ["repoint"]
    assert speaker.earcon_paths == ["/System/Library/Sounds/Bottle.aiff"]
    assert speaker.silent_cues == []
