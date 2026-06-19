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
    assert any("Nothing to navigate" in s.text for s in _drain(queue))


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
