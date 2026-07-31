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
import itertools
import os
import re
import shlex
import subprocess
from pathlib import Path

# Per-process render sequence: with the pid it makes every render's tmp file
# unique, so concurrent renders can never share (and corrupt) one .part inode.
_RENDER_SEQ = itertools.count()


def _default_voice_lister() -> str:
    """Run `say -v '?'` and return stdout. Raises on any error (non-zero / missing)."""
    result = subprocess.run(
        ["say", "-v", "?"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError("say -v '?' exited non-zero")
    return result.stdout


def _voice_is_available(voice: str, lister) -> bool:
    """Return True iff *voice* appears as the first token on any line of `say -v '?`
    output. On any error (say missing, non-zero exit, parse failure) returns False —
    the safe fallback (no doomed per-cue Popen)."""
    try:
        output = lister()
        for line in output.splitlines():
            parts = line.split()
            if parts and parts[0].lower() == voice.lower():
                return True
        return False
    except Exception:
        return False


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
                 popen=None, voice_lister=None) -> None:
        self._dir = Path(cache_dir)
        self._voice = voice
        self._rate = rate
        self._popen = popen or subprocess.Popen
        # One-time check at init (never on the hot path). If the configured voice is
        # absent or the lookup fails, generate() falls back to system default (no -v).
        self._voice_available = _voice_is_available(voice, voice_lister or _default_voice_lister)

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

    def generate(self, label):
        """Spawn a non-blocking render of *label*'s short form. `say -o` writes a
        SIBLING temp file (<final>.<render-id>.part) and an atomic same-directory
        rename publishes it — one spawned shell so fire-and-forget is preserved,
        and a killed/failed render can never leave a truncated file that get()
        would treat as a permanent cache hit. The render id (pid + a process
        counter) gives each render its own tmp inode, so two concurrent renders
        of one label race benignly: each mv publishes a COMPLETE file, last
        writer wins. Any spawn error is swallowed (the caller falls back to
        speech). Returns the proc, or None."""
        short = spearcon_label(label)
        if not short:
            return None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            p = self.path_for(label)
            tmp = p.parent / "{}.{}-{}.part".format(p.name, os.getpid(),
                                                    next(_RENDER_SEQ))
            cmd = ["say"]
            if self._voice_available:
                cmd += ["-v", self._voice]
            cmd += ["-r", str(self._rate), "-o", str(tmp), short]
            script = "{0} && mv {1} {2}".format(
                " ".join(shlex.quote(c) for c in cmd),
                shlex.quote(str(tmp)), shlex.quote(str(p)))
            return self._popen(["sh", "-c", script])
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
        # Sweep stale in-flight renders: cleanup runs at daemon start, so any
        # surviving *.part is a dead render (its shell died before the publish).
        try:
            for part in self._dir.glob("*.part"):
                try:
                    part.unlink()
                except OSError:
                    pass
        except OSError:
            pass
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
