"""Static per-check metadata for the doctor registry.

Spoken names and warn-class are properties of a CHECK, not of a run, so they
live here rather than widening doctor()'s (check, ok, detail) row — which 13
existing tests unpack positionally.
"""
from __future__ import annotations

# Printed name -> short name that survives being read aloud in a list.
_SPOKEN = {
    "SONARI_DIR writable": "storage",
    "daemon socket": "daemon socket",
    "hooks installed": "hooks",
    "keymap resolves": "keymap",
    "neural voices": "neural voices",
    "python3": "python",
    "plugin path resolved": "plugin path",
    "speech path": "speech path",
    "restore health": "restore health",
    "hotkeyd": "hotkeyd",
    "fault log": "fault log",
    "reachability": "reachability",
}

# Checks whose failure is advisory: printed, but never spoken and never
# enough to call the whole system unhealthy.
_WARN = frozenset({"neural voices", "fault log"})


def spoken_name(check: str) -> str:
    """Short sayable name for *check*; falls back to the printed name."""
    return _SPOKEN.get(check, check)


def is_warn(check: str) -> bool:
    """True if a failure of *check* is advisory rather than unhealthy."""
    return check in _WARN
