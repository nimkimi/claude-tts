"""Audit pins: the frontier that gates catch-up silently loses a whole pile of
unheard content, in two independent ways.

See /Users/Nima.Hakimi/projects/private/claude-tts/scratchpad/e3-review/test-audit/HUNT-RESULTS.json
findings 1 and 2 for the full adjudication.
"""
import pytest

from tests.daemon_helpers import make_daemon
from sonari.history import SessionHistory
from sonari.protocol import MsgType, PROTOCOL_VERSION


def msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


@pytest.mark.xfail(
    strict=True,
    reason="BUG-2 (pre-existing at 073b82b, per the hunter's own base-tree "
           "control run): ctrl-cmd-S resume promises the dropped pile stays "
           "catch-up-reachable, but the very next post-resume reply's "
           "frontier advance buries the whole pile with no warning; awaiting "
           "owner fix decision -- see HUNT dossier finding 1.",
)
def test_bug2_ctrl_cmd_s_resume_buries_the_dropped_pile_behind_one_reply():
    """BUG-2 (CONFIRMED, finding 1, severity high).

    mechanism: src/sonari/daemon/features/playback.py:135-139 on_stop_session's
    resuming branch drops the pre-start queue and states, in its own comment,
    that "the pile persists in the history transcript BEHIND the frozen
    frontier ... reachable later by SP5's catch-up". The freeze is only
    instantaneous: src/sonari/session_stream.py:56-61 advance_frontier takes
    the MAX of the current frontier and the new key, and
    src/sonari/daemon/host.py:721-729 note_spoken advances it on the very
    next completed forward=True item. That item's key is strictly larger than
    the entire dropped pile's, so the frontier jumps clean over the whole pile
    in ONE step; src/sonari/history.py:173-196 unheard_from_frontier then
    returns nothing for it, and history.py:195's aged_out check (deque-
    eviction only) never fires -- no warning either.

    ratified basis: the on_stop_session comment's own promise (playback.py:
    135-139), and docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-
    design.md:136-138 -- the frontier "only ever advances and NEVER MOVES OVER
    UNHEARD OUTPUT" outside two ratified, SPOKEN jumps (the deliberate
    pile-skip gesture; SP5 catch-up's own burn). This jump is neither: no
    gesture requested it and nothing is spoken.
    """
    daemon, _, speaker, sessions, _ = make_daemon(verbosity="everything", foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.set_speaker("A")

    def _record_and_enqueue(text, forward):
        entry = daemon.history.record("A", "prose", text)
        daemon.history.end_message("A")
        daemon._enqueue("A", "prose", text, False, entry=entry, forward=forward)

    # 1. MUTE A, then accumulate a pile while muted (an "away turn").
    daemon.handle_message(msg(MsgType.STOP_SESSION, "A"))
    daemon._speak_loop_once()          # hears "Stopped."
    speaker.spoken.clear()
    for i in range(10):
        _record_and_enqueue("away line {0}".format(i), True)
    assert daemon._stream("A").frontier is None
    assert len(daemon._stream("A").queue) == 10

    # 2. ctrl-cmd-S RESUME: the promise holds INSTANTANEOUSLY -- the queue is
    #    dropped but nothing has advanced the frontier yet.
    daemon.handle_message(msg(MsgType.STOP_SESSION, "A"))
    assert daemon._stream("A").stopped is False
    assert daemon._stream("A").frontier is None
    entries, _ = daemon.history.unheard_from_frontier("A", daemon._stream("A").frontier)
    assert len(entries) == 10          # the pile IS still reachable right here

    # 3. Drain "Resumed.", then ONE post-resume reply completes.
    for _ in range(3):
        daemon._speak_loop_once()
    speaker.spoken.clear()
    _record_and_enqueue("the new reply", True)
    daemon._speak_loop_once()          # completes, forward=True -> advances the frontier
    assert speaker.spoken == ["the new reply"]

    # 4. Press catch-up. RATIFIED: the 10-item pile must still be reachable
    #    and announced -- never a silent "Nothing to catch up."
    daemon.handle_message(msg(MsgType.CATCH_UP, "A"))
    for _ in range(5):
        daemon._speak_loop_once()
    ack = next((s for s in speaker.spoken
                if s.startswith("Catching up") or s == "Nothing to catch up."), None)
    assert ack != "Nothing to catch up."


@pytest.mark.xfail(
    strict=True,
    reason="BUG-5 (pre-existing at 073b82b, per the hunter's own base-tree "
           "control run): aged_out is unconditionally False whenever a "
           "session's frontier has never advanced, so a deque-cap eviction "
           "on a muted-from-birth session starts catch-up silently mid-pile "
           "instead of announcing the gap; awaiting owner fix decision -- "
           "see HUNT dossier finding 2.",
)
def test_bug5_aged_out_is_silently_false_when_the_frontier_has_never_advanced():
    """BUG-5 (CONFIRMED, finding 2, severity medium).

    mechanism: src/sonari/history.py:189-196 unheard_from_frontier:
    `if frontier is None: return list(d), False` returns aged_out=False
    UNCONDITIONALLY, before the eviction test on :195
    (`aged_out = frontier < (d[0].msg_id, d[0].seq)`) is ever reached.
    frontier stays None for any session whose stream never completed a
    forward=True item (host.py:721-729) -- e.g. a background/muted-from-birth
    session, exactly the session catch-up exists for. When the history
    deque's maxlen cap overflows and silently drops the oldest entries, a
    session with a real (non-None) frontier gets the fail-loud "earlier
    output aged out" cue; a session whose frontier never advanced gets
    nothing -- the exact same eviction, announced in one case and silent in
    the other, differing only in whether the frontier had ever moved.

    ratified basis: docs/superpowers/specs/2026-07-17-sonari-sp5-catchup-
    design.md:256-257 (Sec 9 Edges, R-1): "aged_out=True -> the cue rides the
    ack; the slice starts at the oldest surviving entry -- announced, NEVER a
    silent mid-pile start." history.py:186-188 restates the identical
    contract in the function's own docstring. The None-frontier branch
    contradicts its own documented behaviour.
    """
    daemon, _, speaker, sessions, _ = make_daemon(verbosity="everything", foreground="A")
    sessions.register("A", cwd="/x/alpha")
    # A tiny cap so the deque overflows fast and deterministically. A's
    # stream is muted from birth (never resumed, never spoke a forward=True
    # item) -- its frontier stays None throughout, exactly the shape under
    # test. Direct swap: no daemon API exposes history_cap post-construction.
    daemon.history = SessionHistory(cap=5)

    for i in range(8):
        daemon.history.record("A", "prose", "line {0}".format(i))
        daemon.history.end_message("A")
    # Only the newest 5 of 8 entries survive the cap -- 3 were silently
    # evicted, and A's frontier (never touched) is still None.
    assert daemon._stream("A").frontier is None
    assert len(daemon.history._entries["A"]) == 5

    daemon.handle_message(msg(MsgType.CATCH_UP, "A"))
    for _ in range(5):
        daemon._speak_loop_once()
    ack = next((s for s in speaker.spoken if s.startswith("Catching up")
                or s.startswith("Earlier output aged out.")), None)

    # RATIFIED: a deque-cap eviction the session never dealt with must speak
    # the fail-loud gap warning, never a silent mid-pile start.
    assert ack is not None and ack.startswith("Earlier output aged out.")
