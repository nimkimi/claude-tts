from __future__ import annotations
import struct, wave, pathlib
from sonari.platform.windows.earcons.generate import generate_earcon, _EARCON_SPECS


def _hdr(p):
    raw = open(p, "rb").read(44)
    return (raw[0:4], raw[8:12], struct.unpack("<H", raw[20:22])[0],
            struct.unpack("<H", raw[22:24])[0], struct.unpack("<I", raw[24:28])[0],
            struct.unpack("<H", raw[34:36])[0])


def test_generate_writes_valid_pcm_wav(tmp_path):
    p = tmp_path / "x.wav"; generate_earcon(p, 440.0, 0.12)
    riff, wav, fmt, ch, sr, bits = _hdr(p)
    assert riff == b"RIFF" and wav == b"WAVE" and fmt == 1 and ch == 1 and sr == 44100 and bits == 16


def test_all_specs_valid(tmp_path):
    for name, (f, d, wt, f2) in _EARCON_SPECS.items():
        p = tmp_path / (name + ".wav"); generate_earcon(p, f, d, wave_type=wt, freq2=f2)
        assert p.stat().st_size > 0
        with wave.open(str(p)) as w:
            assert abs(w.getnframes() / w.getframerate() - d) < 1e-3
