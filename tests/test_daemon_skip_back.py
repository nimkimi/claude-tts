"""skip_back ('previous item'): each press re-speaks the previous message group,
stepping further back; new content or repeat resets the cursor to the latest."""
from tests.daemon_helpers import make_daemon


def _drain(queue):
    items = []
    while True:
        it = queue.pop_next()
        if it is None:
            break
        items.append(it)
    return items


def _seed_three_messages(daemon):
    h = daemon.history
    h.record("fg", "prose", "m0a"); h.record("fg", "prose", "m0b"); h.end_message("fg")
    h.record("fg", "choice", "m1"); h.end_message("fg")
    h.record("fg", "prose", "m2-current")   # current (open) message


def test_skip_back_walks_back_one_message_per_press():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    _seed_three_messages(daemon)

    daemon.handle_message({"type": "skip_back", "session": "fg"})
    assert [s.text for s in _drain(queue)] == ["m1"]          # previous
    assert daemon._back_step["fg"] == 1

    daemon.handle_message({"type": "skip_back", "session": "fg"})
    assert [s.text for s in _drain(queue)] == ["m0a", "m0b"]  # two back (whole group)
    assert daemon._back_step["fg"] == 2


def test_skip_back_at_start_of_history_announces_and_does_not_overstep():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    _seed_three_messages(daemon)
    for _ in range(3):
        daemon.handle_message({"type": "skip_back", "session": "fg"})
    last = _drain(queue)
    assert any("Start of history" in s.text for s in last)
    assert daemon._back_step["fg"] == 2          # capped; never ran past the oldest


def test_new_content_resets_the_back_cursor():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    _seed_three_messages(daemon)
    daemon.handle_message({"type": "skip_back", "session": "fg"})
    assert daemon._back_step.get("fg") == 1
    # a new prose message arrives -> cursor resets
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "brand new", "index": 0, "final": True})
    assert "fg" not in daemon._back_step


def test_repeat_resets_the_back_cursor():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    _seed_three_messages(daemon)
    daemon.handle_message({"type": "skip_back", "session": "fg"})
    assert daemon._back_step.get("fg") == 1
    daemon.handle_message({"type": "repeat", "session": "fg"})
    assert "fg" not in daemon._back_step
