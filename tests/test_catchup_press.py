from sonari.summarizer import SummarizeResult
from tests.daemon_helpers import make_daemon, FakeSummarizer


def _catch_up(session="fg"):
    return {"v": 1, "type": "catch_up", "session": session}


def test_empty_pile_says_nothing_to_catch_up_and_no_worker():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_catch_up())
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Nothing to catch up."
    assert daemon._catchup is None


def test_ack_announces_pile_magnitude_and_folder():
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=FakeSummarizer())
    sessions.set_foreground("fg", cwd="/x/myrepo")
    for i in range(3):
        daemon.history.record("fg", "prose", "line {0}.".format(i))
    daemon.handle_message(_catch_up())
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Catching up 3 items in myrepo."
    # phase is NOT asserted: with the CATCHUP_RESULT handler registered (T7), the
    # worker may or may not have posted before this tick's drain, so phase after
    # one tick is timing-dependent ("preparing" vs "rendering"). The race-free
    # post-press invariant is only that the press created an in-flight bundle;
    # in-flight phase transitions are covered by
    # test_worker_posts_result_and_press_pins_slice_end and the render tests.
    assert daemon._catchup is not None


def test_singular_item_ack():
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=FakeSummarizer())
    sessions.set_foreground("fg", cwd="/x/myrepo")
    daemon.history.record("fg", "prose", "only one.")
    daemon.handle_message(_catch_up())
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Catching up 1 item in myrepo."


def test_aged_out_rider_rides_the_ack():
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=FakeSummarizer())
    sessions.set_foreground("fg", cwd="/x/myrepo")
    daemon.history.record("fg", "prose", "a.")
    daemon._stream("fg").frontier = (-1, -1)   # behind the oldest entry -> aged_out
    daemon.handle_message(_catch_up())
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Earlier output aged out. Catching up 1 item in myrepo."


def test_worker_posts_result_and_press_pins_slice_end():
    fake = FakeSummarizer(result=SummarizeResult.ok("Done."))
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=fake)
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "one.")
    e1 = daemon.history.record("fg", "prose", "two.")
    daemon.handle_message(_catch_up())
    posted = daemon._catchup_inbox.get(timeout=2)     # worker thread posted it
    assert posted["type"] == "catchup_result"
    assert posted["ok"] is True and posted["text"] == "Done."
    assert fake.calls and "assistant: two." in fake.calls[0]
    assert daemon._catchup["slice_end"] == (e1.msg_id, e1.seq)


def test_no_summarizer_posts_unavailable_without_a_worker():
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=None)
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "a.")
    daemon.handle_message(_catch_up())
    posted = daemon._catchup_inbox.get(timeout=2)
    assert posted["ok"] is False and posted["reason"] == "unavailable"


def test_press_while_in_flight_cancels_no_new_worker():
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=FakeSummarizer())
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "a.")
    daemon.handle_message(_catch_up())
    cancel_event = daemon._catchup["cancel"]
    daemon.handle_message(_catch_up())       # second press = pure cancel
    assert daemon._catchup is None and cancel_event.is_set()
    daemon._speak_loop_once()
    assert "Cancelled." in speaker.spoken


def test_mailbox_drains_on_speak_loop_tick():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._catchup_inbox.put({"v": 1, "type": "catchup_result", "request_id": 999,
                               "ok": False, "text": "", "reason": "error"})
    daemon._speak_loop_once()
    assert daemon._catchup_inbox.empty()     # drained (dispatched; no handler yet = no-op)


def test_render_never_precedes_its_ack_default_digest():
    # No adapter (make_daemon's default summarizer=None): the failure result is
    # mailed SYNCHRONOUSLY at press, so the digest render is drainable on the very
    # next loop tick. It MUST still land AFTER the ack, never ahead of it — the
    # ground-truth magnitude always speaks first (the ack->summary contract).
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=None)
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "a.")
    daemon.handle_message(_catch_up())
    for _ in range(4):
        daemon._speak_loop_once()
    ack = "Catching up 1 item in r."
    digest = "Summary unavailable. Last: a."
    assert ack in speaker.spoken and digest in speaker.spoken
    assert speaker.spoken.index(ack) < speaker.spoken.index(digest)


def test_render_lands_after_ack_when_ack_queued_behind_a_busy_item():
    # A busy utterance is already queued; the ack is enqueued at_front (ahead of
    # it), then the digest result arrives while the ack is still queued ->
    # insert_after keeps the render immediately behind the ack, never ahead of it.
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=None)
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "a.")
    daemon._enqueue("fg", "prose", "Busy line.", False)   # already queued ahead
    daemon.handle_message(_catch_up())
    for _ in range(5):
        daemon._speak_loop_once()
    ack = "Catching up 1 item in r."
    digest = "Summary unavailable. Last: a."
    assert speaker.spoken.index(ack) < speaker.spoken.index(digest)
