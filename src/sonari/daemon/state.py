from __future__ import annotations

from contextlib import contextmanager


class SessionState:
    def __init__(self, lock):
        self._lock = lock

    @contextmanager
    def transaction(self):
        with self._lock:
            yield
