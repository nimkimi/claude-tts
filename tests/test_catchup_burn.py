import threading

from tests.daemon_helpers import make_daemon


def _result(rid, ok, text="", reason=""):
    return {"v": 1, "type": "catchup_result", "request_id": rid,
            "ok": ok, "text": text, "reason": reason}


def _catch_up(session="fg"):
    return {"v": 1, "type": "catch_up", "session": session}


def _inflight(daemon, target="fg", folder="r", slice_end=(0, 0)):
    daemon._catchup = {"id": 1, "target": target, "folder": folder,
                       "slice_end": slice_end, "digest": "Summary unavailable. Last: x.",
                       "cancel": threading.Event(), "phase": "preparing",
                       "render_id": None, "ended": False, "ack_id": None}


def _drain(daemon, n=4):
    for _ in range(n):
        daemon._speak_loop_once()


def test_full_completion_burns_to_pinned_slice_end():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "a.")
    e1 = daemon.history.record("fg", "prose", "b.")
    _inflight(daemon, slice_end=(e1.msg_id, e1.seq))
    daemon.handle_message(_result(1, ok=True, text="All done."))
    _drain(daemon)
    assert daemon._stream("fg").frontier == (e1.msg_id, e1.seq)
    assert daemon._catchup is None


def test_cut_render_suppresses_burn_and_keeps_pile():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    for i in range(3):
        daemon.history.record("fg", "prose", "line {0}.".format(i))
    pile, _ = daemon.history.unheard_from_frontier("fg", None)
    _inflight(daemon, slice_end=(pile[-1].msg_id, pile[-1].seq))
    daemon.handle_message(_result(1, ok=True, text="One. Two. Three."))
    daemon._speak_loop_once()          # frame completes
    speaker.complete = False
    daemon._speak_loop_once()          # body cut
    assert daemon._stream("fg").frontier is None        # NO burn
    assert daemon._catchup is None                      # render invalidated
    still, _ = daemon.history.unheard_from_frontier("fg", daemon._stream("fg").frontier)
    assert len(still) == 3                              # pile intact


def test_cut_middle_drops_the_tail_no_lone_continuation():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    daemon._pending_decisions["fg"] = {"event": None, "behavior": None,
                                       "text": "?", "item_id": None}
    e1 = daemon.history.record("fg", "prose", "b.")
    _inflight(daemon, slice_end=(e1.msg_id, e1.seq))
    daemon.handle_message(_result(1, ok=True, text="Body."))
    daemon._speak_loop_once()          # frame completes
    speaker.complete = False
    daemon._speak_loop_once()          # body cut
    speaker.complete = True
    daemon._speak_loop_once()          # the tail was dropped, nothing to speak
    assert "Decision waiting." not in speaker.spoken
    assert daemon._stream("fg").frontier is None


def test_burn_drops_queued_pile_items_at_or_below_slice_end():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    e0 = daemon.history.record("fg", "prose", "a.")
    e1 = daemon.history.record("fg", "prose", "b.")
    daemon._enqueue("fg", "prose", "a.", False, entry=e0, forward=True)
    daemon._enqueue("fg", "prose", "b.", False, entry=e1, forward=True)
    _inflight(daemon, slice_end=(e1.msg_id, e1.seq))
    daemon.handle_message(_result(1, ok=True, text="Summary body."))
    _drain(daemon, 3)
    st = daemon._stream("fg")
    assert st.frontier == (e1.msg_id, e1.seq)
    assert len(st.queue) == 0                          # a./b. dropped on burn
    assert daemon._catchup is None


def test_burn_never_retreats_frontier():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "x.")
    daemon._stream("fg").frontier = (5, 0)
    _inflight(daemon, slice_end=(2, 0))
    daemon.handle_message(_result(1, ok=True, text="x."))
    _drain(daemon)
    assert daemon._stream("fg").frontier == (5, 0)     # monotonic; behind key is a no-op


