from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- ⌃⌘S-start lifts (state-based: lifts even with nothing queued, (c)#10) ---
def test_ctrl_s_start_lifts_hold():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold, A stopped, nothing queued
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # ⌃⌘S-start (resume)
    assert daemon.voice_state == "flowing"                        # lifted (state-based)
    assert daemon._stream("A").stopped is False


# --- ⌃⌘J lifts + the stopped one STAYS muted (R7:191) ---
def test_jump_lifts_hold_and_leaves_stopped_muted():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b backlog", False)
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold on A
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))
    assert daemon.voice_state == "flowing"
    assert sessions.speaker() == "B"
    assert daemon._stream("A").stopped is True                    # A stays muted (lift != un-mute)


# --- ⌃⌘J with NO target: does NOT lift, but the cue is AUDIBLE under hold (override 3) ---
def test_jump_no_target_is_audible_under_hold_and_does_not_lift():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold, A stopped, no other session
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))          # nothing waiting
    daemon._speak_loop_once()                                     # held branch pops pause_exempt
    assert any(s and "No session waiting." in s for s in speaker.spoken)
    assert daemon.voice_state == "quiet-hold"                     # no jump happened -> no lift


# --- ⌃⌘D (jump-decision) lifts (R5 jump-class) ---
def test_jump_decision_lifts_hold():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon._enqueue("A", "permission", "Allow X?", True)
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold
    daemon.handle_message(_msg(MsgType.JUMP_DECISION, ""))
    assert daemon.voice_state == "flowing"


# --- a CROSS-nav lifts; a WITHIN-response nav does NOT ---
def test_nav_cross_lifts_but_within_does_not():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon.history.record("B", "prose", "b msg"); daemon.history.end_message("B")
    daemon._enqueue("B", "prose", "b backlog", False)
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))         # quiet-hold, speaker=workspace=A
    # within-response nav on A (not crossed: workspace()==speaker()==A) -> NO lift
    daemon.history.record("A", "prose", "a msg"); daemon.history.end_message("A")
    daemon.handle_message(_msg(MsgType.NAV, "", to="prev"))
    assert daemon.voice_state == "quiet-hold"                     # within-nav did NOT lift
    # now cross to B via OS focus -> crossed -> lift
    from sonari.sessions import Identity
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttysB"))
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysB")
    daemon.handle_message(_msg(MsgType.NAV, "", to="prev"))
    assert daemon.voice_state == "flowing"                        # cross-nav lifted
