"""SP6 durable-state persistence: the single atomic JSON snapshot at
SONARI_DIR/state.json.

Two objects: StateStore (this file's load/save) and PersistenceWriter (the
off-lock debounced writer thread, added in Task 6). Both are I/O primitives with
no knowledge of the daemon's state shape — the host builds the dict.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time

# Serialized format version. A mismatch on load() => fail open (§8): the daemon
# boots empty rather than misreading a future/foreign schema. Reserves the
# migration seam without shipping migration.
STATE_VERSION = 1


class StateStore:
    """The durable-state file. load() is fail-open; save() is concurrent-safe.

    NOT a consumer of atomicio.atomic_write_json: that writer uses a FIXED
    `path + ".tmp"`, so the shutdown flush() and the writer thread would race on
    one temp path and publish a torn file, which load() then rejects and
    fail-opens to EMPTY — silently dropping the whole pile on the exact
    `sonari install` path SP6 protects (§7). save() below serializes under an
    internal lock AND writes a UNIQUE temp via tempfile.mkstemp, so overlapping
    saves can never collide.
    """

    def __init__(self, path) -> None:
        self._path = str(path)
        self._lock = threading.Lock()

    def load(self) -> "dict | None":
        """The persisted dict, or None on missing / unreadable / invalid-JSON /
        non-object / version-mismatch (fail-open: the daemon boots empty, §8)."""
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, ValueError, OSError):
            return None
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            return None
        return data

    def save(self, data: dict) -> None:
        """Serialize `data` to the state file atomically. Under an internal lock
        (so two callers serialize) and via a UNIQUE temp in the same dir + fsync
        + os.replace (so a concurrent save can never tear the published file)."""
        with self._lock:
            directory = os.path.dirname(self._path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, suffix=".state.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self._path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise


class PersistenceWriter:
    """Off-lock, debounced state writer. A dirty Event coalesces bursts of
    mark_dirty() into a small bounded number of saves. The snapshot is built
    UNDER the daemon lock (passed in) and the disk write happens OUTSIDE it, so no
    I/O ever runs under self._lock (the load-bearing perf rule, §7/§12).

    snapshot_fn is invoked with `lock` held and MUST NOT acquire `lock` itself
    (threading.Lock is non-reentrant) — it is the host's _snapshot_state, a
    lock-free builder that reads state under the lock the writer holds.
    """

    def __init__(self, store, snapshot_fn, lock, *, debounce: float = 1.0,
                 clock=time.monotonic, sleep=time.sleep) -> None:
        self._store = store
        self._snapshot_fn = snapshot_fn
        self._lock = lock
        self._debounce = debounce
        self._clock = clock          # reserved monotonic seam (§9); coalesce uses _sleep
        self._sleep = sleep
        self._dirty = threading.Event()
        self._running = threading.Event()
        self._thread = None

    def mark_dirty(self) -> None:
        """Arm a save. NON-BLOCKING: sets an Event and returns. Acquires no lock
        and does no I/O, so it is safe on the hot path and under self._lock."""
        self._dirty.set()

    def start(self) -> None:
        self._running.set()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running.is_set():
            self._run_one_cycle()

    def _run_one_cycle(self) -> bool:
        """One debounce+save cycle: block for dirty, coalesce a burst, clear,
        snapshot, save. Returns False when woken for shutdown (nothing saved),
        else True. A late mark_dirty landing after clear() re-arms the Event for a
        redundant next cycle, so no steady-state mutation is dropped (§7)."""
        self._dirty.wait()
        if not self._running.is_set():
            return False
        self._sleep(self._debounce)          # coalesce a burst into one save
        self._dirty.clear()
        self._save_once()
        return True

    def _save_once(self) -> None:
        """Build the snapshot UNDER the lock, write OUTSIDE it. Never propagates a
        fault — a failed save must not kill the writer or the shutdown flush."""
        try:
            with self._lock:
                data = self._snapshot_fn()
            self._store.save(data)
        except Exception:  # noqa: BLE001 - a save fault must never propagate
            pass

    def flush(self) -> None:
        """Synchronous snapshot + save for shutdown (§7). Same off-lock
        discipline as the loop; used after the writer thread is joined."""
        self._save_once()

    def stop(self) -> None:
        """Stop the loop and JOIN the thread (§7 shutdown contract). Idempotent:
        a never-started writer just clears the flags."""
        self._running.clear()
        self._dirty.set()                    # unblock a waiting _run_one_cycle
        if self._thread is not None:
            self._thread.join()
            self._thread = None
