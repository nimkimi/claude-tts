from __future__ import annotations

import threading

from tests.daemon_helpers import make_daemon


def test_session_state_stores_exact_lock():
    from sonari.daemon.state import SessionState

    lock = threading.Lock()
    state = SessionState(lock)
    assert state._lock is lock


def test_transaction_acquires_and_releases_lock():
    from sonari.daemon.state import SessionState

    lock = threading.Lock()
    state = SessionState(lock)

    with state.transaction():
        assert lock.locked()

    assert not lock.locked()


def test_transaction_releases_lock_on_exception():
    from sonari.daemon.state import SessionState

    lock = threading.Lock()
    state = SessionState(lock)

    try:
        with state.transaction():
            assert lock.locked()
            raise ValueError("boom")
    except ValueError:
        pass

    assert not lock.locked()


def test_sessionstate_owns_the_global_ledger():
    from sonari.daemon.state import SessionState
    s = SessionState(threading.Lock())
    assert s._streams == {}
    assert s._pending_heard == {}
    assert s._next_id == 0
    assert s._current_item is None
    assert s._last_spoken_session is None
    assert not s._paused.is_set()
    assert not s._wake.is_set()


def test_host_ledger_shims_delegate_to_state():
    daemon, *_ = make_daemon()
    # read-only shims return the SAME live object as state
    assert daemon._streams is daemon._state._streams
    assert daemon._pending_heard is daemon._state._pending_heard
    assert daemon._paused is daemon._state._paused
    assert daemon._wake is daemon._state._wake
    # read/write shims write through to state
    daemon._current_item = "sentinel"
    assert daemon._state._current_item == "sentinel"
    daemon._last_spoken_session = "sess-x"
    assert daemon._state._last_spoken_session == "sess-x"
    daemon._next_id = 41
    assert daemon._alloc_id() == 42
    assert daemon._state._next_id == 42
