"""Audit pin: ctrl-cmd-R (repeat) answers with a stale pre-mute utterance
after a muted read, with no cue that it is stale.

See /Users/Nima.Hakimi/projects/private/claude-tts/scratchpad/e3-review/test-audit/HUNT-RESULTS.json
finding 6 for the full adjudication.
"""
import pytest

from tests.daemon_helpers import make_daemon
from sonari.protocol import MsgType, PROTOCOL_VERSION


def msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


@pytest.mark.xfail(
    strict=True,
    reason="BUG-6 (new-in-receipts): ctrl-cmd-R re-speaks a stale pre-mute "
           "utterance instead of the content a muted read just delivered, "
           "with no cue that it is stale; awaiting owner fix decision -- see "
           "HUNT dossier finding 6.",
)
def test_bug6_repeat_after_a_muted_read_answers_with_a_stale_pre_mute_utterance():
    """BUG-6 (CONFIRMED, finding 6, severity medium).

    mechanism: src/sonari/daemon/host.py:1567 captures self._last_utterance
    only `elif completed and not item.control_cue:`. The receipts build made
    a muted read (M3, spec 4.4) DELIVER its content by flagging it
    control_cue=True (navigation.py:57-58/106-107) -- so every line just
    heard through the mute is invisible to that capture. ctrl-cmd-R
    (REPEAT_LAST) then answers with whatever was captured BEFORE the mute.

    ratified basis: protocol.py:47's REPEAT_LAST contract: "re-speak the last
    completed content utterance" -- the last one actually heard, not a stale
    one from before a since-superseded mute.
    """
    daemon, _, speaker, sessions, _ = make_daemon(verbosity="everything", foreground="B")
    sessions.register("B", cwd="/x/bravo")
    sessions.set_speaker("B")

    for i in range(3):
        daemon.history.record("B", "prose", "old line {0}".format(i))
        daemon.history.end_message("B")
    daemon.history.start_turn("B")
    daemon.history.record("B", "prose", "the newest line")
    daemon.history.end_message("B")

    # 0. Hear one ordinary (un-muted) line -- seeds _last_utterance.
    daemon._enqueue("B", "prose", "the newest line", False)
    daemon._speak_loop_once()
    speaker.spoken.clear()

    # 1. MUTE.
    daemon.handle_message(msg(MsgType.STOP_SESSION, "B"))
    daemon._speak_loop_once()          # "Stopped."
    speaker.spoken.clear()

    # 2. READ GESTURE through the mute -- the ratified M3 behaviour.
    daemon.handle_message(msg(MsgType.NAV, "B", to="prev_response"))
    for _ in range(3):
        daemon._speak_loop_once()
    heard = list(speaker.spoken)
    assert heard          # the muted read did deliver content (M3)
    last_content_heard = heard[-1]
    speaker.spoken.clear()

    # 3. "Say that again."
    daemon.handle_message(msg(MsgType.REPEAT_LAST, "B"))
    for _ in range(3):
        daemon._speak_loop_once()

    repeated = speaker.spoken[0] if speaker.spoken else None
    # RATIFIED: repeat re-speaks the last thing actually heard.
    assert repeated == last_content_heard
