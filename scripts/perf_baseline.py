"""Micro-benchmark the daemon's enqueue->pop critical section and bank a JSON
baseline. Step 7 (the speak-loop/state relocation) re-runs this and compares,
so the per-utterance hot path is gated on a measured number, not an ear test.

Run:  .venv/bin/python scripts/perf_baseline.py
Writes: scripts/perf_baseline.json  (committed as the before-number)
"""
from __future__ import annotations

import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from sonari.sessions import SessionManager
from sonari.daemon import SpeechDaemon
from sonari.config import DEFAULTS

N = 200_000
WARMUP = 10_000


def _make_daemon():
    class _NullSpeaker:
        rate = DEFAULTS["rate"]

        def speak(self, text, cancel_epoch=None):
            return True

        def cancel_epoch(self):
            return 0

        def cancel(self):
            pass

        def earcon(self, kind):
            pass

        def set_rate(self, r):
            pass

        def set_voice(self, v):
            pass

    sessions = SessionManager()
    sessions.set_foreground("fg")
    config = {k: (v.copy() if isinstance(v, dict) else v)
              for k, v in DEFAULTS.items()}
    daemon = SpeechDaemon(_NullSpeaker(), sessions, config)
    return daemon


def _bench(daemon, n):
    st = daemon._stream("fg")
    q = st.queue
    best = float("inf")
    total = 0.0
    for _ in range(n):
        t0 = time.perf_counter()
        daemon._enqueue("fg", "prose", "x", False)
        item = q.pop_next()
        dt = time.perf_counter() - t0
        total += dt
        if dt < best:
            best = dt
        assert item is not None
    return total, best


def main() -> None:
    daemon = _make_daemon()
    _bench(daemon, WARMUP)  # warm up; discard
    total, best = _bench(daemon, N)
    result = {
        "iterations": N,
        "section": "enqueue+pop_next",
        "total_seconds": round(total, 6),
        "mean_ns": round(total / N * 1e9, 1),
        "best_ns": round(best * 1e9, 1),
        "python": sys.version.split()[0],
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "perf_baseline.json")
    with open(out, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    print("Wrote {0}".format(out))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
