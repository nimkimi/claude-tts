from __future__ import annotations

import threading
import unittest.mock as mock

from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _dispatch(daemon, msg):
    return daemon._handle_message_guarded(msg)


def test_permission_request_enqueues_prompt_and_registers_pending():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("S1", cwd="/x/alpha")
    # call the handler directly under a transaction to inspect the sentinel
    with daemon._state.transaction():
        ret = daemon.handle_message(
            {"type": MsgType.PERMISSION_REQUEST, "session": "S1",
             "tool": "Bash", "summary": "rm -rf build"})
    assert ret == {"__await_decision__": True, "session": "S1"}
    assert "S1" in daemon._pending_decisions
    # the prompt was enqueued as a decision item on S1
    st = daemon._stream("S1")
    assert any(it.is_decision and "rm -rf build" in it.text for it in st.queue._items)


def test_await_returns_behavior_when_signalled():
    daemon, *_ = make_daemon()
    daemon._pending_decisions["S1"] = {"event": threading.Event(), "behavior": None}
    daemon._pending_decisions["S1"]["behavior"] = "allow"
    daemon._pending_decisions["S1"]["event"].set()
    assert daemon._await_permission_decision("S1", 1.0) == {"decision": "allow"}
    assert "S1" not in daemon._pending_decisions   # popped after resolution


def test_await_times_out_to_none():
    daemon, *_ = make_daemon()
    daemon._pending_decisions["S1"] = {"event": threading.Event(), "behavior": None}
    assert daemon._await_permission_decision("S1", 0.05) == {"decision": None}
    assert "S1" not in daemon._pending_decisions


def test_answer_sets_behavior_and_confirms_for_focused_session():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("S1", cwd="/x/alpha")
    ev = threading.Event()
    daemon._pending_decisions["S1"] = {"event": ev, "behavior": None}
    _dispatch(daemon, {"type": MsgType.ANSWER_PERMISSION, "behavior": "allow"})
    assert daemon._pending_decisions["S1"]["behavior"] == "allow"
    assert ev.is_set()
    assert speaker.cancels > 0          # barge-in happened
    st = daemon._stream("S1")
    assert any("Approved." in it.text for it in st.queue._items)


def test_answer_on_session_without_pending_is_error_no_route():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("A", cwd="/x/a")     # focused/foreground = A (no pending)
    other = threading.Event()
    daemon._pending_decisions["B"] = {"event": other, "behavior": None}  # B has the prompt
    _dispatch(daemon, {"type": MsgType.ANSWER_PERMISSION, "behavior": "allow"})
    assert daemon._pending_decisions["B"]["behavior"] is None   # B was NOT answered
    assert not other.is_set()
    assert "error" in speaker.earcons   # error earcon played


def test_stale_prev_decision_released_on_new_request():
    # A second PERMISSION_REQUEST for the same session must release the stale waiter
    # (so it unblocks with None / fail-closed) and replace the entry with a new one.
    daemon, *_ = make_daemon()
    with daemon._state.transaction():
        daemon.handle_message(
            {"type": MsgType.PERMISSION_REQUEST, "session": "S1",
             "tool": "Bash", "summary": "first cmd"})
    prev_pd = daemon._pending_decisions["S1"]
    prev_event = prev_pd["event"]
    # Second request for the same session — should release the stale waiter.
    with daemon._state.transaction():
        daemon.handle_message(
            {"type": MsgType.PERMISSION_REQUEST, "session": "S1",
             "tool": "Bash", "summary": "second cmd"})
    assert prev_event.is_set()                                  # stale waiter released
    assert prev_pd["behavior"] is None                          # behavior still None → fail-closed
    assert daemon._pending_decisions["S1"] is not prev_pd       # replaced with a new entry


def test_handle_message_guarded_permission_request_raises_returns_fail_closed():
    # Fix 1: if on_permission_request itself raises (bad handler path), the guarded
    # wrapper must return {"decision": None} immediately so the blocking hook doesn't
    # wait its full 130 s socket timeout before falling through.
    daemon, *_ = make_daemon()
    with mock.patch.object(daemon, "handle_message", side_effect=RuntimeError("boom")):
        result = daemon._handle_message_guarded(
            {"type": MsgType.PERMISSION_REQUEST, "session": "S1"})
    assert result == {"decision": None}


def test_blocking_round_trip_request_then_answer():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("S1", cwd="/x/alpha")
    out = {}

    def asker():
        out["reply"] = daemon._handle_message_guarded(
            {"type": MsgType.PERMISSION_REQUEST, "session": "S1",
             "tool": "Bash", "summary": "deploy"})

    t = threading.Thread(target=asker)
    t.start()
    # wait until the request has registered its pending decision, then answer
    deadline = threading.Event()
    for _ in range(200):
        if "S1" in daemon._pending_decisions:
            break
        deadline.wait(0.01)
    daemon._handle_message_guarded({"type": MsgType.ANSWER_PERMISSION, "behavior": "deny"})
    t.join(timeout=5.0)
    assert out["reply"] == {"decision": "deny"}
