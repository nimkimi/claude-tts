"""Fire-once-per-failure-class cue suppression, re-armed by a later success.

The same discipline hotkeyd's witness already uses (sonari-hotkeyd.swift:167-173),
ported to Python rather than invented: sound once, then stay quiet for that class
until a success re-arms it. Without it a repeating fault becomes a repeating nag,
the user mutes it, and the signal is gone — the failure mode that costs the whole
cue.

Thread-safe: failure signalling runs on the speak thread AND on handler threads,
so the set needs its own lock. It is deliberately not the daemon lock — this is
called from inside an except block on the speak thread, which does not hold it.
"""
from __future__ import annotations

import threading


class FaultCue:
    def __init__(self) -> None:
        self._fired = set()
        self._lock = threading.Lock()

    def should_fire(self, cls: str) -> bool:
        """True the first time *cls* fails; False until a success re-arms it."""
        with self._lock:
            if cls in self._fired:
                return False
            self._fired.add(cls)
            return True

    def note_success(self, cls: str) -> None:
        """A success for *cls* — the next failure may speak again."""
        with self._lock:
            self._fired.discard(cls)
