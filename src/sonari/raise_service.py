"""Core focus-follow orchestration: config gate, jump-generation supersession,
and async dispatch to a platform RaiseBackend. The slow OS raise (~0.4s) runs on
a daemon thread so the message handler and speak loop are never blocked; a stale
raise (a newer jump superseded it) no-ops, so OS focus never diverges from voice.
"""
from __future__ import annotations

import threading


class RaiseService:
    def __init__(self, backend, config) -> None:
        self._backend = backend
        self._config = config
        self._generation = 0
        self._lock = threading.Lock()
        self._threads: "list[threading.Thread]" = []

    def will_attempt(self, identity) -> bool:
        if not bool(self._config.get("focus_follow", True)):
            return False
        if identity is None:
            return False
        return bool(self._backend.supports(identity))

    def bump_generation(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def current_generation(self) -> int:
        with self._lock:
            return self._generation

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation

    def raise_async(self, identity, generation: int, on_failure=None) -> None:
        def _run():
            if not self._is_current(generation):
                return
            try:
                ok = self._backend.raise_session(identity)
            except Exception:  # noqa: BLE001 - a backend bug must never crash the thread
                ok = False
            if not ok and on_failure is not None and self._is_current(generation):
                on_failure()
        t = threading.Thread(target=_run, name="sonari-raise", daemon=True)
        with self._lock:
            self._threads = [x for x in self._threads if x.is_alive()]
            self._threads.append(t)
        t.start()

    def join(self, timeout: "float | None" = None) -> None:
        """Test helper: wait for spawned raise threads to finish."""
        for t in list(self._threads):
            t.join(timeout)
