"""Message-cursor navigation (nav next/prev/first/last) over the current turn.
Each move cuts current speech, clears the queue, and replays the target message;
next/prev clamp at the ends (no wrap); new content snaps the cursor to latest."""
from tests.daemon_helpers import make_daemon


def _drain(queue):
    items = []
    while True:
        it = queue.pop_next()
        if it is None:
            break
        items.append(it)
    return items


def _seed(daemon):
    # Current turn = 3 messages: m0 (two sentences), m1, m2 (latest).
    h = daemon.history
    h.record("fg", "prose", "m0a"); h.record("fg", "prose", "m0b"); h.end_message("fg")
    h.record("fg", "prose", "m1"); h.end_message("fg")
    h.record("fg", "prose", "m2")


def _nav(daemon, to):
    daemon.handle_message({"type": "nav", "to": to, "session": "fg"})


def test_prev_steps_back_one_message_then_plays_forward():
    # Seek-and-play: stepping back lands on the previous item AND reads every
    # later one, so playback continues instead of stopping after one item.
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    _nav(daemon, "prev")
    assert [s.text for s in _drain(queue)] == ["m1", "m2"]
    _nav(daemon, "prev")
    assert [s.text for s in _drain(queue)] == ["m0a", "m0b", "m1", "m2"]


def test_prev_clamps_at_first():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    for _ in range(5):
        _nav(daemon, "prev")
    assert [s.text for s in _drain(queue)] == ["m0a", "m0b", "m1", "m2"]
    assert daemon._stream("fg").nav_cursor == 0


def test_next_clamps_at_last_no_wrap():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    _nav(daemon, "first"); _drain(queue)                       # cursor at m0
    _nav(daemon, "next"); assert [s.text for s in _drain(queue)] == ["m1", "m2"]
    _nav(daemon, "next"); assert [s.text for s in _drain(queue)] == ["m2"]
    _nav(daemon, "next")                                       # at last -> re-read m2
    assert [s.text for s in _drain(queue)] == ["m2"]           # never wrapped to 0
    # reaching the latest clears the cursor: "following live" again, not pinned
    assert daemon._stream("fg").nav_cursor is None


def test_first_and_last_jump():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    _nav(daemon, "first")
    assert [s.text for s in _drain(queue)] == ["m0a", "m0b", "m1", "m2"]   # whole turn
    _nav(daemon, "last")
    assert [s.text for s in _drain(queue)] == ["m2"]


def test_streaming_content_does_not_move_the_cursor_but_flush_resets_it():
    # The streaming-nav bug fix: new paragraphs arriving while you navigate must
    # NOT yank the cursor to latest; only a new prompt (FLUSH) clears it.
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    _nav(daemon, "prev")
    anchored = daemon._stream("fg").nav_cursor
    assert anchored is not None
    # more content streams in -> cursor stays put
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "More streamed text.", "index": 9, "final": False})
    assert daemon._stream("fg").nav_cursor == anchored
    # a new prompt clears navigation
    daemon.handle_message({"type": "flush", "session": "fg"})
    assert daemon._stream("fg").nav_cursor is None


def test_nav_then_live_prose_continues_after_replay_no_interleave():
    # #4: after navigating back, newly streamed prose enqueues AFTER the replayed
    # items (a contiguous catch-up) rather than jumping into the middle of the
    # replay. Seek-and-play makes the in-between items play, so there is no jump.
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)                                   # m0, m1, m2
    _drain(queue)
    _nav(daemon, "prev")                            # queues m1, m2 (seek-and-play)
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Live continues.\n\n", "index": 7, "final": False})
    texts = [s.text for s in _drain(queue)]
    assert texts[:2] == ["m1", "m2"]
    assert "Live continues." in texts
    assert texts.index("Live continues.") > texts.index("m2")   # after, not interleaved


def test_nav_replays_then_live_prose_for_foreground_is_spoken():
    """The flip: nav has no voice-claim. It replays the foreground session's history
    into the foreground stream, and live prose streaming in afterwards enqueues there
    too and is drained/spoken. Was test_nav_makes_foreground_session_the_voice_owner."""
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    _nav(daemon, "prev")
    # live prose for fg now enqueues into fg's stream and is spoken
    _drain(queue)
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Live after nav. ", "index": 9, "final": False})
    assert [s.text for s in _drain(queue)] == ["Live after nav."]


# Removed test_nav_does_not_steal_voice_from_a_streaming_session: there is no voice
# ownership to protect in the Stage 2 flip; "nav enqueues replay items for fg" is
# covered by test_nav_replays_then_live_prose_for_foreground_is_spoken above.


