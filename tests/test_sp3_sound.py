from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- D2 §6.1 (T3): the FLOWING speaker's turn boundary now SPEAKS — your_turn
#     (the old silence made "done" and "stack died" identical; req 18 superseded) ---
def test_flowing_speaker_turn_done_becomes_your_turn():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.EARCON, "fg", kind="turn_done"))
    assert speaker.earcons == ["your_turn"]


# --- a NON-speaker's turn_done DINGS ("something landed", req 16) ---
def test_non_speaker_turn_done_dings():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("bg", cwd="/x/bg")
    daemon.handle_message(_msg(MsgType.EARCON, "bg", kind="turn_done"))
    assert speaker.earcons == ["turn_done"]


# --- F11: the MUTED ex-speaker still dings under hold (session==speaker but NOT flowing;
#     guards against the C2 speaker-only-suppression mis-implementation) ---
def test_muted_ex_speaker_dings_under_hold():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))       # quiet-hold: A is speaker AND muted
    speaker.earcons.clear()
    daemon.handle_message(_msg(MsgType.EARCON, "A", kind="turn_done"))
    assert speaker.earcons == ["turn_done"]                     # R7:192-193 muted piles + dings


# --- the flush side-effect at turn_done is UNCONDITIONAL (survives suppression) ---
def test_turn_done_flush_survives_earcon_suppression():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    config["minqueue"] = 5
    daemon.handle_message(_msg(MsgType.PROSE, "fg", delta="Only one. ", index=0, final=True))
    assert len(daemon._stream("fg").queue) == 0                 # held below threshold
    daemon.handle_message(_msg(MsgType.EARCON, "fg", kind="turn_done"))
    assert speaker.earcons == ["your_turn"]                     # solo boundary tone (D2 §6.1)
    assert len(daemon._stream("fg").queue) > 0                 # ... but the flush STILL ran


# --- waiting RETIRED: background prose no longer dings mid-turn ---
def test_background_prose_no_longer_dings_mid_turn():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.PROSE, "bg", delta="chatter. ", index=0, final=False))
    assert speaker.earcons == []


# --- sessionless choice/plan/permission earcons are UNAFFECTED (the trap) ---
def test_sessionless_decision_earcons_still_fire():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.EARCON, "", kind="choice"))
    assert speaker.earcons == ["choice"]


# --- permission double-earcon self-heals when waiting retires ---
def test_permission_earcon_no_longer_doubles_with_waiting():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("bg", cwd="/x/bg")
    config["minqueue"] = 5
    daemon._buffer_prose("bg", "pending prose.", None)          # held below threshold, no earcon yet
    assert speaker.earcons == []
    daemon.handle_message(_msg(MsgType.PERMISSION_REQUEST, "bg", tool="Bash", summary="ls"))
    assert speaker.earcons == ["permission"]                    # was ["permission","waiting"] pre-SP3
