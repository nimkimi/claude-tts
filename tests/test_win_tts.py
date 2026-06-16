"""WinTtsBackend (OneCore via PyWinRT) — mock-tested on macOS via _winfakes.

WINDOWS-only code. "Green" here means the MOCKED contract holds (the fake
winrt tree injected by tests/_winfakes.py); it is NOT a claim that OneCore TTS
works on real Windows.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from sonari.platform.windows.tts import WinTtsBackend, wpm_to_speaking_rate


def test_list_voices():
    b = WinTtsBackend()
    assert isinstance(b.list_voices(), list) and b.list_voices()


def test_best_voice_returns_display_name_string():
    # ABC contract: best_voice() -> str. Holds for both the macOS fake voice
    # and a real OneCore voice on Windows (no hard-coded name).
    b = WinTtsBackend()
    v = b.best_voice()
    assert isinstance(v, str) and v
    assert v == b._best_voice_info().display_name


def test_best_voice_info_returns_object_with_onecore_id():
    info = WinTtsBackend()._best_voice_info()
    assert "speech_onecore" in (info.id or "").lower()


def test_run_completes_returns_zero():
    h = WinTtsBackend().run("hello", None, 200)
    assert h.wait(timeout=2.0) == 0


def test_terminate_sets_returncode_one():
    h = WinTtsBackend().run("hello", None, 200)
    h.terminate()
    assert h.returncode == 1


def test_wait_timeout_raises(monkeypatch):
    # A "long" clip: the completion timer hasn't fired, so a tiny wait must raise.
    import sonari.platform.windows.tts as tts
    monkeypatch.setattr(tts, "_wav_duration", lambda data: 100.0)
    h = WinTtsBackend().run("hello", None, 200)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            h.wait(timeout=0.05)
    finally:
        h.terminate()   # cancel the 100s timer so the test doesn't linger


def test_wpm_maps_to_multiplier():
    assert abs(wpm_to_speaking_rate(200) - 1.0) < 1e-6
    assert wpm_to_speaking_rate(400) > 1.0 and wpm_to_speaking_rate(100) < 1.0


def test_run_falls_back_when_voice_name_unknown():
    # a stale/foreign voice name (e.g. macOS "Samantha") must not be assigned
    # as-is to synth.voice — run() resolves it or falls back to best_voice().
    h = WinTtsBackend().run("hi", "Samantha", 200)  # fake has no such voice
    assert h.wait(timeout=2.0) == 0   # did not crash on an unresolved name


def test_run_unlinks_temp_wav_when_playsound_raises(monkeypatch, tmp_path):
    # Regression #7: if PlaySound raises before the _TtsHandle owns the file, run()
    # must unlink the temp WAV (else it leaks one file per failed utterance) and
    # propagate the error.
    import sonari.platform.windows.tts as tts
    import winsound
    leak = tmp_path / "sonari-tts-leak.wav"
    fd = os.open(str(leak), os.O_RDWR | os.O_CREAT)
    monkeypatch.setattr(tts.tempfile, "mkstemp", lambda *a, **k: (fd, str(leak)))

    def boom(*a, **k):
        raise RuntimeError("PlaySound failed")

    monkeypatch.setattr(winsound, "PlaySound", boom)
    with pytest.raises(RuntimeError):
        WinTtsBackend().run("hello", None, 200)
    assert not leak.exists()    # cleaned up, not leaked


def test_run_raises_actionable_error_when_no_voices(monkeypatch):
    # On a box with no OneCore voices, run() must surface the actionable
    # "install a voice" RuntimeError — NOT the raw FileNotFoundError that real
    # SpeechSynthesizer activation throws. Regression: nimkimi/sonari#2.
    import winrt.windows.media.speechsynthesis as ss
    monkeypatch.setattr(ss.SpeechSynthesizer, "all_voices", [])
    with pytest.raises(RuntimeError, match="No TTS voices installed"):
        WinTtsBackend().run("hello", None, 200)
