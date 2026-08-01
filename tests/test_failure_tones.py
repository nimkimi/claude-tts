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
    assert played == ["/System/Library/Sounds/Sosumi.aiff",
                      "/System/Library/Sounds/Sosumi.aiff"]


def test_config_entry_wins_and_old_kinds_keep_silent_noop():
    played = []
    sp = Speaker(earcon_player=lambda p: played.append(p) or None,
                 earcons={"error_misdirected": "/custom/door.aiff"})
    sp.transient("error_misdirected")              # config override wins
    sp.transient("turn_done")                      # absent OLD kind: silent no-op
    assert played == ["/custom/door.aiff"]


def test_macos_defaults_gain_the_new_kinds():
    from sonari.platform.macos.earcon import _DEFAULTS
    assert _DEFAULTS["error_misdirected"] == "/System/Library/Sounds/Sosumi.aiff"
    assert _DEFAULTS["error_system"] == "/System/Library/Sounds/Sosumi.aiff"
    assert _DEFAULTS["error"] == "/System/Library/Sounds/Sosumi.aiff"  # unchanged


# ---------------------------------------------------------------------------
# D7a (§4): spontaneous failures speak a word after the tone
# ---------------------------------------------------------------------------

def test_permission_expiry_speaks_the_word_on_the_asking_session():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("permission_request", "fg", tool="Bash", summary="ls"))
    with daemon._lock:
        daemon._expire_permission("fg", daemon._pending_decisions["fg"])
    assert "permission_expired" in speaker.earcons
    texts = [it.text for it in queue._items]
    assert "That ask timed out — check the terminal." in texts   # word enqueued on fg
    assert "Bash: ls" not in texts                               # the dead ask was removed


def test_expiry_word_speaks_on_a_muted_session():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("stop_session", "fg"))          # fg muted, voice held
    daemon.handle_message(_msg("permission_request", "fg", tool="Bash", summary="ls"))
    with daemon._lock:
        daemon._expire_permission("fg", daemon._pending_decisions["fg"])
    daemon._speak_loop_once()                                  # held branch: "Stopped."
    daemon._speak_loop_once()                                  # held branch: the word
    assert "That ask timed out — check the terminal." in speaker.spoken


def test_misdirected_answer_names_the_asking_session():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.register("other", cwd="/x/invoice-generator")
    daemon.handle_message(_msg("permission_request", "other", tool="Bash", summary="ls"))
    daemon.handle_message(_msg("answer_permission", "fg", behavior="allow"))
    assert speaker.earcons[-1] == "error_misdirected"
    # Routed word on the WORKSPACE stream, naming the asker by its spoken short
    # label (spearcon_label source: 'invoice-generator' -> 'invoice').
    assert queue._items[-1].text == "No ask here — invoice is asking."


def test_answer_with_nothing_pending_anywhere_says_so():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_msg("answer_permission", "fg", behavior="allow"))
    assert speaker.earcons == ["error_misdirected"]
    assert queue._items[-1].text == "Nothing to answer."


def test_answer_with_no_workspace_stays_tone_only():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.handle_message(_msg("answer_permission", "", behavior="allow"))
    assert speaker.earcons == ["error_misdirected"]
    assert all(len(st.queue) == 0 for st in daemon._streams.values())


def test_speak_loop_failure_speaks_the_word_after_the_tone():
    daemon, queue, speaker, sessions, config = make_daemon()
    calls = {"n": 0}
    real_speak = speaker.speak

    def _boom_once(text=None, audio_path=None, cancel_epoch=None, voice=None):
        if calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("synth failure")
        return real_speak(text, audio_path=audio_path,
                          cancel_epoch=cancel_epoch, voice=voice)

    speaker.speak = _boom_once
    daemon._enqueue("fg", "prose", "doomed.", False)
    daemon._speak_loop_once()                      # raises inside; tone + queued word
    assert "error_system" in speaker.earcons
    daemon._speak_loop_once()                      # the word drains in normal order
    assert speaker.spoken[-1] == "Speech failed; kept unheard."


def test_crossing_fallback_can_never_be_silently_unconfigured():
    from sonari.speaker import _FALLBACK_EARCONS
    assert _FALLBACK_EARCONS["crossing"] == "/System/Library/Sounds/Frog.aiff"


def test_alarm_assets_can_never_be_silently_unconfigured():
    from sonari.speaker import _FALLBACK_EARCONS
    assert _FALLBACK_EARCONS["alarm_daemon_down"] == "/System/Library/Sounds/Hero.aiff"
    assert _FALLBACK_EARCONS["alarm_hotkeys_down"] == "/System/Library/Sounds/Basso.aiff"
