"""Spearcon cache — time-compressed spoken session labels (spec §17.1).

A spearcon is `say -v <voice> -r <rate> -o <key>.aiff "<label>"` rendered once and
cached. Keying is content-addressed (sha256 of voice|rate|short_label) so arbitrary
folder names can never inject a path, and a voice/rate change cleanly re-keys.
Generation is ALWAYS off the hot path (non-blocking Popen); a cache miss returns
None so the caller falls back to plain speech this once while the file renders for
next time. Zero deps — system `say` only.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path


def spearcon_label(folder: str) -> str:
    """The short, recognizable label a folder is spoken as in a spearcon: the first
    component on a hyphen/underscore/whitespace split, capped at 12 chars. Real folder
    names are hyphen/underscore-separated, so 'invoice-generator' -> 'invoice', not a
    mid-word 'invoice-gene'. Empty/falsy -> ''. (Exact truncation is ear-tunable later.)"""
    if not folder:
        return ""
    parts = re.split(r"[-_\s]+", str(folder).strip())
    return parts[0][:12] if parts else ""


class SpearconCache:
    def __init__(self, cache_dir, voice: str = "Samantha", rate: int = 525,
                 popen=None) -> None:
        self._dir = Path(cache_dir)
        self._voice = voice
        self._rate = rate
        self._popen = popen or subprocess.Popen

    def _key(self, label: str) -> str:
        short = spearcon_label(label)
        raw = "{0}|{1}|{2}".format(self._voice, self._rate, short)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def path_for(self, label: str) -> Path:
        return self._dir / (self._key(label) + ".aiff")

    def get(self, label: str) -> "str | None":
        """Cached audio path if it EXISTS, else kick off background generation
        (non-blocking) and return None. Never blocks; never on the hot path."""
        p = self.path_for(label)
        if p.exists():
            return str(p)
        self.generate(label)
        return None

    def generate(self, label: str):
        """Spawn a non-blocking `say -o` rendering *label*'s short form to its cache
        file. Fire-and-forget; any spawn error is swallowed (the caller falls back to
        speech). Returns the proc, or None."""
        short = spearcon_label(label)
        if not short:
            return None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            p = self.path_for(label)
            cmd = ["say", "-v", self._voice, "-r", str(self._rate), "-o", str(p), short]
            return self._popen(cmd)
        except (OSError, ValueError):
            return None

    def pregenerate(self, labels) -> None:
        """Background pre-gen (SessionStart) for known labels; skips cached ones."""
        for label in labels:
            if spearcon_label(label) and not self.path_for(label).exists():
                self.generate(label)

    def cleanup(self, max_files: int = 256) -> None:
        """Prune to the *max_files* most-recently-modified .aiff at daemon start
        (stale reclamation; label-keyed orphan detection isn't possible at start —
        no sessions are registered yet). Bounds disk; never raises."""
        try:
            files = sorted(self._dir.glob("*.aiff"),
                           key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
                           reverse=True)
        except OSError:
            return
        for f in files[max_files:]:
            try:
                f.unlink()
            except OSError:
                pass