def test_nav_with_empty_history_announces():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _nav(daemon, "prev")
    items = _drain(queue)
    # Exact equality, not substring: a prefix/suffix corruption of this spoken
    # sentence must be caught, not silently pass a looser "contains" check.
    assert [s.text for s in items] == ["Nothing to navigate yet."]
    # Not a decision: flipping this would exempt an ordinary empty-history cue
    # from queue-cap eviction and make jump_to_decision/has_decision treat a
    # browsing session as though it had an unanswered decision pending.
    assert all(it.is_decision is False for it in items)


def test_nav_steps_by_paragraph_within_one_message():
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon.handle_message({
        "type": "prose", "session": "fg",
        "delta": "Para one sentence.\n\nPara two sentence.\n\nPara three sentence.",
        "index": 0, "final": True})
    _drain(queue)                                   # clear the spoken queue
    # the one message became three paragraph 'items'
    assert len(daemon.history.message_ids("fg")) == 3
    _nav(daemon, "prev")                            # latest(para3) -> para2 onward
    assert [s.text for s in _drain(queue)] == ["Para two sentence.", "Para three sentence."]
    _nav(daemon, "first")                           # -> para1 onward (whole message)
    assert [s.text for s in _drain(queue)] == [
        "Para one sentence.", "Para two sentence.", "Para three sentence."]
    _nav(daemon, "last")                            # -> para3 only
    assert [s.text for s in _drain(queue)] == ["Para three sentence."]


def test_nav_stays_within_current_turn_after_new_prompt():
    # Stage 4 discriminator (the existing nav suite is blind to this — every other
    # test seeds a single turn). History persists across turns, but the existing
    # within-turn nav must NOT walk into a prior turn; that's Stage 5's two-level nav.
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "T1 alpha.", "index": 0, "final": True})
    daemon.handle_message({"type": "flush", "session": "fg"})        # open turn 2
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "T2 one.", "index": 0, "final": True})
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "T2 two.", "index": 1, "final": True})
    _drain(queue)                                                    # clear live playback
    _nav(daemon, "first")                                           # first of CURRENT turn
    texts = [s.text for s in _drain(queue)]
    assert texts == ["T2 one.", "T2 two."]                          # whole current turn
    assert "T1 alpha." not in texts                                 # never the prior turn


def test_nav_prev_clamps_at_current_turn_start_not_prior_turn():
    # After a new prompt with a single message in the fresh turn, 'prev' clamps on
    # that message and never reaches into the prior turn's transcript.
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Prior turn.", "index": 0, "final": True})
    daemon.handle_message({"type": "flush", "session": "fg"})        # open new turn
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Current turn.", "index": 0, "final": True})
    _drain(queue)
    for _ in range(3):
        _nav(daemon, "prev")                                        # clamps, no wrap/leak
    texts = [s.text for s in _drain(queue)]
    assert texts == ["Current turn."]
    assert "Prior turn." not in texts


def test_flush_resets_nav_turn_anchor():
    # A new prompt snaps the response anchor back to live (Stage 5).
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon._stream("fg").nav_turn = 5
    daemon.handle_message({"type": "flush", "session": "fg"})
    assert daemon._stream("fg").nav_turn is None


def test_within_nav_operates_on_anchored_past_turn():
    # With the anchor on a past turn, within-response nav reads THAT turn, not live.
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "T0 a.", "index": 0, "final": True})   # turn 0
    daemon.handle_message({"type": "flush", "session": "fg"})              # -> turn 1 (live)
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "T1 a.", "index": 0, "final": True})
    _drain(queue)
    daemon._stream("fg").nav_turn = 0          # anchor on the PAST turn
    daemon._stream("fg").nav_cursor = None
    _nav(daemon, "first")
    assert [s.text for s in _drain(queue)] == ["T0 a."]   # the anchored turn, not "T1 a."


def test_within_nav_falls_back_to_live_when_anchor_turn_evicted():
    # Stage 5 (anchor-eviction guard): if the anchored turn was evicted by the rolling
    # cap mid-session, within-nav falls back to the live turn rather than announcing empty.
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Live one.", "index": 0, "final": True})
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Live two.", "index": 1, "final": True})
    _drain(queue)
    daemon._stream("fg").nav_turn = 999        # an anchor that no longer exists
    daemon._stream("fg").nav_cursor = None
    _nav(daemon, "first")
    assert [s.text for s in _drain(queue)] == ["Live one.", "Live two."]   # navigated LIVE
    assert daemon._stream("fg").nav_turn is None                           # anchor cleared


def _responses(daemon, session, texts):
    # Each FLUSH opens a new turn; each prose is that turn's single response.
    for i, t in enumerate(texts):
        daemon.handle_message({"type": "flush", "session": session})
        daemon.handle_message({"type": "prose", "session": session,
                               "delta": t, "index": i, "final": True})


def test_prev_response_reads_previous_response_with_relative_cue():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["R1.", "R2.", "R3."])        # turns: live = R3
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})
    assert [s.text for s in _drain(queue)] == ["1 response back.", "R2."]


