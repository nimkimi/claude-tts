"""W7 (spec §8): a blocking permission that dies at the ~120s daemon wait must
be MARKED (earcon) and its still-queued text removed — a later ⌃⌘D/read must
never voice the dead ask as answerable. Answered/superseded asks are untouched."""
import threading

from sonari.protocol import PROTOCOL_VERSION
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _request(daemon, session="fg"):
    r = daemon.handle_message(_msg("permission_request", session,
                                   tool="Bash", summary="rm -rf build"))
    assert r == {"__await_decision__": True, "session": session}


def test_enqueue_returns_the_new_items_id():
    daemon, queue, speaker, sessions, config = make_daemon()
    rid = daemon._enqueue("fg", "prose", "hello.", False)
    assert rid == queue._items[-1].id


def test_timeout_plays_the_expiry_earcon_and_cleans_the_queue():
    daemon, queue, speaker, sessions, config = make_daemon()
    _request(daemon)
    item_id = daemon._pending_decisions["fg"]["item_id"]
    # Task 11: the decision hint is enqueued right after the ask, so the ask is
    # no longer necessarily the LAST item -- assert it is tracked by id instead.
    assert any(it.id == item_id for it in queue._items)   # the queued ask is tracked
    r = daemon._await_permission_decision("fg", timeout=0.01)
    assert r == {"decision": None}                 # fail-closed, unchanged
    assert speaker.earcons[-1] == "permission_expired"
    assert all(it.id != item_id for it in queue._items)   # dead ask removed
    assert item_id not in daemon._pending_heard            # marker dropped
    assert daemon.history.last_message("fg")               # history KEPT (archaeology)


def test_answered_ask_gets_no_expiry_and_no_cleanup():
    daemon, queue, speaker, sessions, config = make_daemon()
    _request(daemon)
    pd = daemon._pending_decisions["fg"]
    pd["behavior"] = "allow"
    pd["event"].set()
    r = daemon._await_permission_decision("fg", timeout=1.0)
    assert r == {"decision": "allow"}
    assert "permission_expired" not in speaker.earcons
    # Task 11: the decision hint also lands in this queue -- filter to the ask
    # itself, which is what "still queued, no cleanup" is actually about.
    asks = [it for it in queue._items if it.kind == "permission"]
    assert len(asks) == 1                           # the ask still queued (spoken later)


def test_superseded_ask_gets_no_expiry_and_the_newer_owns_the_slot():
    daemon, queue, speaker, sessions, config = make_daemon()
    _request(daemon)
    out = {}
    t = threading.Thread(
        target=lambda: out.update(r=daemon._await_permission_decision("fg", 5.0)))
    t.start()
    _request(daemon)                               # newer request releases the stale waiter
    t.join(5.0)
    assert not t.is_alive()
    assert out["r"] == {"decision": None}
    assert "permission_expired" not in speaker.earcons
    assert daemon._pending_decisions["fg"]["item_id"] == queue._items[-1].id


def test_in_flight_at_expiry_is_left_to_finish():
    daemon, queue, speaker, sessions, config = make_daemon()
    _request(daemon)
    item = queue.pop_next()                        # already popped: in flight
    daemon._current_item = item
    daemon._await_permission_decision("fg", timeout=0.01)
    assert speaker.earcons[-1] == "permission_expired"     # the honest context beside it
    assert item.id in daemon._pending_heard               # marker left for note_spoken


def test_macos_defaults_gain_permission_expired():
    from sonari.platform.macos.earcon import _DEFAULTS
    assert _DEFAULTS["permission_expired"] == "/System/Library/Sounds/Sosumi.aiff"
