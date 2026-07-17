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