def test_prev_response_clamps_at_oldest_with_boundary_cue():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["R1.", "R2.", "R3."])
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})  # R2
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})  # R1 (oldest)
    assert [s.text for s in _drain(queue)] == ["Oldest response.", "R1."]
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})  # clamp
    assert [s.text for s in _drain(queue)] == ["Oldest response.", "R1."]


def test_next_response_returns_to_latest_with_boundary_cue():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["R1.", "R2.", "R3."])
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})  # R2
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "next_response", "session": "fg"})  # back to live R3
    assert [s.text for s in _drain(queue)] == ["Back to the latest.", "R3."]
    assert daemon._stream("fg").nav_turn is None           # anchored back to live


def test_next_response_steps_forward_one_turn_at_a_time():
    # Every other next_response test goes straight to the newest turn in one
    # step, where the min(..., len(turns)-1) clamp masks +1 vs +2 (both land
    # on the same boundary index). With 4+ turns, parking on the OLDEST and
    # pressing next_response once must land on the SECOND-oldest turn, not
    # skip past it -- and its cue's plural branch ("2 responses back.") must
    # render intact, not corrupted.
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["R1.", "R2.", "R3.", "R4."])
    _drain(queue)
    for _ in range(3):
        daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})
        _drain(queue)
    # now parked on R1, the oldest
    daemon.handle_message({"type": "nav", "to": "next_response", "session": "fg"})
    assert [s.text for s in _drain(queue)] == ["2 responses back.", "R2."]


def test_response_nav_with_one_response_says_no_other():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["Only."])
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})
    assert [s.text for s in _drain(queue)] == ["No other response."]


def test_response_nav_with_no_history_says_nothing_to_navigate():
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})
    assert [s.text for s in _drain(queue)] == ["Nothing to navigate yet."]


def test_live_prose_while_parked_on_past_response_enqueues_after_replay():
    # Advisor pin: parked on a past response, new live prose for the live turn enqueues
    # AFTER the replayed items (no buffering, no yank to live). Same invariant as
    # within-turn nav's "streaming continues after replay".
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["R1.", "R2.", "R3."])
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})  # park on R2
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Live more.", "index": 9, "final": True})        # live (R3) prose
    texts = [s.text for s in _drain(queue)]
    assert "R2." in texts and "Live more." in texts
    assert texts.index("Live more.") > texts.index("R2.")    # after the replay, not interleaved
    assert daemon._stream("fg").nav_turn is not None         # still parked, not yanked to live


def test_prev_response_then_next_step_anchors_cursor_at_the_responses_start():
    # nav_cursor must anchor at the START of the response _nav_response just
    # jumped to (None == "follow live"), so a FOLLOWING single-step nav starts
    # from there. Chains prev_response with a message-step nav -- nothing else
    # in the suite exercises that combination.
    daemon, queue, *_ = make_daemon(foreground="fg")
    h = daemon.history
    h.record("fg", "prose", "A0"); h.end_message("fg")
    h.record("fg", "prose", "A1"); h.end_message("fg")
    h.record("fg", "prose", "A2"); h.end_message("fg")
    h.start_turn("fg")
    h.record("fg", "prose", "B0"); h.end_message("fg")
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})   # parks on turn A
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "next", "session": "fg"})
    assert [s.text for s in _drain(queue)] == ["A1", "A2"]


def test_nav_with_no_to_key_defaults_to_prev():
    # A malformed/legacy client message omitting "to" entirely must fall back
    # to "prev", not silently drop the press (total silence on an uncrossed
    # target).
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    daemon.handle_message({"type": "nav", "session": "fg"})
    assert [s.text for s in _drain(queue)] == ["m1", "m2"]


def test_back_to_latest_with_empty_live_turn_pins_anchor_not_none():
    # Deferred Stage-5 Minor: a FLUSH after the last prose opens an EMPTY live turn
    # (excluded from turn_ids). Navigating back to the latest must pin the anchor to the
    # newest CONTENT turn (not None == the empty live turn), so a follow-up within-nav
    # still works instead of saying "Nothing to navigate yet."
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["R1.", "R2."])         # turns with content; live turn has R2.
    _drain(queue)
    daemon.handle_message({"type": "flush", "session": "fg"})   # opens an EMPTY live turn
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})  # park back
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "next_response", "session": "fg"})  # to latest
    cues = [s.text for s in _drain(queue)]
    assert "Back to the latest." in cues                         # cue unchanged
    st = daemon._stream("fg")
    assert st.nav_turn is not None                               # PINNED, not the empty live turn
    assert st.nav_turn in daemon.history.turn_ids("fg")          # a real navigable turn
    # within-nav over the pinned turn works (no dead-end cue)
    daemon.handle_message({"type": "nav", "to": "prev", "session": "fg"})
    assert "Nothing to navigate yet." not in [s.text for s in _drain(queue)]
