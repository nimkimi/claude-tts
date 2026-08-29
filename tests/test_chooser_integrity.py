"""Audit pin: a chooser commit onto a live session leaks the session he just
deliberately left.

See /Users/Nima.Hakimi/projects/private/claude-tts/scratchpad/e3-review/test-audit/HUNT-RESULTS.json
finding 5 for the full adjudication.
"""
import pytest

from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType, PROTOCOL_VERSION


def msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


@pytest.mark.xfail(
    strict=True,
    reason="BUG-4 (new-in-receipts): committing the chooser onto a live "
           "session does not stop a muted session's read-through-mute "
           "backlog from continuing to speak, unattributed, ahead of and "
           "behind the landing cue; awaiting owner fix decision -- see HUNT "
           "dossier finding 5.",
)
def test_bug4_chooser_commit_onto_live_session_leaks_the_muted_sessions_backlog():
    """BUG-4 (CONFIRMED, finding 5, severity high).

    mechanism: two ratified paths collide. chooser.py:191-238 _commit does
    focus(target), voice_state="flowing", and enqueues the "{folder}."
    landing cue -- a genuine deliberate move. But host.py:1342-1368
    _pop_held_control_cue scans EVERY stopped stream for a queued control cue
    and, at host.py:1399-1400, this scan runs BEFORE the speaker is even
    resolved -- so the muted session's OWN leftover read-gesture backlog
    (control_cue=True since navigation.py:57-58/106-107, M3's ratified
    read-through-mute) keeps outranking the landing cue and the newly
    committed session's own content. host.py:657-676 _attributed_text also
    skips the folder prefix for control cues, so what plays is an
    UNATTRIBUTED voice still reading the session he just deliberately left.

    ratified basis: a chooser commit is THE deliberate re-engage gesture
    (chooser.py:219, "a commit is a deliberate re-engage"); the landing cue
    promises the destination, and D8's attribution contract (host.py:
    657-676) exists precisely so the ear always knows who is talking.
    """
    daemon, _, speaker, sessions, _ = make_daemon(verbosity="everything", foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    sessions.set_speaker("A")

    for i in range(4):
        daemon.history.record("A", "prose", "alpha line {0}".format(i))
        daemon.history.end_message("A")
    daemon.history.start_turn("A")
    daemon.history.record("A", "prose", "alpha newest")
    daemon.history.end_message("A")

    # 1. Mute A, then read through the mute (the ratified M3 behaviour).
    daemon.handle_message(msg(MsgType.STOP_SESSION, "A"))
    daemon.handle_message(msg(MsgType.NAV, "A", to="prev_response"))
    daemon._speak_loop_once()
    daemon._speak_loop_once()
    speaker.spoken.clear()

    # B has real news waiting.
    daemon._enqueue("B", "prose", "the build finished", False)

    # 2. Move: open the chooser and land on B.
    daemon.handle_message(msg(MsgType.CHOOSER_STEP, "A", direction="next"))
    daemon.handle_message(msg(MsgType.CHOOSER_COMMIT, "A"))
    speaker.spoken.clear()

    # 3. What does he actually hear after deliberately moving to B?
    for _ in range(8):
        daemon._speak_loop_once()

    heard = speaker.spoken
    stray = [s for s in heard if "alpha line" in s]
    # RATIFIED: a deliberate commit must not keep reading the session he just
    # left.
    assert not stray
