"""T6 — Control keys (⌃⌘S / ⌃⌘Tab / ⌃⌘W) act on speaker() in the keep-going era.

When keep-going has diverged speaker() from foreground(), these three keys must
act on the session the user is HEARING (speaker), not the silent workspace
(foreground). Nima chose speaker() for all three.

Test pattern: force set_speaker("B") with foreground="A", assert the key acts
on B (speaker), not A (workspace). Mirrors the T1/T2 divergence pattern.
"""
from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# ---------------------------------------------------------------------------
# ⌃⌘S — on_stop_session targets speaker()
# ---------------------------------------------------------------------------

def test_stop_session_stops_speaker_not_foreground_under_divergence():
    """⌃⌘S under divergence stops B (speaker), not A (workspace)."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.set_speaker("B")                          # voice=B, workspace=A
    assert sessions.speaker() == "B" and sessions.foreground() == "A"
    daemon.handle_message(_msg(MsgType.STOP_SESSION, ""))
    assert daemon._stream("B").stopped is True         # speaker B was stopped
    assert daemon._stream("A").stopped is False        # workspace A untouched


def test_stop_session_cue_lands_in_speaker_stream_and_is_voiced():
    """⌃⌘S "Stopped." lands in B's (speaker) stream and is voiced by the held
    branch even though A is the foreground (F4 fix: cue follows the target)."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "b content", False)
    sessions.set_speaker("B")                          # voice=B, workspace=A
    daemon.handle_message(_msg(MsgType.STOP_SESSION, ""))
    # Cue must be in B's stream (pause_exempt so the held branch voices it)
    bq = daemon._stream("B").queue
    assert any(it.text == "Stopped." for it in bq._items)
    aq = daemon._stream("A").queue
    assert not any(it.text == "Stopped." for it in aq._items)
    # Held branch: speaker()==B, B is stopped, pop the pause-exempt cue and voice it.
    daemon._speak_loop_once()
    assert any(s and "Stopped." in s for s in speaker.spoken)


def test_stop_session_resume_cue_also_follows_speaker():
    """⌃⌘S resume: "Resumed." enqueues to the speaker's stream and is voiced."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._stream("B").stopped = True                 # B already stopped
    sessions.set_speaker("B")                          # voice=B, workspace=A
    daemon.handle_message(_msg(MsgType.STOP_SESSION, ""))
    # Resume un-stops B and puts "Resumed." at the front of B's stream.
    bq = daemon._stream("B").queue
    assert any(it.text == "Resumed." for it in bq._items)
    assert daemon._stream("B").stopped is False
    # Normal branch voices "Resumed." since B is no longer stopped.
    daemon._speak_loop_once()
    assert any(s and "Resumed." in s for s in speaker.spoken)


# ---------------------------------------------------------------------------
# ⌃⌘Tab — on_cycle_session cycles FROM workspace() (Fork 1, T4)
# ---------------------------------------------------------------------------

def test_cycle_session_from_workspace_not_speaker_under_divergence():
    """⌃⌘Tab under divergence: cycles from A (workspace) → B, not from B (speaker) → C."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.register("C", cwd="/x/C")
    sessions.set_speaker("B")                          # voice=B, workspace=A; roster=[A,B,C]
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    # Fork 1 = workspace(): fg=A(idx 0) -> target=B; speaker()=="B" (not "C").
    assert sessions.speaker() == "B"


def test_cycle_session_parity_when_speaker_equals_foreground():
    """⌃⌘Tab at parity (speaker==foreground): behavior is identical to before."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    # speaker==foreground==A
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert sessions.speaker() == "B"


# ---------------------------------------------------------------------------
# ⌃⌘W — on_where_am_i reports speaker()
# ---------------------------------------------------------------------------

def test_where_am_i_reports_speaker_folder_under_divergence():
    """⌃⌘W under divergence: reports B's (speaker) state, not A's (workspace)."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    daemon._enqueue("B", "prose", "b content", False)  # keep B non-empty (no keep-going)
    sessions.set_speaker("B")                          # voice=B, workspace=A
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    # Status must mention B's folder ("bravo"), not A's ("alpha").
    assert any(s and "bravo" in s for s in speaker.spoken)
    assert not any(s and "alpha" in s for s in speaker.spoken)
