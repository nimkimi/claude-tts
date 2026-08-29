"""Audit pins: ctrl-cmd-M (STOP_ALL / the master quiet key) fails to silence
content that a muted session's own read-gesture backlog carries.

Both bugs share one mechanism: navigation.py's seek-and-play loop marks EVERY
transcript entry it replays `control_cue=st.stopped` when the session is
already muted (the ratified M3 "read through the mute" behaviour). But
control_cue ALSO means "exempt from the master quiet's hold" (host.py's
_pop_held_control_cue), so a whole-response replay becomes an unstoppable run
of cues the master quiet key cannot touch -- violating queue.py:14-16's own
contract that control_cue means "the answer to a deliberate press", not a
multi-item transcript body.

See /Users/Nima.Hakimi/projects/private/claude-tts/scratchpad/e3-review/test-audit/HUNT-RESULTS.json
findings 0 and 4 for the full adjudication.
"""
import pytest

from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType, PROTOCOL_VERSION


def msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


@pytest.mark.xfail(
    strict=True,
    reason="BUG-1 (new-in-receipts): a muted session's whole-response nav "
           "replay is control_cue wholesale, so it starves an unrelated LIVE "
           "session's already-queued blocking decision indefinitely; awaiting "
           "owner fix decision -- see HUNT dossier finding 0.",
)
def test_bug1_muted_sessions_control_cue_backlog_starves_a_live_sessions_blocking_decision():
    """BUG-1 (CONFIRMED, finding 0, severity high).

    mechanism: src/sonari/daemon/features/navigation.py:57-58 (_nav) and
    :106-107 (_nav_response) mark EVERY seek-and-play transcript entry
    control_cue=st.stopped -- so a whole-response replay on a MUTED session
    becomes an unstoppable run of "control cues" (queue.py:14-16 defines
    control_cue as "the answer to a deliberate press"; a multi-item transcript
    body is not that). src/sonari/daemon/host.py:1342-1368
    _pop_held_control_cue scans EVERY stopped stream for a queued control cue,
    and at host.py:1399-1400 this scan runs UNCONDITIONALLY BEFORE the
    speaker session's own queue is ever touched -- so a completely unrelated,
    MUTED session's queued read-gesture backlog drains ahead of a LIVE
    session's already-queued blocking decision, tick for tick, until the
    muted backlog is exhausted.

    ratified basis: queue.py:14-16's control_cue contract ("the answer to a
    deliberate press") is violated by marking a whole multi-item transcript
    body this way; playback.py's blocking-PERMISSION comment documents a
    ~120s answer window a large-enough backlog blows straight through (the
    hunter's own repro measured a 157s delay).
    """
    daemon, _, speaker, sessions, _ = make_daemon(verbosity="everything", foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    sessions.set_speaker("A")

    for i in range(4):
        daemon.history.record("A", "prose", "old line {0}".format(i))
        daemon.history.end_message("A")
    daemon.history.start_turn("A")
    daemon.history.record("A", "prose", "the newest line")
    daemon.history.end_message("A")

    # 1. Mute A.
    daemon.handle_message(msg(MsgType.STOP_SESSION, "A"))
    daemon._speak_loop_once()          # hears "Stopped."
    speaker.spoken.clear()

    # 2. Read gesture on the muted A: replay the whole earlier response.
    daemon.handle_message(msg(MsgType.NAV, "A", to="prev_response"))
    flood_len = len(daemon._stream("A").queue)
    assert flood_len >= 4              # the orientation cue + the 4-entry turn

    # 3. B is a LIVE, unmuted session with its own blocking decision queued --
    #    mirrors keep-going having already advanced the voice there.
    sessions.set_speaker("B")
    daemon._enqueue("B", "prose", "B needs an answer now", True)
    assert daemon._stream("B").stopped is False

    # 4. Drain exactly the muted flood's own length.
    for _ in range(flood_len):
        daemon._speak_loop_once()
    heard = list(speaker.spoken)

    # RATIFIED: B's blocking decision -- on the LIVE, unmuted speaker -- must
    # not be starved behind an unrelated muted session's whole-response replay.
    assert "B needs an answer now" in heard


@pytest.mark.xfail(
    strict=True,
    reason="BUG-3 (new-in-receipts): ctrl-cmd-M does not silence a muted "
           "session's nav-triggered backlog -- most of it plays anyway, and "
           "'All stopped.' arrives after the leak, not before it; awaiting "
           "owner fix decision -- see HUNT dossier finding 4.",
)
def test_bug3_ctrl_cmd_m_does_not_silence_a_muted_sessions_nav_replay():
    """BUG-3 (CONFIRMED, finding 4, severity high).

    mechanism: src/sonari/daemon/features/navigation.py:57-58/:106-107 mark
    a muted session's whole seek-and-play replay control_cue=True (M3's
    ratified "read through the mute"). src/sonari/daemon/features/
    playback.py:206-229 on_stop_all (ctrl-cmd-M) silences by setting
    st.stopped=True on every stream -- exactly the state
    _pop_held_control_cue (host.py:1342-1368) drains from. So pressing the
    master quiet key mid-read does not stop the read: every remaining
    control-cue item in the queue still plays, one per loop tick, and the
    deliberate "All stopped." acknowledgment is enqueued BEHIND them, so it
    speaks last instead of first.

    ratified basis: docs/superpowers/specs/2026-08-28-receipts-design.md:658
    -- "he pressed [ctrl-cmd-M] meaning *silence everything*" (R7 "lasting
    quiet"). Under stop-all NOTHING may speak except the deliberate
    acknowledgment.
    """
    daemon, _, speaker, sessions, _ = make_daemon(verbosity="everything", foreground="B")
    for sid, cwd in (("A", "/x/alpha"), ("B", "/x/bravo")):
        sessions.register(sid, cwd=cwd)
    sessions.set_speaker("B")

    for i in range(5):
        daemon.history.record("B", "prose", "old line {0}".format(i))
        daemon.history.end_message("B")
    daemon.history.start_turn("B")
    daemon.history.record("B", "prose", "the newest line")
    daemon.history.end_message("B")

    # 1. MUTE this session.
    daemon.handle_message(msg(MsgType.STOP_SESSION, "B"))
    daemon._speak_loop_once()          # hears "Stopped."
    speaker.spoken.clear()

    # 2. READ GESTURE on the muted session: jump back a whole response.
    daemon.handle_message(msg(MsgType.NAV, "B", to="prev_response"))
    daemon._speak_loop_once()          # the orientation cue
    daemon._speak_loop_once()          # the first line of the response
    speaker.spoken.clear()

    # 3. THE MASTER QUIET KEY. Silence everything.
    daemon.handle_message(msg(MsgType.STOP_ALL, "B"))

    # 4. Run the loop out. Under the ratified law the ONLY thing that may be
    #    heard is the deliberate acknowledgment "All stopped."
    for _ in range(20):
        daemon._speak_loop_once()

    leaked = [s for s in speaker.spoken if s not in ("All stopped.",)]
    assert not leaked
