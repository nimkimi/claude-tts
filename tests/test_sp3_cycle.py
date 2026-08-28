from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- Fork 4: ⌃⌘S STARTS the navigated-to muted workspace (not stop the active speaker) ---
def test_ctrl_s_starts_navigated_muted_workspace():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/A")             # candidates [B, A, C]
    sessions.register("C", cwd="/x/C")
    daemon._stream("A").stopped = True             # A muted
    daemon._enqueue("A", "prose", "a pile", False)
    daemon._enqueue("C", "prose", "c active", False)
    # Navigate onto the mute via the chooser: first step lands index 1 = A; commit.
    daemon.handle_message(_msg(MsgType.CHOOSER_STEP, "", direction="next"))
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))   # workspace=A(muted), keep-go
    daemon._speak_loop_once()                       # tick 1 (M2): landing cue from muted A
    daemon._speak_loop_once()                       # tick 2: voice keep-goes to C
    assert sessions.workspace() == "A" and sessions.speaker() == "C"
    daemon.handle_message(_msg(MsgType.STOP_SESSION, ""))   # ⌃⌘S: workspace A is muted -> START A
    assert daemon._stream("A").stopped is False     # A started (un-muted)
    assert sessions.speaker() == "A"                # voice moved to the started session
    assert daemon._stream("C").stopped is False     # C the ACTIVE speaker was NOT stopped


# --- Fork 4 else-branch: workspace active -> ⌃⌘S STOPS the speaker (status quo) ---
def test_ctrl_s_stops_speaker_when_workspace_active():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, ""))   # workspace A active -> stop the speaker A
    assert daemon._stream("A").stopped is True
    assert daemon.voice_state == "quiet-hold"