def test_cancel_during_render_drops_items_and_no_burn():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    e1 = daemon.history.record("fg", "prose", "b.")
    _inflight(daemon, slice_end=(e1.msg_id, e1.seq))
    daemon.handle_message(_result(1, ok=True, text="Body one. Body two."))
    daemon._speak_loop_once()          # frame plays; body queued
    daemon.handle_message(_catch_up())  # cancel while rendering
    assert daemon._catchup is None
    _drain(daemon, 3)
    assert "Body one. Body two." not in speaker.spoken # render items dropped
    assert "Cancelled." in speaker.spoken
    assert daemon._stream("fg").frontier is None       # no burn


def test_failure_digest_render_burns_to_slice_end():
    # On an adapter-less/failed host the digest IS the only render, so it MUST
    # burn or catch-up would never clear the pile there (owner-flagged, kept).
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    e1 = daemon.history.record("fg", "prose", "b.")
    _inflight(daemon, slice_end=(e1.msg_id, e1.seq))
    daemon.handle_message(_result(1, ok=False, reason="timeout"))
    _drain(daemon)
    assert daemon._stream("fg").frontier == (e1.msg_id, e1.seq)   # digest burns too
    assert daemon._catchup is None


def test_ended_render_completion_clears_bundle_so_next_press_starts_fresh():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("live", cwd="/x/live")   # a live session to voice on
    daemon._catchup = {"id": 1, "target": "gone", "folder": "oldrepo",
                       "slice_end": (0, 0), "digest": "Summary unavailable. Last: x.",
                       "cancel": threading.Event(), "phase": "preparing",
                       "render_id": None, "ended": False, "ack_id": None}
    daemon.handle_message(_result(1, ok=True, text="It finished."))   # 'gone' unregistered -> ended
    _drain(daemon)
    assert daemon._catchup is None                    # ended render CLEARED the bundle
    daemon.history.record("live", "prose", "new output.")
    daemon.handle_message(_catch_up("live"))          # a fresh press must START, not cancel
    _drain(daemon)
    assert "Cancelled." not in speaker.spoken
    assert any("Catching up" in s for s in speaker.spoken)


def test_where_am_i_barge_in_mid_render_leaves_no_orphan_fragment():
    # ⌃⌘W landing mid-body must NOT re-queue the cut render item (on_where_am_i's
    # render_id guard) — a re-queued body would replay frame-less after the
    # readout with no burn. Mid-play is SIMULATED by popping the body and setting
    # daemon._current_item directly (the suite's established idiom for mid-play
    # state; the synchronous harness cannot land handle_message inside speak()),
    # then the cancelled utterance lands via note_spoken(completed=False) exactly
    # as the loop would drive it. The cut semantics themselves (siblings dropped,
    # bundle cleared, no burn) are covered by the other tests in this file; this
    # test targets exactly the no-orphan re-queue guard.
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "a.")
    _inflight(daemon, slice_end=(0, 0))
    daemon.handle_message(_result(1, ok=True, text="Body one. Body two."))
    daemon._speak_loop_once()          # frame plays; body now at the queue head
    body = daemon._stream("fg").queue.pop_next()
    assert body.render_id is not None and body.catchup_burn
    daemon._current_item = body        # mid-play simulation (established idiom)
    daemon.handle_message({"v": 1, "type": "where_am_i", "session": "fg"})  # barge-in mid-body
    daemon.note_spoken(body, False)    # the cancelled speak() lands, as the loop would
    _drain(daemon, 2)
    # The readout spoke; the body was cut and NOT replayed as an orphan; no burn.
    assert any("Voice and keyboard" in s for s in speaker.spoken)
    assert not any(s in ("Body one. Body two.", "Body one.", "Body two.")
                   for s in speaker.spoken)
    assert daemon._catchup is None
    assert daemon._stream("fg").frontier is None
