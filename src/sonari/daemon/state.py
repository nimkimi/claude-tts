from __future__ import annotations

import threading
from contextlib import contextmanager


class SessionState:
    """The lock owner + the global speech ledger.

    Holds the cross-thread fields the speak loop, the connection threads, and the
    hotkey thread all touch under the one lock: the per-session stream registry,
    the pending-heard markers, the in-flight claim, the folder-attribution cursor,
    the id counter, and the wake Event. The host reads/writes these directly
    as ``self._state._X`` on the hot path; property shims on the host bridge the old
    ``self._X`` names for cold-path callers (tests, guards, feature modules).
    """

    def __init__(self, lock):
        self._lock = lock
        self._streams: "dict" = {}
        self._next_id = 0
        self._wake = threading.Event()
        self._pending_heard: "dict" = {}
        self._current_item = None
        self._last_spoken_session = None
        # W12 repeat-last: the last COMPLETED non-control_cue utterance as
        # (spoken_text, audio_path) — text AS SPOKEN (_attributed_text output,
        # folder prefix included: verbatim = what the ear got). Written by the
        # speak loop under the tail lock; read by the REPEAT_LAST handler under
        # the same lock (the handler transaction). None until first capture.
        self._last_utterance = None
        # The voice-global mode (SPEC §6): exactly one of "flowing" / "quiet-hold"
        # / "stopped-all". Born flowing so the keep-going gate is a no-op until a
        # deliberate ⌃⌘S / ⌃⌘M transitions it (T1). Read on the hot path directly
        # as self._state._voice_state; cold-path callers use host.voice_state.
        # TRANSIENT: not serialized -- a live hold is lost on daemon restart (-> SP6).
        self._voice_state = "flowing"

    @contextmanager
    def transaction(self):
        with self._lock:
            yield
