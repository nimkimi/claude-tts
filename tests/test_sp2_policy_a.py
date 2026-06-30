"""SP2 T3 — Policy-A preempt gate + workspace-split guard.

Tests:
  E  test_policy_a_speaker_self_submit_does_not_move_workspace
     keep-going-advanced speaker self-submitting takes voice (already ours) but
     must NOT drift the workspace. Fails under the #65 gate; pins the guard.
  B  test_policy_a_non_speaker_foreground_does_not_seize
     re-asserts T1's Test B under the new gate (busy non-speaker is denied).
  M4 test_session_start_does_not_seize_busy_voice
     SESSION_START is idle-only (new session can't be the speaker → idle branch
     is the only allow path).
  M4 test_session_start_takes_voice_when_idle
     idle bootstrap: first SESSION_START takes voice + workspace.
"""
import threading
from sonari.protocol import MsgType
from sonari.queue import SpeechItem
from sonari.daemon.features import lifecycle
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- Test E: the keep-going-advanced speaker self-submitting does NOT move the workspace ---
def test_policy_a_speaker_self_submit_does_not_move_workspace():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.set_speaker("B")                      # diverge: voice=B, workspace=A
    assert sessions.speaker() == "B" and sessions.foreground() == "A"
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "B"))   # B autonomously self-submits
    assert sessions.foreground() == "A"            # workspace did NOT drift onto the speaker
    assert sessions.speaker() == "B"               # voice already ours; unchanged
    # The same-session self-cut still fires (F7, ratified), workspace still A.
    daemon._current_item = SpeechItem(id=11, session="B", kind="prose",
                                      text="b", is_decision=False)
    before = speaker.cancels
    daemon.handle_message(_msg(MsgType.FLUSH, "B"))
    assert speaker.cancels == before + 1
    assert sessions.foreground() == "A"


# --- Test B stays green after the gate swap (asserts on OUTCOMES, not gate internals) ---
def test_policy_a_non_speaker_foreground_does_not_seize():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b1", False)
    daemon._enqueue("B", "prose", "b2", False)
    sessions.set_speaker("B")                      # voice=B (busy), workspace=A
    daemon.handle_message(_msg(MsgType.SET_FOREGROUND, "A"))   # A re-submits while B speaks
    assert sessions.speaker() == "B"               # denied: B keeps the voice
    assert len(daemon._stream("B").queue) == 2     # b1,b2 untouched


# --- M4: SESSION_START takes the voice only when idle; identity block is unconditional ---
def test_session_start_does_not_seize_busy_voice(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b busy", False)
    sessions.set_speaker("B")                      # voice busy on B
    daemon.handle_message(_msg(MsgType.SESSION_START, "C", cwd="/x/C",
                               term_program="Apple_Terminal", tty="/dev/ttysC"))
    assert sessions.speaker() == "B"               # idle-only: brand-new C did not seize
    assert sessions.identity("C") is not None      # identity registration ran unconditionally
    assert "C" in sessions.session_ids()


def test_session_start_takes_voice_when_idle(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)   # no speaker
    daemon.handle_message(_msg(MsgType.SESSION_START, "C", cwd="/x/C",
                               term_program="Apple_Terminal", tty="/dev/ttysC"))
    assert sessions.speaker() == "C"               # idle bootstrap took the voice
    assert sessions.foreground() == "C"            # AND the workspace (first genuine session)
