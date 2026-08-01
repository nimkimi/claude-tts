"""macOS earcon backend — wraps `afplay` + the System Sounds defaults."""
from __future__ import annotations

import os
import subprocess

from sonari.platform.contracts import EarconBackend

_DEFAULTS = {
    "permission": "/System/Library/Sounds/Funk.aiff",
    "choice":     "/System/Library/Sounds/Ping.aiff",
    "plan":       "/System/Library/Sounds/Submarine.aiff",
    "error":      "/System/Library/Sounds/Sosumi.aiff",
    "turn_done":  "/System/Library/Sounds/Tink.aiff",
    # W6/W7 failure taxonomy: distinct KINDS, one shared tone — the owner's
    # ear ruling (ear-batch-2 slot 1, 2026-08-01): every failure sounds Sosumi,
    # like plain `error`; the paired WORD carries the class. Kinds stay
    # distinct for words/config; a future re-split is config-level.
    "error_misdirected": "/System/Library/Sounds/Sosumi.aiff",
    "error_system":      "/System/Library/Sounds/Sosumi.aiff",
    "permission_expired": "/System/Library/Sounds/Sosumi.aiff",
    # D2 §6 silences: provisional assets — the owner's audition (ear-batch-2)
    # may swap any of these (config-level, no code change).
    "your_turn":  "/System/Library/Sounds/Pop.aiff",   # solo turn boundary (distinct from Tink)
    "submit_ack": "/System/Library/Sounds/Morse.aiff",  # prompt-submit ack (dark by default)
    "repoint":    "/System/Library/Sounds/Bottle.aiff", # workspace repoint on click
    "crossing":   "/System/Library/Sounds/Frog.aiff",   # keep-going miss marker (prelude)
    # §7 witness alarms — played via raw spawn (hotkeyd / the daemon), never
    # the transient arbiter. PROVISIONAL assets (ear-batch-2).
    "alarm_daemon_down":  "/System/Library/Sounds/Hero.aiff",
    # Basso not Glass (ear-batch-2 slot 11): Glass doubles as the owner's
    # out-of-band chat attention chime, so the alarm gets its own timbre.
    "alarm_hotkeys_down": "/System/Library/Sounds/Basso.aiff",
}


class MacEarconBackend:
    def play(self, path: str):
        if not os.path.exists(path):
            return None
        try:
            return subprocess.Popen(["afplay", path])
        except (FileNotFoundError, OSError):
            return None

    def default_earcons(self) -> dict:
        return dict(_DEFAULTS)
