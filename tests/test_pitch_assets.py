import wave
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "src" / "sonari" / "assets"


def _check(name):
    p = ASSETS / name
    assert p.exists(), "missing committed asset {0}".format(p)
    with wave.open(str(p), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2            # 16-bit
        assert w.getframerate() == 44100
        # 200 ms ± one frame of rounding
        assert abs(w.getnframes() - int(0.200 * 44100)) <= 1


def test_pitch_up_asset_is_44100_16bit_mono_200ms():
    _check("pitch_up.wav")


def test_pitch_down_asset_is_44100_16bit_mono_200ms():
    _check("pitch_down.wav")
