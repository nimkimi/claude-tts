from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- the enum defaults flowing; the property bridges it (cold path) ---
def test_voice_state_defaults_flowing():
    daemon, *_ = make_daemon(foreground="fg")
    assert daemon.voice_state == "flowing"
    assert daemon._state._voice_state == "flowing"       # hot-path read target


# --- the gate: keep-going is SUPPRESSED when the enum is not flowing ---
def test_gate_suppresses_keep_going_when_not_flowing():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("bg", cwd="/x/bg")
    daemon._enqueue("bg", "prose", "from bg", False)
    daemon._state._voice_state = "quiet-hold"            # set manually (real entry lands in T1)
    daemon._speak_loop_once()                            # fg idle -> gate blocks the scan
    assert sessions.speaker() == "fg"                    # voice did NOT advance to bg
    assert not any(s and "from bg" in s for s in speaker.spoken)


# --- regression guard: default flowing -> keep-going STILL fires (F9 no-op) ---
def test_gate_noop_keep_going_fires_when_flowing():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("bg", cwd="/x/bg")
    daemon._enqueue("bg", "prose", "from bg", False)
    daemon._speak_loop_once()
    assert sessions.speaker() == "bg"                    # advanced (no regression)
    assert any(s and "from bg" in s for s in speaker.spoken)


# --- state-aware None-branch: speaker() None but a workspace exists + flowing
#     -> report "Nothing playing." instead of an error tone (R7 discoverability) ---
def test_where_am_i_none_speaker_with_workspace_reports_nothing_playing():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    sessions.focus("w", cwd="/x/work")                   # workspace=w, speaker=w
    sessions.set_speaker(None)                           # legit None speaker, workspace stays w
    assert sessions.speaker() is None and sessions.workspace() == "w"
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    # Delivered via keep-going (speaker() was None): an uncached spearcon for
    # "w" binds the neutral crossing marker ahead of the readout (D2 §6.6);
    # the spoken text itself is unchanged.
    assert speaker.audio_paths[-2:] == ["/System/Library/Sounds/Frog.aiff", None]
    assert speaker.spoken == [None, "Nothing playing. Keyboard: work 1."]


# --- STATUS surfaces the voice-state ---
def test_status_reports_voice_state():
    daemon, *_ = make_daemon(foreground="fg")
    reply = daemon.handle_message(_msg(MsgType.STATUS, ""))
    assert reply["voice_state"] == "flowing"
