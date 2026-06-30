"""SP2 whole-branch review finding — on_jump_waiting None-path cue routing.

When nothing is waiting and JUMP_WAITING fires, the "No session waiting."
confirmation must land in SPEAKER()'s stream so the speak loop (which reads
speaker()'s stream) voices it immediately.

Before the fix, the cue was enqueued to foreground() instead of speaker().
Under divergence (speaker=B, foreground=A) the loop reads B's stream, so the
cue landed in A and was NOT heard — the comment claiming "always heard" was
false.

After the fix, the cue goes to speaker() or foreground() (matching the T2/F3
pattern), so it lands in the stream the loop actually reads.
"""
from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_no_session_waiting_cue_routes_to_speaker_not_foreground():
    """Under divergence (speaker=B, foreground=A), with nothing waiting,
    the 'No session waiting.' cue must land in B's stream (the speaker's)
    so the speak loop voices it immediately.

    Before fix: cue lands in A → not heard (RED).
    After fix: cue lands in B → heard (GREEN).
    """
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.set_speaker("B")                      # diverge: voice=B, workspace=A
    assert sessions.speaker() == "B"
    assert sessions.foreground() == "A"

    # No session has waiting output, so _waiting_target returns None → None-path fires.
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))

    # The cue must be in B's stream (what the speak loop reads), NOT A's.
    bq = daemon._stream("B").queue
    aq = daemon._stream("A").queue
    assert any(it.text == "No session waiting." for it in bq._items), (
        "Cue must land in speaker(B)'s stream so the loop can voice it"
    )
    assert not any(it.text == "No session waiting." for it in aq._items), (
        "Cue must NOT be in foreground(A)'s stream (loop doesn't read it)"
    )

    # End-to-end: the loop (reads speaker=B, B is not stopped) voices the cue.
    daemon._speak_loop_once()
    assert any(s and "No session waiting." in s for s in speaker.spoken), (
        "speak loop must have voiced the confirmation from B's stream"
    )


def test_no_session_waiting_cue_aligned_case_still_works():
    """When speaker==foreground (no divergence), the cue still routes correctly
    and is voiced — parity / no regression."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    # No divergence: speaker defaults to foreground A
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))

    aq = daemon._stream("A").queue
    assert any(it.text == "No session waiting." for it in aq._items)

    daemon._speak_loop_once()
    assert any(s and "No session waiting." in s for s in speaker.spoken)


def test_no_session_waiting_earcon_when_both_none():
    """When BOTH speaker() and foreground() are None, an error earcon fires
    instead of crashing."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    # No sessions registered; speaker() and foreground() are both None.
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))
    assert "error" in speaker.earcons
