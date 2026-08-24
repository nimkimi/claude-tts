"""The silent keep-alive WAV: generated at runtime under SONARI_DIR, idempotent,
regenerated if truncated. Spec: docs/superpowers/specs/2026-08-24-bt-keepalive-design.md."""
import os
import wave

from sonari import paths
from sonari.daemon import keepalive


def test_generates_valid_silence_wav_with_spec_parameters():
    path = keepalive.ensure_silence_wav()
    assert path == str(paths.KEEPALIVE_WAV_PATH)
    assert os.path.isfile(path)
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 8000
        assert w.getnframes() == int(8000 * keepalive.SILENCE_S)


def test_second_call_does_not_rewrite():
    path = keepalive.ensure_silence_wav()
    before = os.stat(path).st_mtime_ns
    assert keepalive.ensure_silence_wav() == path
    assert os.stat(path).st_mtime_ns == before


def test_truncated_file_is_regenerated():
    path = keepalive.ensure_silence_wav()
    with open(path, "wb") as fh:
        fh.write(b"RIFFbroken")
    keepalive.ensure_silence_wav()
    with wave.open(path, "rb") as w:
        assert w.getnframes() == int(8000 * keepalive.SILENCE_S)


def test_no_part_file_left_behind():
    keepalive.ensure_silence_wav()
    siblings = os.listdir(str(paths.SONARI_DIR))
    assert not any(name.endswith(".part") for name in siblings)
