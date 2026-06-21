from __future__ import annotations

import threading
from contextlib import contextmanager


class SessionState:
    """The lock owner + the global speech ledger.

    Holds the cross-thread fields the speak loop, the connection threads, and the
    hotkey thread all touch under the one lock: the per-session stream registry,
    the pending-heard markers, the in-flight claim, the folder-attribution cursor,
    the id counter, and the pause/wake Events. The host reads/writes these directly
    as ``self._state._X`` on the hot path; property shims on the host bridge the old
    ``self._X`` names for cold-path callers (tests, guards, feature modules).
    """

    def __init__(self, lock):
        self._lock = lock
        self._streams: "dict" = {}
        self._next_id = 0
        self._wake = threading.Event()
        self._pending_heard: "dict" = {}
        self._paused = threading.Event()
        self._current_item = None
        self._last_spoken_session = None

    @contextmanager
    def transaction(self):
        with self._lock:
            yield
