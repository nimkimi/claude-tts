"""macOS earcon backend — wraps `afplay` + the System Sounds defaults."""
from __future__ import annotations

import os
import subprocess

from sonari.platform.contracts import EarconBackend


class MacEarconBackend:
    def play(self, path: str):
        if not os.path.exists(path):
            return None
        try:
            return subprocess.Popen(["afplay", path])
        except (FileNotFoundError, OSError):
            return None
