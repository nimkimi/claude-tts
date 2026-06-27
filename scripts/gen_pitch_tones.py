#!/usr/bin/env python3
"""Generate Sonari's pitch-direction chirp assets (zero deps; stdlib only).

Set A (Nima's ear choice, spec §17.2): rising pitch_up 440->880 Hz, falling
pitch_down 880->440 Hz, 200 ms linear chirp, 5 ms cosine in/out fades, 44100 Hz
16-bit mono. Output is committed to src/sonari/assets/; re-run to regenerate.
"""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 44100
DURATION = 0.200          # seconds
FADE = 0.005              # seconds, cosine in/out
AMPLITUDE = 0.6           # headroom below clipping
ASSETS = Path(__file__).resolve().parent.parent / "src" / "sonari" / "assets"


def _chirp(f0: float, f1: float) -> bytes:
    n = int(SAMPLE_RATE * DURATION)
    fade = max(1, int(SAMPLE_RATE * FADE))
    out = bytearray()
    for i in range(n):
        t = i / SAMPLE_RATE
        # Linear frequency sweep f0->f1: instantaneous phase is the integral of
        # 2*pi*f(t) where f(t)=f0+(f1-f0)*t/DURATION.
        phase = 2.0 * math.pi * (f0 * t + (f1 - f0) * t * t / (2.0 * DURATION))
        s = math.sin(phase)
        if i < fade:
            s *= 0.5 * (1.0 - math.cos(math.pi * i / fade))
        elif i >= n - fade:
            s *= 0.5 * (1.0 - math.cos(math.pi * (n - 1 - i) / fade))
        v = int(max(-1.0, min(1.0, s * AMPLITUDE)) * 32767)
        out += struct.pack("<h", v)
    return bytes(out)


def _write(path: Path, data: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(data)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    _write(ASSETS / "pitch_up.wav", _chirp(440.0, 880.0))
    _write(ASSETS / "pitch_down.wav", _chirp(880.0, 440.0))
    print("wrote", ASSETS / "pitch_up.wav", "and", ASSETS / "pitch_down.wav")


if __name__ == "__main__":
    main()
