from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- Fork 1: the anchor is workspace(), not the (keep-going-advanced) speaker() ---
def test_cycle_anchor_is_workspace_not_speaker():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.register("C", cwd="/x/C")             # roster [A, B, C]
    sessions.set_speaker("C")                      # keep-going advanced the voice to C; workspace=A
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert sessions.speaker() == "B"               # stepped from workspace A(0) -> B; NOT speaker C -> A


# --- Fork 2: cycle onto a MUTED session keeps the workspace there + keep-goes the voice ---
def test_cycle_onto_muted_keeps_going_to_active():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/A")             # roster [B, A, C]
    sessions.register("C", cwd="/x/C")
    daemon._stream("A").stopped = True             # A muted
    daemon._enqueue("C", "prose", "c active", False)
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))  # B(0) -> A(1), muted
    assert sessions.workspace() == "A"             # workspace landed on the muted target
    assert sessions.speaker() is None              # voice released off the mute (no dead-stop)
    assert daemon.voice_state == "flowing"         # hold lifted
    assert daemon._stream("A").stopped is True     # target stays muted (R7:191)
    daemon._speak_loop_once()                      # keep-going voices an ACTIVE session
    assert sessions.speaker() == "C"
    assert any(s and "c active" in s for s in speaker.spoken)


# --- Fork 2 edge (c)#9: cycle onto muted with NO active session -> speaker None, ⌃⌘W reports ---
def test_cycle_onto_muted_no_active_reports_via_where_am_i():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/A")             # roster [B, A]; B has nothing, A muted
    daemon._stream("A").stopped = True
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))  # -> A, muted
    assert sessions.workspace() == "A" and sessions.speaker() is None
    # ⌃⌘W: speaker None + a MUTED workspace -> nothing voiceable without moving the
    # voice -> honest error earcon (the playable-workspace path is exercised in T0).
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    assert speaker.earcons[-1] == "error"


# --- Fork 4: ⌃⌘S STARTS the navigated-to muted workspace (not stop the active speaker) ---
def test_ctrl_s_starts_navigated_muted_workspace():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/A")             # roster [B, A, C]
    sessions.register("C", cwd="/x/C")
    daemon._stream("A").stopped = True             # A muted
    daemon._enqueue("A", "prose", "a pile", False)
    daemon._enqueue("C", "prose", "c active", False)
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))  # workspace=A(muted), keep-go
    daemon._speak_loop_once()                       # voice keep-goes to C
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
