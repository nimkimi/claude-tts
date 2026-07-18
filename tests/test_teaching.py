"""Learn mode (SP-D1, Task 10): while on, every Sonari hotkey speaks its own
'teach' sentence instead of acting, with a 120s idle auto-exit. The interception
lives in SpeechDaemon._dispatch_hotkey (the raw hotkey path), so a socket message
is never intercepted; the LEARN_MODE handler in features/teaching.py owns only
the toggle."""
import threading

import pytest

from sonari import keymap
from tests.daemon_helpers import make_daemon


@pytest.fixture
def timers(monkeypatch):
    """Replace threading.Timer with a fake that records (interval, fn) and starts
    NO real background thread — so the 120s idle auto-exit is inspectable and the
    suite never leaves a live daemon timer behind."""
    created = []

    class _FakeTimer:
        def __init__(self, interval, fn):
            self.interval = interval
            self.fn = fn
            self.daemon = False
            self.started = False
            self.cancelled = False
            created.append(self)

        def start(self):
            self.started = True

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    return created


def test_learn_mode_toggle_announces(timers):
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message({"type": "learn_mode"})
    assert daemon._learn_mode is True
    item = queue._items[-1]
    assert item.text.startswith("Learn mode.")
    assert item.mute_exempt and item.pause_exempt
    daemon.handle_message({"type": "learn_mode"})
    assert daemon._learn_mode is False
    assert queue._items[-1].text == "Learn mode off."
    assert timers[0].cancelled is True and len(timers) == 1   # manual exit cancels the idle timer


def test_hotkey_intercepted_speaks_teach_and_does_not_execute(timers, monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message({"type": "learn_mode"})          # learn mode ON
    dispatched = []
    real = daemon.handle_message

    def spy(m):
        dispatched.append(m.get("type"))
        return real(m)

    monkeypatch.setattr(daemon, "handle_message", spy)
    daemon._dispatch_hotkey({"type": "where_am_i"})
    assert "where_am_i" not in dispatched                  # the action never dispatched
    assert daemon._learn_mode is True                      # interception does not toggle
    item = queue._items[-1]
    assert item.text == keymap.ACTIONS["where_am_i"]["teach"]
    assert item.mute_exempt and item.pause_exempt


def test_learn_mode_toggle_key_is_exempt_from_interception(timers):
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message({"type": "learn_mode"})          # ON
    assert daemon._learn_mode is True
    daemon._dispatch_hotkey({"type": "learn_mode"})        # the toggle key itself
    assert daemon._learn_mode is False                     # exits (not taught)
    assert queue._items[-1].text == "Learn mode off."


def test_socket_path_never_intercepted(timers):
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message({"type": "learn_mode"})          # ON
    daemon.handle_message({"type": "stop"})                # socket path
    assert speaker.cancels == 1                            # stop executed, not taught
    assert daemon._learn_mode is True                      # socket path leaves mode alone


def test_auto_exit_timer_rearms_and_fires(timers):
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message({"type": "learn_mode"})          # arms the idle timer
    assert timers[-1].interval == 120
    assert timers[-1].started is True
    first = timers[-1]
    daemon._dispatch_hotkey({"type": "where_am_i"})        # a press re-arms it
    assert first.cancelled is True                         # the old timer is cancelled
    assert len(timers) == 2 and timers[-1].interval == 120
    timers[-1].fn()                                        # the idle timer fires
    assert daemon._learn_mode is False
    assert queue._items[-1].text == "Learn mode off."
