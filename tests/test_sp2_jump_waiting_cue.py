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
import sonari.ttyutil as ttyutil
from sonari.protocol import MsgType
from sonari.sessions import Identity
from sonari.daemon.features import focus
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _liveness(monkeypatch, dead):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff not in `dead`."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _ident(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


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


# --- D3 §4c: commit-time re-check + truthful empty tails (Task 7) -----------


def test_jump_commit_onto_dead_target_errors_and_does_not_move(monkeypatch):
    """The selection→focus gap: _waiting_target's candidates are is_live-filtered
    at SELECTION, but nothing re-checked liveness before the focus move (the race
    the chooser's _commit already closes, chooser.py:184-191). Simulate died-
    mid-flight by monkeypatching _waiting_target to hand back a target that is
    dead BY THE TIME on_jump_waiting acts on it (the public path can't otherwise
    reach this: selection and focus share one dispatch). Must error-tone +
    CLOSED_WORD, and touch nothing else."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/bravo")
    _ident(sessions, "B", "/dev/ttysB")
    _liveness(monkeypatch, dead={"/dev/ttysB"})   # B died after selection, before commit
    monkeypatch.setattr(focus, "_waiting_target", lambda ctx, exclude: "B")
    daemon.voice_state = "quiet-hold"             # a value the fix must NOT touch

    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))

    assert speaker.earcons == ["error"]
    texts = [it.text for it in daemon._stream("A").queue._items]
    assert "That session closed." in texts        # D7a word, speaker()-or-workspace
    assert sessions.speaker() == "A"               # no focus move
    assert sessions.foreground() == "A"
    assert daemon.voice_state == "quiet-hold"      # no voice_state write
    assert speaker.cancels == 0                    # no cancel


def test_no_session_waiting_gains_pending_tail():
    """A restored session holding queued content (pending, no live candidate)
    makes the empty cue truthful: 'No session waiting.' gains an aggregate tail."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.load_state({"P": {"folder": "/x/p", "number": 5}})   # -> pending
    daemon._enqueue("P", "prose", "restored backlog", False)      # seeds the queue only

    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))

    texts = [it.text for it in daemon._stream("A").queue._items]
    assert "No session waiting. One pending." in texts


def test_no_session_waiting_gains_closed_tail(monkeypatch):
    """A dead-tty session holding queued content makes the empty cue truthful
    with the closed tail instead of the pending one."""
    _liveness(monkeypatch, dead={"/dev/ttys404"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("D", cwd="/x/etl")
    _ident(sessions, "D", "/dev/ttys404")          # captured node is gone -> dead
    daemon._enqueue("D", "prose", "orphan backlog", False)

    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))

    texts = [it.text for it in daemon._stream("A").queue._items]
    assert "No session waiting. One closed." in texts


def test_no_session_waiting_both_tails_pending_first(monkeypatch):
    """One pending + one dead backlog-holder: both tails, pending sentence first."""
    _liveness(monkeypatch, dead={"/dev/ttys404"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.load_state({"P": {"folder": "/x/p", "number": 5}})
    daemon._enqueue("P", "prose", "restored backlog", False)
    sessions.register("D", cwd="/x/etl")
    _ident(sessions, "D", "/dev/ttys404")
    daemon._enqueue("D", "prose", "orphan backlog", False)

    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))

    texts = [it.text for it in daemon._stream("A").queue._items]
    assert "No session waiting. One pending. One closed." in texts


def test_bare_empty_fleet_keeps_the_plain_string():
    """Regression pin: a fleet with no other sessions at all keeps the plain
    string byte-exact — no tail, nothing to append."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")

    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))

    texts = [it.text for it in daemon._stream("A").queue._items]
    assert texts == ["No session waiting."]
