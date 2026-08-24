"""Bluetooth keep-alive: hold the audio output device open with silence.

macOS suspends a Bluetooth A2DP stream ~1.1s after the last audio client goes
quiet; re-establishment swallows the head of the next utterance (measured —
see docs/superpowers/specs/2026-08-24-bt-keepalive-design.md). While any live
session exists the daemon keeps a silent afplay child streaming so the device
never goes quiet. The asset is generated here, at runtime, because committing
megabytes of literal zeros buys nothing (the spearcon cache is the precedent
for runtime audio artifacts under SONARI_DIR).
"""
from __future__ import annotations

import os
import wave

SILENCE_S = 300.0
_RATE = 8000


def ensure_silence_wav() -> str:
    """Return the silent WAV's path, generating it if missing or truncated.
    Path read LIVE (import inside the function, not at module top) so the
    conftest per-test redirect takes effect — matches host.py's StateStore idiom."""
    from sonari.paths import KEEPALIVE_WAV_PATH, ensure_sonari_dir

    path = str(KEEPALIVE_WAV_PATH)
    frames = int(_RATE * SILENCE_S)
    if _valid(path, frames):
        return path
    ensure_sonari_dir()
    part = path + ".part"
    try:
        with wave.open(part, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(_RATE)
            w.writeframes(b"\x00\x00" * frames)
        os.replace(part, path)
    finally:
        try:
            os.unlink(part)
        except OSError:
            pass
    return path


def _valid(path: str, frames: int) -> bool:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() == frames
    except (OSError, wave.Error, EOFError):
        return False
