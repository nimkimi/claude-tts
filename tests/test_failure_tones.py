"""W6 (spec §7): three failure classes, principled by what-you-should-do-next.
Invalid/nothing-there keeps Sosumi ('error', unchanged); misdirected answers get
'error_misdirected'; speak-loop crashes get 'error_system'. New kinds can NEVER
be silently disabled on an existing install (speaker-side fallback — the
pitch-asset precedent; bootstrap merges defaults only when the whole earcons key
is absent)."""
from sonari.protocol import PROTOCOL_VERSION
from sonari.speaker import Speaker
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_misdirected_answer_plays_error_misdirected():
    daemon, queue, speaker, sessions, config = make_daemon()
    # No pending decision on the workspace: valid intent, wrong session.
    daemon.handle_message(_msg("answer_permission", "fg", behavior="allow"))
    assert speaker.earcons == ["error_misdirected"]


def test_invalid_behavior_keeps_plain_error():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("answer_permission", "fg", behavior="maybe"))
    assert speaker.earcons == ["error"]


def test_speak_loop_failure_plays_error_system():
    daemon, queue, speaker, sessions, config = make_daemon()
    def _boom(text=None, audio_path=None, cancel_epoch=None):
        raise RuntimeError("synth failure")
    speaker.speak = _boom
    daemon._enqueue("fg", "prose", "doomed.", False)
    daemon._speak_loop_once()                      # must not raise; signals audibly
    assert "error_system" in speaker.earcons
    assert daemon._current_item is None            # claim released


def test_new_kinds_fall_back_on_an_existing_installs_config():
    played = []
    sp = Speaker(earcon_player=lambda p: played.append(p) or None,
                 earcons={"error": "/System/Library/Sounds/Sosumi.aiff"})
    sp.transient("error_misdirected")
    sp.transient("error_system")
    assert played == ["/System/Library/Sounds/Basso.aiff",
                      "/System/Library/Sounds/Blow.aiff"]


def test_config_entry_wins_and_old_kinds_keep_silent_noop():
    played = []
    sp = Speaker(earcon_player=lambda p: played.append(p) or None,
                 earcons={"error_misdirected": "/custom/door.aiff"})
    sp.transient("error_misdirected")              # config override wins
    sp.transient("turn_done")                      # absent OLD kind: silent no-op
    assert played == ["/custom/door.aiff"]


def test_macos_defaults_gain_the_new_kinds():
    from sonari.platform.macos.earcon import _DEFAULTS
    assert _DEFAULTS["error_misdirected"] == "/System/Library/Sounds/Basso.aiff"
    assert _DEFAULTS["error_system"] == "/System/Library/Sounds/Blow.aiff"
    assert _DEFAULTS["error"] == "/System/Library/Sounds/Sosumi.aiff"  # unchanged
