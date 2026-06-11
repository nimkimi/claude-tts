"""WinTtsBackend (OneCore via PyWinRT) — mock-tested on macOS via _winfakes.

WINDOWS-only code. "Green" here means the MOCKED contract holds (the fake
winrt tree injected by tests/_winfakes.py); it is NOT a claim that OneCore TTS
works on real Windows.
"""
from __future__ import annotations

import subprocess

import pytest

from sonari.platform.windows.tts import WinTtsBackend, wpm_to_speaking_rate


def test_list_and_best_voice():
    b = WinTtsBackend()
    assert isinstance(b.list_voices(), list) and b.list_voices()
    assert "speech_onecore" in (b.best_voice().id or "").lower()


def test_run_completes_returns_zero():
    h = WinTtsBackend().run("hello", None, 200)
    assert h.wait(timeout=2.0) == 0


def test_terminate_sets_returncode_one():
    h = WinTtsBackend().run("hello", None, 200)
    h.terminate()
    assert h.returncode == 1


def test_wait_timeout_raises(monkeypatch):
    # a player that never fires media_ended → wait must raise TimeoutExpired
    import winrt.windows.media.playback as pb
    monkeypatch.setattr(pb.MediaPlayer, "play", lambda self: None)
    h = WinTtsBackend().run("hello", None, 200)
    with pytest.raises(subprocess.TimeoutExpired):
        h.wait(timeout=0.05)


def test_wpm_maps_to_multiplier():
    assert abs(wpm_to_speaking_rate(200) - 1.0) < 1e-6
    assert wpm_to_speaking_rate(400) > 1.0 and wpm_to_speaking_rate(100) < 1.0


def test_run_falls_back_when_voice_name_unknown():
    # a stale/foreign voice name (e.g. macOS "Samantha") must not be assigned
    # as-is to synth.voice — run() resolves it or falls back to best_voice().
    h = WinTtsBackend().run("hi", "Samantha", 200)  # fake has no such voice
    assert h.wait(timeout=2.0) == 0   # did not crash on an unresolved name
