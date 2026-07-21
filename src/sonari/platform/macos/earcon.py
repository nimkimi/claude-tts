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
    # W6 failure taxonomy (spec §7): distinct kinds, provisional assets —
    # the OWNER's ear-pass may swap these paths (config-level, no code change).
    "error_misdirected": "/System/Library/Sounds/Basso.aiff",  # "wrong door"
    "error_system":      "/System/Library/Sounds/Blow.aiff",   # "broke inside"
    # W7 permission expiry (spec §8): provisional asset, owner's ear-pass swaps it.
    "permission_expired": "/System/Library/Sounds/Purr.aiff",  # "it slipped away"
    # D2 §6 silences: provisional assets — the owner's audition (ear-batch-2)
    # may swap any of these (config-level, no code change).
    "your_turn":  "/System/Library/Sounds/Pop.aiff",   # solo turn boundary (distinct from Tink)
    "submit_ack": "/System/Library/Sounds/Morse.aiff",  # prompt-submit ack (dark by default)
    "repoint":    "/System/Library/Sounds/Bottle.aiff", # workspace repoint on click
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
