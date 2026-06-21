from __future__ import annotations

import threading


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
