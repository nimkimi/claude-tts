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
    assert [s.text for s in _drain(queue)] == ["m2"]
    assert daemon._nav_cursor["fg"] == 2                        # never wrapped to 0


def test_first_and_last_jump():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    _nav(daemon, "first")
    assert [s.text for s in _drain(queue)] == ["m0a", "m0b"]
    _nav(daemon, "last")
    assert [s.text for s in _drain(queue)] == ["m2"]


def test_new_content_resets_cursor_to_latest():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _seed(daemon)
    _nav(daemon, "prev")
    assert daemon._nav_cursor.get("fg") == 1
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "new", "index": 0, "final": True})
    assert "fg" not in daemon._nav_cursor


def test_nav_with_empty_history_announces():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _nav(daemon, "prev")
    assert any("Nothing to navigate" in s.text for s in _drain(queue))
