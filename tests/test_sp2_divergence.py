from sonari.protocol import MsgType
from sonari.queue import SpeechItem
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- CHANGE 3 / Test B: _voice_busy_elsewhere reads speaker() (the CONC-1 pin) ---
def test_voice_busy_predicate_reads_speaker_under_divergence():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b1", False)
    daemon._enqueue("B", "prose", "b2", False)
    sessions.set_speaker("B")                      # diverge: voice=B (with backlog), workspace=A
    assert sessions.speaker() == "B" and sessions.foreground() == "A"
    # The repointed predicate reads speaker()==B (busy) -> A IS busy-elsewhere.
    # (With the old foreground() read this returns False -> CONC-1 relocated: A would seize B.)
    assert daemon._voice_busy_elsewhere("A") is True
    # End-to-end at the still-#65 gate: A's submit registers only; B keeps the voice.
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "A"))
    assert sessions.speaker() == "B"
    assert len(daemon._stream("B").queue) == 2     # b1,b2 untouched (not seized/flushed)


def test_voice_busy_predicate_parity_when_aligned():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("A", "prose", "a1", False)     # the speaker(==foreground) has backlog
    assert daemon._voice_busy_elsewhere("B") is True   # B sees A (the speaker) busy
    assert daemon._voice_busy_elsewhere("A") is False  # A is the speaker -> not "elsewhere"


# --- turn boundary under divergence: ONE sound regardless of who finished ---
def test_ding_gate_uses_speaker_not_foreground():
    # turn_done dings at EVERY boundary (ear-batch-2 slot 4); under divergence
    # (speaker=B, workspace=A) both B's and A's boundaries sound the same.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.set_speaker("B")                            # voice=B, workspace=A
    daemon.handle_message(_msg(MsgType.EARCON, "B", kind="turn_done"))
    assert speaker.earcons == ["turn_done"]              # one boundary sound, owner ear ruling (ear-batch-2 slot 4)
    daemon.handle_message(_msg(MsgType.EARCON, "A", kind="turn_done"))
    assert speaker.earcons == ["turn_done", "turn_done"]  # A is NOT the speaker -> landed ding


# --- CHANGE 2 / F1: on_flush cuts only the speaker's own / same-session readout ---
def test_flush_does_not_cut_across_speaker_divergence():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.set_speaker("B")                      # voice=B, workspace=A
    daemon._current_item = SpeechItem(id=901, session="B", kind="prose",
                                      text="b live", is_decision=False)
    before = speaker.cancels
    daemon.handle_message(_msg(MsgType.FLUSH, "A"))    # autonomous A submits
    assert speaker.cancels == before               # B's live readout NOT cut (Policy A)
    daemon._current_item = SpeechItem(id=902, session="B", kind="prose",
                                      text="b live2", is_decision=False)
    daemon.handle_message(_msg(MsgType.FLUSH, "B"))    # same-session supersede (F7, ratified)
    assert speaker.cancels == before + 1


# --- F6: jump-to-waiting also excludes the speaker ---
def test_jump_waiting_excludes_the_speaker():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.register("C", cwd="/x/C")
    daemon._enqueue("B", "prose", "b waiting", False)
    daemon._enqueue("C", "prose", "c waiting", False)
    sessions.set_speaker("B")                      # voice=B (already voiced), workspace=A
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))
    assert sessions.speaker() == "C"               # jumps to C, NOT the already-voiced B
