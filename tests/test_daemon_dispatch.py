from __future__ import annotations

"""Task 3.5 pins: dispatch-under-lock, unknown-type, and reply-row."""

from tests.daemon_helpers import make_daemon


def test_dispatch_under_lock_handle_message_guarded():
    """Dispatch runs while the transaction lock is held (socket path)."""
    daemon, _queue, _speaker, _sessions, _config = make_daemon()
    from sonari.daemon import registry
    recorded = []
    original = registry.HANDLERS["ping"]

    def recording_ping(ctx, msg):
        recorded.append(daemon._lock.locked())
        return original(ctx, msg)

    registry.HANDLERS["ping"] = recording_ping
    try:
        daemon._handle_message_guarded({"type": "ping"})
    finally:
        registry.HANDLERS["ping"] = original
    assert recorded == [True], "expected lock held during dispatch, got: {0}".format(recorded)


def test_dispatch_under_lock_dispatch_hotkey():
    """Dispatch runs while the transaction lock is held (hotkey path)."""
    daemon, _queue, _speaker, _sessions, _config = make_daemon()
    from sonari.daemon import registry
    recorded = []
    original = registry.HANDLERS["ping"]

    def recording_ping(ctx, msg):
        recorded.append(daemon._lock.locked())
        return original(ctx, msg)

    registry.HANDLERS["ping"] = recording_ping
    try:
        daemon._dispatch_hotkey({"type": "ping"})
    finally:
        registry.HANDLERS["ping"] = original
    assert recorded == [True], "expected lock held during hotkey dispatch, got: {0}".format(recorded)


def test_unknown_type_returns_none_no_raise():
    """handle_message with an unrecognized type returns None without raising."""
    daemon, _queue, _speaker, _sessions, _config = make_daemon()
    result = daemon.handle_message({"type": "nonexistent"})
    assert result is None


def test_ping_returns_ok_dict():
    """handle_message for ping returns the reply dict (exception #2: reply row)."""
    daemon, _queue, _speaker, _sessions, _config = make_daemon()
    result = daemon.handle_message({"type": "ping"})
    assert result == {"ok": True}


def test_status_returns_snapshot_dict():
    """handle_message for status returns the config snapshot dict."""
    daemon, _queue, _speaker, _sessions, _config = make_daemon()
    result = daemon.handle_message({"type": "status"})
    assert isinstance(result, dict)
    assert "verbosity" in result
    assert "rate" in result
    assert "voice" in result
    assert "foreground" in result
    assert "queue_len" in result
    assert "minqueue" in result
