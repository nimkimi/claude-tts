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


def test_prev_steps_back_one_message_from_latest():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    _nav(daemon, "prev")
    assert [s.text for s in _drain(queue)] == ["m1"]
    _nav(daemon, "prev")
    assert [s.text for s in _drain(queue)] == ["m0a", "m0b"]   # whole message


def test_prev_clamps_at_first():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    for _ in range(5):
        _nav(daemon, "prev")
    assert [s.text for s in _drain(queue)] == ["m0a", "m0b"]
    assert daemon._nav_cursor["fg"] == 0


def test_next_clamps_at_last_no_wrap():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    _nav(daemon, "first"); _drain(queue)                       # cursor at m0
    _nav(daemon, "next"); assert [s.text for s in _drain(queue)] == ["m1"]
    _nav(daemon, "next"); assert [s.text for s in _drain(queue)] == ["m2"]
    _nav(daemon, "next")                                       # at last -> re-read m2
    assert [s.text for s in _drain(queue)] == ["m2"]           # never wrapped to 0
    # reaching the latest clears the cursor: "following live" again, not pinned
    assert daemon._nav_cursor.get("fg") is None


def test_first_and_last_jump():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    _nav(daemon, "first")
    assert [s.text for s in _drain(queue)] == ["m0a", "m0b"]
    _nav(daemon, "last")
    assert [s.text for s in _drain(queue)] == ["m2"]


def test_streaming_content_does_not_move_the_cursor_but_flush_resets_it():
    # The streaming-nav bug fix: new paragraphs arriving while you navigate must
    # NOT yank the cursor to latest; only a new prompt (FLUSH) clears it.
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    _nav(daemon, "prev")
    anchored = daemon._nav_cursor.get("fg")
    assert anchored is not None
    # more content streams in -> cursor stays put
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "More streamed text.", "index": 9, "final": False})
    assert daemon._nav_cursor.get("fg") == anchored
    # a new prompt clears navigation
    daemon.handle_message({"type": "flush", "session": "fg"})
    assert "fg" not in daemon._nav_cursor


def test_live_prose_suppressed_while_reading_past_then_resumes_at_last():
    # #4: while parked on an earlier message, live prose is recorded but NOT
    # enqueued (no interleaving with the replay); returning to the latest clears
    # the cursor so subsequent live prose is spoken again.
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)                                   # m0, m1, m2
    _drain(queue)
    _nav(daemon, "first"); _drain(queue)            # park on m0 (a past message)
    assert daemon._nav_cursor.get("fg") is not None
    # live prose streams in -> recorded to history, but not queued
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Live paragraph.\n\n", "index": 7, "final": False})
    assert len(queue) == 0
    assert any("Live paragraph" in e.text for e in daemon.history.unheard("fg"))
    # return to the latest -> cursor clears (follow live)
    _nav(daemon, "last"); _drain(queue)
    assert daemon._nav_cursor.get("fg") is None
    # subsequent live prose is spoken again
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Another live one.\n\n", "index": 8, "final": False})
    assert any("Another live one" in s.text for s in _drain(queue))


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
    _nav(daemon, "prev")                            # latest(para3) -> para2
    assert [s.text for s in _drain(queue)] == ["Para two sentence."]
    _nav(daemon, "first")                           # -> para1
    assert [s.text for s in _drain(queue)] == ["Para one sentence."]
    _nav(daemon, "last")                            # -> para3
    assert [s.text for s in _drain(queue)] == ["Para three sentence."]
