# Sonari — Cockpit Sound Language (sub-project D) — implementation plan

- **Date:** 2026-06-27
- **Branch:** `feat/cockpit-sound-language` (off merged main @ 403e2a1)
- **Baseline suite:** **796 passed, 1 skipped** (every task must return to 796/1skip + its own new tests)
- **Spec (authoritative):** `docs/superpowers/specs/2026-06-26-sonari-cockpit-grammar-design.md` §8 + **§17**
- **Ledger:** `.git/sdd/progress.md`
- **Execution:** superpowers:subagent-driven-development (per-task review + opus whole-branch review)

## Goal

Implement §8's sound language as the last cockpit sub-project: (1) **pitch-direction chirps**
(rising = next/yes, falling = prev/no) at 4 directional sites; (2) **spearcons** (time-compressed
spoken session names) replacing the 5 standalone folder name-cues, played queue-integrated via
`afplay` with the same barge-in as `say`; (3) **earcon-set shrink** (drop `ready`, keep everything
else). Zero new dependencies — system `say` + `afplay` only.

## Architecture

- **Chirps** are two committed WAV assets (`src/sonari/assets/pitch_{up,down}.wav`), played
  fire-and-forget via `afplay` **directly from the package asset path** — NOT through the
  configurable earcons dict (`bootstrap.py:70`'s whole-key guard silently no-ops new keys for
  existing users). A new `Speaker.pitch(direction)` resolves the asset and plays it with the same
  non-blocking reap as `earcon()`. Four directional handlers call it; the chirp fires first, the
  spearcon/content follows.
- **Spearcons** are cached `say -v <voice> -r <rate> -o <key>.aiff "<label>"` files under
  `~/.sonari/spearcons/`, keyed `sha256(voice|rate|short_label)[:16]`. A standalone `SpearconCache`
  owns keying, background generation (`subprocess.Popen`, non-blocking — never on the hot path),
  pre-generation on SessionStart, and a start-time prune. **Generation is decoupled from playback:**
  a cache *miss* returns `None` (the cue falls back to plain speech this once) and kicks off
  background generation for next time; a *hit* returns the audio path.
- **Queue-integrated playback:** a name cue stays a queued `at_front` `SpeechItem` but now carries an
  optional `audio_path`. The speak loop, when the claimed item has an `audio_path`, calls the
  generalized `Speaker.speak(text, audio_path=..., cancel_epoch=...)` which `afplay`s that file as the
  tracked `_current` proc — so `cancel()` (barge-in) interrupts it identically to `say`, and at_front
  ordering / `names_session` attribution-suppression are preserved.
- **Earcon shrink:** `ready` removed from the macOS default map and from the `idle_prompt` hook
  branch. `turn_done` kept (owner decision).

## Tech Stack

Python 3.9+, stdlib only (`wave`, `struct`, `math`, `hashlib`, `subprocess`, `pathlib`,
`importlib`/`__file__`). pytest behind the existing `tests/daemon_helpers.py` fakes. No new runtime
deps; Kokoro optional extra untouched.

## Global Constraints (copied verbatim from the ledger's HARD CONTRACTS)

- TDD; full suite green before each task (baseline 796/1skip). Daemon behavior behind tests/daemon_helpers.py fakes.
- ZERO new deps — system `say`/`afplay` only (kokoro stays an optional extra, untouched).
- Runtime perf (Nima's hard constraint): spearcon GENERATION never on the hot path (background Popen, cached);
  playback = afplay a tiny cached file (fast). Chirps = fire-and-forget afplay.
- Speak-loop change (audio_path) is concurrency-sensitive: the afplay proc participates in cancel-epoch barge-in
  exactly like `say`; the existing 2 permanent concurrency guards + barge-in tests must stay green.
- Pitch assets bypass the configurable earcons dict (direct asset-path afplay).
- Branch+PR only — NO direct main push; NO claude.ai/code/session footer. NEVER `sonari install` against live ~/.sonari.
- All A/B/C behavior stays green (#65, per-session stop, nav grammar, ⌃⌘D, answer-via-hook).

## File Structure

```
scripts/gen_pitch_tones.py            NEW  stdlib chirp generator (committed; re-runnable)
src/sonari/assets/pitch_up.wav        NEW  committed asset (440→880 Hz)
src/sonari/assets/pitch_down.wav      NEW  committed asset (880→440 Hz)
src/sonari/spearcon.py                NEW  SpearconCache + spearcon_label()
pyproject.toml                        EDIT package-data: ship assets/*.wav
src/sonari/speaker.py                 EDIT Speaker.pitch(); speak(text=None, audio_path=None, ...)
src/sonari/queue.py                   EDIT SpeechItem.audio_path field
src/sonari/config.py                  EDIT DEFAULTS += spearcon_voice, spearcon_rate
src/sonari/daemon/host.py             EDIT __init__ spearcons; _enqueue audio_path; speak-loop threading; _spearcon_path
src/sonari/daemon/bootstrap.py        EDIT build SpearconCache + cleanup() + wire into SpeechDaemon
src/sonari/daemon/features/focus.py   EDIT on_cycle_session: chirp + spearcon
src/sonari/daemon/features/navigation.py EDIT on_nav: chirp + crossed spearcon
src/sonari/daemon/features/playback.py   EDIT on_jump_decision: crossed spearcon
src/sonari/daemon/features/control.py    EDIT on_where_am_i: spearcon folder part
src/sonari/daemon/features/decisions.py  EDIT on_answer_permission: chirp
src/sonari/daemon/features/lifecycle.py  EDIT SessionStart: background pregenerate
src/sonari/platform/macos/earcon.py   EDIT drop `ready`
src/sonari/hooks_entry.py             EDIT drop idle_prompt→ready branch
tests/daemon_helpers.py               EDIT FakeSpeaker.pitch/speak(audio_path); FakeSpearconCache; inject into make_daemon
tests/test_*.py                       EDIT/NEW per task
```

## Resolved ambiguities (read before building)

1. **Cache miss → speech fallback (load-bearing simplification).** `Speaker.speak` ignores `text`
   when `audio_path` is set; on a *hit* the cue item carries `audio_path` and the spearcon plays, on a
   *miss* `audio_path` is `None` and the existing spoken cue plays unchanged. **Every existing cue test
   runs the miss path** (the injected fake cache defaults to all-miss) so they stay green untouched;
   only new hit-path tests exercise spearcons.
2. **`spearcon_label()` lives INSIDE the cache** (used by every entry point: key/generate/get/pregenerate).
   If `get()` truncated but `pregenerate()` keyed off raw folders the sha keys would diverge and
   pre-generation would be silently wasted. One owner for the transform.
3. **Cues that carry extra speech split into two items** (spearcon then speech), because `afplay` of a
   folder spearcon cannot also speak the remaining words:
   - `on_where_am_i`: spearcon(folder) + `"{state}. {N} waiting."` (§6.5 requires the status survive).
   - `on_jump_waiting`: spearcon(folder) + (when not raising) `"Bring it forward to type."`. The
     "Jumping to" verb is dropped — the user pressed ⌃⌘J and the spearcon names the destination
     (cockpit minimalism). **Ear-tunable at the live gate.** Never double-announce.
4. **`SpearconCache.cleanup()` is an mtime-LRU prune, not label-keyed.** At daemon start NO sessions are
   registered, so label-keyed orphan detection is impossible; the honest reading of "cleanup
   stale/orphan files at start" is a bounded mtime prune. Flagged for owner confirmation.
5. **Conditional-pass in the speak loop** (`if item.audio_path:` two-arm call) keeps the 2 permanent
   concurrency guards and all inline speak fakes untouched — only `FakeSpeaker.speak` gains the
   `audio_path` kwarg.

---

## Task 1 — Pitch-direction chirps (generator + assets + `Speaker.pitch` + wire 4 directional sites)

Independent of Tasks 2–4. Establishes the directional sound channel.

**Files:** `scripts/gen_pitch_tones.py` (new), `src/sonari/assets/pitch_{up,down}.wav` (new),
`pyproject.toml`, `src/sonari/speaker.py`, `tests/daemon_helpers.py` (FakeSpeaker.pitch),
`src/sonari/daemon/features/focus.py`, `navigation.py`, `decisions.py`,
`tests/test_pitch_assets.py` (new), `tests/test_speaker_pitch.py` (new),
`tests/test_pitch_dispatch.py` (new).

**Interfaces:**
- Produces `Speaker.pitch(self, direction: str) -> None` (`direction` ∈ `{"up","down"}`; anything else
  is a no-op).
- Produces `scripts/gen_pitch_tones.py` `main() -> None` writing both assets.
- Consumes the existing `Speaker._earcon_player` (afplay) + `_reap_earcon_procs()`.
- FakeSpeaker gains `pitch(self, direction)` recording `self.pitches.append(direction)`.

### Step 1.1 RED — committed-asset format test

`tests/test_pitch_assets.py`:
```python
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
```
Run: `pytest tests/test_pitch_assets.py -q` → FAIL (no assets dir).

### Step 1.2 GREEN — generator + commit assets

Create `scripts/gen_pitch_tones.py`:
```python
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
```
Run `python scripts/gen_pitch_tones.py` to create + commit the two `.wav` files. `pytest tests/test_pitch_assets.py -q` → PASS.

Add package-data so the assets ship (append to `pyproject.toml` after `[tool.setuptools.packages.find]`):
```toml
[tool.setuptools.package-data]
sonari = ["assets/*.wav"]
```

### Step 1.3 RED — `Speaker.pitch`

`tests/test_speaker_pitch.py`:
```python
from pathlib import Path

from sonari.speaker import Speaker


class RecordingPlayer:
    def __init__(self):
        self.paths = []

    def __call__(self, path):
        self.paths.append(path)
        return None        # fire-and-forget; no proc to track


def test_pitch_up_plays_the_up_asset_directly_from_the_package():
    player = RecordingPlayer()
    sp = Speaker(earcon_player=player)
    sp.pitch("up")
    assert len(player.paths) == 1
    assert player.paths[0].endswith("/assets/pitch_up.wav")
    assert Path(player.paths[0]).exists()           # a real committed asset path


def test_pitch_down_plays_the_down_asset():
    player = RecordingPlayer()
    Speaker(earcon_player=player).pitch("down")
    assert player.paths[0].endswith("/assets/pitch_down.wav")


def test_pitch_unknown_direction_is_noop():
    player = RecordingPlayer()
    Speaker(earcon_player=player).pitch("sideways")
    assert player.paths == []


def test_pitch_without_player_is_noop():
    Speaker().pitch("up")        # must not raise
```
Run → FAIL (`Speaker` has no `pitch`).

### Step 1.4 GREEN — implement `Speaker.pitch`

In `src/sonari/speaker.py`, after `earcon()` (currently ends line 98), add:
```python
    def pitch(self, direction: str) -> None:
        """Play a pitch-direction chirp (up = next/yes, down = prev/no), fire-and-
        forget. The asset is resolved DIRECTLY from the package (not the configurable
        earcons dict) so the cue can never be silently disabled by an existing user's
        `earcons` config (bootstrap merges with a whole-key guard). Reuses the earcon
        player (afplay) and the same non-blocking reap as earcon()."""
        if self._earcon_player is None or direction not in ("up", "down"):
            return
        self._reap_earcon_procs()
        from pathlib import Path
        path = str(Path(__file__).resolve().parent
                   / "assets" / "pitch_{0}.wav".format(direction))
        proc = self._earcon_player(path)
        if proc is not None and hasattr(proc, "poll"):
            self._earcon_procs.append(proc)
```
Run `pytest tests/test_speaker_pitch.py tests/test_speaker.py -q` → PASS.

### Step 1.5 GREEN — FakeSpeaker.pitch

In `tests/daemon_helpers.py`, `FakeSpeaker.__init__` currently ends:
```python
        self.complete = True          # next speak() reports completed?
        self._epoch = 0
```
Add a list:
```python
        self.complete = True          # next speak() reports completed?
        self._epoch = 0
        self.pitches: list[str] = []  # pitch(direction) calls
```
After `cancel()` (currently lines 29–31), add:
```python
    def pitch(self, direction: str) -> None:
        self.pitches.append(direction)
```

### Step 1.6 RED — directional dispatch at the 4 sites

`tests/test_pitch_dispatch.py`:
```python
from tests.daemon_helpers import make_daemon


def _two(daemon, sessions):
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")


def test_cycle_next_chirps_up():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    _two(daemon, sessions); sessions.set_foreground("A")
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    assert speaker.pitches == ["up"]


def test_cycle_prev_chirps_down():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    _two(daemon, sessions); sessions.set_foreground("A")
    daemon.handle_message({"type": "cycle_session", "direction": "prev"})
    assert speaker.pitches == ["down"]


def test_cycle_under_two_sessions_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    assert speaker.pitches == []          # error case: no directional cue


def _seed(daemon, s="fg"):
    h = daemon.history
    h.record(s, "prose", "m0"); h.end_message(s)
    h.record(s, "prose", "m1")


def test_nav_next_chirps_up_prev_chirps_down():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    _seed(daemon)
    daemon.handle_message({"type": "nav", "to": "next", "session": "fg"})
    daemon.handle_message({"type": "nav", "to": "prev", "session": "fg"})
    assert speaker.pitches == ["up", "down"]


def test_nav_first_last_do_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    _seed(daemon)
    daemon.handle_message({"type": "nav", "to": "first", "session": "fg"})
    daemon.handle_message({"type": "nav", "to": "last", "session": "fg"})
    assert speaker.pitches == []


def test_nav_response_chirps_directionally():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    h = daemon.history
    h.record("fg", "prose", "t0"); h.end_message("fg"); h.start_turn("fg")
    h.record("fg", "prose", "t1")
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})
    daemon.handle_message({"type": "nav", "to": "next_response", "session": "fg"})
    assert speaker.pitches == ["down", "up"]


def test_answer_allow_chirps_up_deny_chirps_down():
    import threading
    daemon, q, speaker, sessions, _ = make_daemon()
    sessions.set_foreground("S1", cwd="/x/a")
    daemon._pending_decisions["S1"] = {"event": threading.Event(), "behavior": None}
    daemon.handle_message({"type": "answer_permission", "behavior": "allow"})
    daemon._pending_decisions["S1"] = {"event": threading.Event(), "behavior": None}
    daemon.handle_message({"type": "answer_permission", "behavior": "deny"})
    assert speaker.pitches == ["up", "down"]


def test_answer_with_no_pending_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon()
    sessions.set_foreground("A", cwd="/x/a")
    daemon.handle_message({"type": "answer_permission", "behavior": "allow"})
    assert speaker.pitches == []          # error case (no pending): no directional cue
```
Run → FAIL (no `pitch` calls in handlers).

### Step 1.7 GREEN — wire the 4 directional sites

**`focus.py` `on_cycle_session`** — current tail:
```python
    step = 1 if msg.get("direction", "next") == "next" else -1
    target = ids[(cur + step) % len(ids)]
    sessions.focus(target)
```
becomes:
```python
    step = 1 if msg.get("direction", "next") == "next" else -1
    target = ids[(cur + step) % len(ids)]
    ctx.host.speaker.pitch("up" if step == 1 else "down")   # directional chirp first
    sessions.focus(target)
```

**`navigation.py` `on_nav`** — current:
```python
    to = msg.get("to", "prev")
    if to in ("prev_response", "next_response"):
        _nav_response(ctx, target, to)             # both clear target queue, then enqueue transcript
    else:
        _nav(ctx, target, to)
```
becomes:
```python
    to = msg.get("to", "prev")
    _chirp = {"next": "up", "prev": "down",
              "next_response": "up", "prev_response": "down"}.get(to)
    if _chirp:
        ctx.host.speaker.pitch(_chirp)             # directional chirp first; first/last get none
    if to in ("prev_response", "next_response"):
        _nav_response(ctx, target, to)             # both clear target queue, then enqueue transcript
    else:
        _nav(ctx, target, to)
```

**`decisions.py` `on_answer_permission`** — current success branch:
```python
    pd["behavior"] = behavior
    pd["event"].set()
    host.speaker.cancel()                     # barge-in: confirm immediately
```
becomes:
```python
    pd["behavior"] = behavior
    pd["event"].set()
    host.speaker.pitch("up" if behavior == "allow" else "down")   # directional chirp first
    host.speaker.cancel()                     # barge-in: confirm immediately
```
Run full suite → 796/1skip + the new pitch tests green. **Commit:** `feat(sonari): pitch-direction chirps + 4 directional sites`.

---

## Task 2 — `SpearconCache` + `spearcon_label` + config defaults (standalone)

Independent of Tasks 1, 3, 4. Pure module + config; no daemon wiring yet.

**Files:** `src/sonari/spearcon.py` (new), `src/sonari/config.py`, `tests/test_spearcon.py` (new),
`tests/test_config.py`.

**Interfaces (Produces):**
- `spearcon_label(folder: str) -> str`
- `class SpearconCache:`
  - `__init__(self, cache_dir, voice="Samantha", rate=525, popen=None)`
  - `path_for(self, label: str) -> Path`
  - `get(self, label: str) -> "str | None"` (hit → path str; miss → kicks background gen, returns None)
  - `generate(self, label: str) -> "object | None"` (non-blocking Popen)
  - `pregenerate(self, labels) -> None`
  - `cleanup(self, max_files: int = 256) -> None`
- DEFAULTS gains `spearcon_voice="Samantha"`, `spearcon_rate=525`.

### Step 2.1 RED — config defaults

In `tests/test_config.py`, `test_defaults_has_documented_top_level_keys` set becomes:
```python
    assert set(DEFAULTS.keys()) == {
        "voice",
        "rate",
        "verbosity",
        "background_policy",
        "history_cap",
        "backlog_cap",
        "minqueue",
        "focus_follow",
        "spearcon_voice",
        "spearcon_rate",
    }
```
Add:
```python
def test_spearcon_defaults():
    assert DEFAULTS["spearcon_voice"] == "Samantha"
    assert DEFAULTS["spearcon_rate"] == 525
```
Run → FAIL.

### Step 2.2 GREEN — config defaults

`src/sonari/config.py` DEFAULTS currently ends:
```python
    "minqueue": 1,
    "focus_follow": True,
}
```
becomes:
```python
    "minqueue": 1,
    "focus_follow": True,
    "spearcon_voice": "Samantha",
    "spearcon_rate": 525,
}
```
Run `pytest tests/test_config.py -q` → PASS.

### Step 2.3 RED — spearcon module

`tests/test_spearcon.py`:
```python
from pathlib import Path

from sonari.spearcon import SpearconCache, spearcon_label


def test_label_first_word_capped_at_12():
    assert spearcon_label("backend") == "backend"
    assert spearcon_label("my project here") == "my"          # first whitespace word
    assert spearcon_label("averylongfoldername") == "averylongfol"   # 12 chars
    assert spearcon_label("") == ""


def _recording():
    calls = []
    return calls, (lambda cmd: calls.append(cmd))


def test_key_is_stable_and_voice_rate_sensitive(tmp_path):
    a = SpearconCache(tmp_path, voice="Samantha", rate=525)
    b = SpearconCache(tmp_path, voice="Alex", rate=525)
    c = SpearconCache(tmp_path, voice="Samantha", rate=300)
    assert a.path_for("backend") == a.path_for("backend")     # deterministic
    assert a.path_for("backend") != b.path_for("backend")     # voice in key
    assert a.path_for("backend") != c.path_for("backend")     # rate in key
    assert a.path_for("backend").suffix == ".aiff"


def test_get_miss_kicks_background_generation_and_returns_none(tmp_path):
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen)
    assert cache.get("backend") is None                       # not generated yet
    assert calls == [["say", "-v", "Samantha", "-r", "525",
                      "-o", str(cache.path_for("backend")), "backend"]]


def test_get_hit_returns_path_and_does_not_regenerate(tmp_path):
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen)
    p = cache.path_for("backend")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"FORM....AIFF")                            # simulate a cached file
    assert cache.get("backend") == str(p)
    assert calls == []                                        # no regeneration on a hit


def test_generate_uses_truncated_label_as_say_text(tmp_path):
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen)
    cache.generate("my project here")
    assert calls[0][-1] == "my"                               # spearcon_label applied


def test_pregenerate_skips_already_cached(tmp_path):
    calls, popen = _recording()
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=popen)
    hit = cache.path_for("backend")
    hit.parent.mkdir(parents=True, exist_ok=True)
    hit.write_bytes(b"x")
    cache.pregenerate(["backend", "frontend", ""])           # backend cached, "" skipped
    assert [c[-1] for c in calls] == ["frontend"]


def test_cleanup_keeps_newest_by_mtime(tmp_path):
    import os
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525)
    d = tmp_path
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, name in enumerate(["a", "b", "c"]):
        p = d / "{0}.aiff".format(name)
        p.write_bytes(b"x")
        os.utime(p, (i, i))                                  # a oldest, c newest
        paths.append(p)
    cache.cleanup(max_files=2)
    assert not paths[0].exists()                             # oldest pruned
    assert paths[1].exists() and paths[2].exists()


def test_generate_swallows_popen_error(tmp_path):
    def boom(cmd):
        raise OSError("say missing")
    cache = SpearconCache(tmp_path, voice="Samantha", rate=525, popen=boom)
    assert cache.generate("backend") is None                # never raises
```
Run → FAIL (no module).

### Step 2.4 GREEN — implement `src/sonari/spearcon.py`

```python
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
import subprocess
from pathlib import Path


def spearcon_label(folder: str) -> str:
    """The short label spoken as a spearcon for *folder*: the first whitespace word,
    capped at 12 chars. Sensible default, ear-tunable at the live gate (§17.1)."""
    if not folder:
        return ""
    return folder.split()[0][:12]


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
                           key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return
        for f in files[max_files:]:
            try:
                f.unlink()
            except OSError:
                pass
```
Run `pytest tests/test_spearcon.py tests/test_config.py -q` then full suite → 796/1skip + new green. **Commit:** `feat(sonari): SpearconCache + spearcon_label + config defaults`.

---

## Task 3 — `Speaker.speak(audio_path)` + `SpeechItem.audio_path` + speak-loop threading (concurrency-sensitive core)

Independent of Tasks 1, 2. **This is the barge-in-critical task.** The 2 permanent concurrency
guards (`tests/test_concurrency_guards.py`) and all `tests/test_speaker*.py` barge-in tests must
stay green. Conditional-pass in the loop keeps the guards' `_ReentrantSpeaker` and every inline
speak fake untouched.

**Files:** `src/sonari/speaker.py`, `src/sonari/queue.py`, `src/sonari/daemon/host.py`,
`tests/daemon_helpers.py` (FakeSpeaker.speak), `tests/test_speaker.py` (new audio_path cases),
`tests/test_daemon_loop.py` (new audio_path-loop case).

**Interfaces:**
- `SpeechItem` gains `audio_path: "str | None" = None`.
- `Speaker.__init__` gains `afplay_runner=None`.
- `Speaker.speak(self, text=None, audio_path=None, cancel_epoch=None) -> bool` — `audio_path` set →
  `afplay_runner(audio_path)`; else `say_runner(text, voice, rate)`. Runner returning `None` → `False`.
- `host._enqueue(..., audio_path=None)` threads it onto the item.
- speak loop passes `audio_path=item.audio_path` (both branches) via conditional-pass.
- FakeSpeaker.speak accepts `audio_path` + records `self.audio_paths.append(audio_path)`.

### Step 3.1 RED — `Speaker.speak(audio_path=...)` afplays + barges in

In `tests/test_speaker.py` add:
```python
def test_speak_audio_path_uses_afplay_runner_not_say():
    say = RecordingRunner()
    played = []
    afplay = lambda path: played.append(path) or FakePopen()
    sp = Speaker(say_runner=say, afplay_runner=afplay)
    assert sp.speak("ignored", audio_path="/cache/x.aiff") is True
    assert played == ["/cache/x.aiff"]
    assert say.calls == []                     # say not invoked for an audio item


def test_speak_audio_path_tracks_proc_and_cancel_terminates_it():
    captured = {}

    class CancelOnWait(FakePopen):
        def wait(self, timeout=None):
            captured["sp"].cancel()            # barge-in mid-afplay
            return super().wait(timeout=timeout)

    sp = Speaker(afplay_runner=lambda path: CancelOnWait())
    captured["sp"] = sp
    sp.speak(audio_path="/cache/x.aiff")
    # the tracked afplay proc was terminated by cancel() — barge-in parity with say


def test_speak_audio_path_honors_external_cancel_epoch_baseline():
    made = []

    def afplay(path):
        p = FakePopen(); made.append(p); return p

    sp = Speaker(afplay_runner=afplay)
    epoch0 = sp.cancel_epoch()
    sp.cancel()                                # lands in the claim->speak gap
    assert sp.speak(audio_path="/x.aiff", cancel_epoch=epoch0) is False
    assert made[0].terminate_calls == 1
    assert made[0].wait_calls == 0


def test_speak_audio_path_runner_returns_none_is_false():
    sp = Speaker(afplay_runner=lambda path: None)
    assert sp.speak(audio_path="/missing.aiff") is False        # never derefs None
```
Run → FAIL.

### Step 3.2 GREEN — generalize `Speaker`

`src/sonari/speaker.py` `__init__` signature currently:
```python
    def __init__(
        self,
        voice=None,
        rate=200,
        say_runner=None,
        earcon_player=None,
        earcons=None,
        _wait_timeout: float = _DEFAULT_WAIT_TIMEOUT,
    ) -> None:
        self._voice = voice
        self._rate = rate
        self._say_runner = say_runner
```
becomes (add `afplay_runner`):
```python
    def __init__(
        self,
        voice=None,
        rate=200,
        say_runner=None,
        afplay_runner=None,
        earcon_player=None,
        earcons=None,
        _wait_timeout: float = _DEFAULT_WAIT_TIMEOUT,
    ) -> None:
        self._voice = voice
        self._rate = rate
        self._say_runner = say_runner
        self._afplay_runner = afplay_runner
```
`speak()` currently:
```python
    def speak(self, text: str, cancel_epoch=None) -> bool:
        """Speak text, blocking. ..."""
        if self._say_runner is None:
            return False
        ...
        with self._current_lock:
            epoch = self._cancel_epoch if cancel_epoch is None else cancel_epoch
        proc = self._say_runner(text, self._voice, self._rate)
        with self._current_lock:
            interrupted = self._cancel_epoch != epoch
            if not interrupted:
                self._current = proc
        if interrupted:
            proc.terminate()
            return False
```
becomes (generalize over the runner; handle a None proc):
```python
    def speak(self, text=None, audio_path=None, cancel_epoch=None) -> bool:
        """Play an utterance, blocking. When *audio_path* is set, afplay that file
        (a spearcon); otherwise say *text*. Return True iff it COMPLETED (exit 0).
        A cancelled/terminated/failed-to-spawn utterance returns False so the caller
        leaves it marked unheard (sentence-granular replay).

        *cancel_epoch* is the baseline to compare against (see cancel_epoch()); a
        cancel arriving between the daemon's claim and this call is detected. The
        afplay proc is tracked as _current exactly like say, so cancel() interrupts
        it identically (barge-in parity)."""
        if audio_path is not None:
            runner = self._afplay_runner
        else:
            runner = self._say_runner
        if runner is None:
            return False
        # Establish the baseline epoch BEFORE synthesis/spawn (see the say note).
        with self._current_lock:
            epoch = self._cancel_epoch if cancel_epoch is None else cancel_epoch
        proc = (runner(audio_path) if audio_path is not None
                else runner(text, self._voice, self._rate))
        if proc is None:
            return False                # afplay could not spawn / the file vanished
        with self._current_lock:
            interrupted = self._cancel_epoch != epoch
            if not interrupted:
                self._current = proc
        if interrupted:
            proc.terminate()
            return False
```
(The `try/finally` wait block and `return getattr(proc, "returncode", None) == 0` are unchanged.)
Run `pytest tests/test_speaker.py tests/test_speaker_cancel_2b.py -q` → PASS.

### Step 3.3 RED — `SpeechItem.audio_path`

`tests/test_queue.py` add:
```python
def test_speech_item_audio_path_defaults_none_and_is_settable():
    from sonari.queue import SpeechItem
    a = SpeechItem(id=1, session="s", kind="prose", text="hi", is_decision=False)
    assert a.audio_path is None
    b = SpeechItem(id=2, session="s", kind="prose", text="hi", is_decision=False,
                   audio_path="/cache/x.aiff")
    assert b.audio_path == "/cache/x.aiff"
```
Run → FAIL.

### Step 3.4 GREEN — add the field

`src/sonari/queue.py` `SpeechItem` currently ends:
```python
    names_session: bool = False  # text already speaks the session's folder (jump cue)
```
add:
```python
    names_session: bool = False  # text already speaks the session's folder (jump cue)
    audio_path: "str | None" = None  # when set, the speak loop afplays this file (spearcon) instead of say
```

### Step 3.5 GREEN — thread `audio_path` through `_enqueue`

`host.py` `_enqueue` signature currently:
```python
    def _enqueue(self, session: str, kind: str, text: str, is_decision: bool,
                 entry=None, mute_exempt: bool = False,
                 pause_exempt: bool = False, at_front: bool = False,
                 names_session: bool = False) -> None:
        item = SpeechItem(
            id=self._alloc_id(),
            session=session,
            kind=kind,
            text=text,
            is_decision=is_decision,
            mute_exempt=mute_exempt,
            pause_exempt=pause_exempt,
            names_session=names_session,
        )
```
becomes:
```python
    def _enqueue(self, session: str, kind: str, text: str, is_decision: bool,
                 entry=None, mute_exempt: bool = False,
                 pause_exempt: bool = False, at_front: bool = False,
                 names_session: bool = False, audio_path=None) -> None:
        item = SpeechItem(
            id=self._alloc_id(),
            session=session,
            kind=kind,
            text=text,
            is_decision=is_decision,
            mute_exempt=mute_exempt,
            pause_exempt=pause_exempt,
            names_session=names_session,
            audio_path=audio_path,
        )
```

### Step 3.6 RED — the speak loop afplays an audio_path item

First extend `FakeSpeaker.speak` in `tests/daemon_helpers.py` (current):
```python
    def speak(self, text: str, cancel_epoch=None) -> bool:
        self.spoken.append(text)
        return self.complete
```
becomes (and add `self.audio_paths: list = []` in `__init__` next to `self.spoken`):
```python
    def speak(self, text=None, audio_path=None, cancel_epoch=None) -> bool:
        self.spoken.append(text)
        self.audio_paths.append(audio_path)
        return self.complete
```
Then in `tests/test_daemon_loop.py` add:
```python
def test_speak_loop_afplays_an_audio_path_item():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    queue.enqueue(SpeechItem(id=1, session="fg", kind="prose", text="backend.",
                             is_decision=False, audio_path="/cache/b.aiff"))
    daemon._speak_loop_once()
    assert speaker.audio_paths == ["/cache/b.aiff"]      # routed to afplay path
    assert speaker.spoken == ["backend."]                # text carried (fallback label)
```
Run → FAIL (loop never passes `audio_path`).

### Step 3.7 GREEN — speak-loop conditional-pass (both branches)

In `host.py` `_speak_loop_once`, the **held (stopped) branch** currently:
```python
            try:
                completed = self.speaker.speak(item.text, cancel_epoch=cancel_epoch)
            except Exception:  # noqa: BLE001 - one bad cue must not wedge the hold
                self._signal_speak_failure()
                completed = False
```
becomes:
```python
            try:
                if item.audio_path:
                    completed = self.speaker.speak(
                        item.text, audio_path=item.audio_path, cancel_epoch=cancel_epoch)
                else:
                    completed = self.speaker.speak(item.text, cancel_epoch=cancel_epoch)
            except Exception:  # noqa: BLE001 - one bad cue must not wedge the hold
                self._signal_speak_failure()
                completed = False
```
The **normal branch** currently:
```python
        try:
            completed = self.speaker.speak(text, cancel_epoch=cancel_epoch)
        except Exception:  # noqa: BLE001 - one bad utterance must not abort the item
            self._signal_speak_failure()
            completed = False
```
becomes:
```python
        try:
            if item.audio_path:
                completed = self.speaker.speak(
                    text, audio_path=item.audio_path, cancel_epoch=cancel_epoch)
            else:
                completed = self.speaker.speak(text, cancel_epoch=cancel_epoch)
        except Exception:  # noqa: BLE001 - one bad utterance must not abort the item
            self._signal_speak_failure()
            completed = False
```
Run `pytest tests/test_daemon_loop.py tests/test_concurrency_guards.py tests/test_daemon_where_am_i.py tests/test_daemon_stop.py tests/test_daemon_streams.py -q`, then the FULL suite → 796/1skip + new green. **Concurrency note:** `_ReentrantSpeaker.speak(text, cancel_epoch=None)` and every inline speak fake (boom/interrupted/interrupting/lambda) are untouched — the conditional-pass only adds the `audio_path=` arm, which fires solely for items that carry one, and those tests enqueue none. **Commit:** `feat(sonari): generalize Speaker.speak to audio_path; thread through the speak loop`.

---

## Task 4 — Wire the 5 name-cue sites to a daemon-owned `SpearconCache`

Depends on Task 2 (cache), Task 3 (`audio_path` field + speak path), Task 1 (the chirp lines already
in `on_cycle_session`/`on_nav` — quote the **Task-1-modified** handlers below). Includes cache
ownership, the `_spearcon_path` helper, the make_daemon fake, bootstrap build + cleanup, and
SessionStart pre-generation.

**Files:** `src/sonari/daemon/host.py` (`__init__` + `_spearcon_path`), `bootstrap.py`,
`features/focus.py`, `navigation.py`, `playback.py`, `control.py`, `lifecycle.py`,
`tests/daemon_helpers.py` (FakeSpearconCache + inject), `tests/test_daemon_spearcon.py` (new).

**Interfaces:**
- `SpeechDaemon.__init__(self, speaker, sessions, config, raise_service=None, spearcons=None)`;
  `self._spearcons = spearcons`.
- `host._spearcon_path(self, folder) -> "str | None"` → `self._spearcons.get(folder)` (cache truncates).
- FakeSpearconCache: `available: dict[str,str]` (seedable hits), records `requested`, `generated`,
  `pregenerated`, `cleaned`.

### Step 4.0a GREEN — daemon owns the cache + `_spearcon_path`

`host.py` `__init__` signature:
```python
    def __init__(self, speaker, sessions, config, raise_service=None) -> None:
```
becomes:
```python
    def __init__(self, speaker, sessions, config, raise_service=None,
                 spearcons=None) -> None:
```
and after `self.raise_service = raise_service` (currently line 51) add:
```python
        self._spearcons = spearcons          # SpearconCache, or None (no spearcons)
```
Add the helper near `_enqueue` (e.g. after `_drop_pending`):
```python
    def _spearcon_path(self, folder) -> "str | None":
        """The cached spearcon audio file for *folder*'s short label, or None when no
        cache is wired or the file isn't generated yet (the caller then falls back to
        plain speech and the cache kicks off background generation for next time).
        Never blocks; never on the hot path."""
        if not folder or self._spearcons is None:
            return None
        return self._spearcons.get(folder)
```

### Step 4.0b GREEN — FakeSpearconCache + inject into make_daemon

In `tests/daemon_helpers.py` add a fake and inject it (so unit tests never spawn real `say`):
```python
class FakeSpearconCache:
    """In-memory stand-in for SpearconCache. `available` maps a folder -> a fake
    cached audio path (a HIT); everything else is a MISS (returns None and records
    the request as a generation kick)."""

    def __init__(self):
        self.available: dict[str, str] = {}
        self.requested: list[str] = []
        self.generated: list[str] = []
        self.pregenerated: list[str] = []
        self.cleaned = None

    def get(self, label):
        self.requested.append(label)
        hit = self.available.get(label)
        if hit is None:
            self.generated.append(label)
        return hit

    def generate(self, label):
        self.generated.append(label)

    def pregenerate(self, labels):
        self.pregenerated.extend(labels)

    def cleanup(self, max_files=256):
        self.cleaned = max_files
```
In `make_daemon`, the construction line currently:
```python
    daemon = SpeechDaemon(speaker, sessions, config)
```
becomes:
```python
    daemon = SpeechDaemon(speaker, sessions, config, spearcons=FakeSpearconCache())
```
(`daemon._spearcons` is now a recording fake; tests seed `daemon._spearcons.available[folder] = path`.)
This default — empty `available`, i.e. **all-miss** — is why every existing cue test keeps its spoken
fallback and stays green.

### Step 4.0c GREEN — bootstrap: wire `afplay_runner`, build the real cache + start-time cleanup

**TWO edits to `bootstrap.py`.** No unit test reaches `bootstrap.main()`, so neither is test-covered —
both are verified at the sacrificial-HOME dogfood. The first is correctness-critical: without it
`Speaker._afplay_runner` is `None` and **every spearcon item plays nothing** (silent name cues — the
worst eyes-free failure), yet the suite stays green at 796/1skip because Task-3 Speaker tests inject
`afplay_runner=` and all daemon tests use `FakeSpeaker`.

**Edit 1 — wire the afplay runner.** The `Speaker(...)` construction currently:
```python
    speaker = Speaker(
        voice=cfg.get("voice"),
        rate=cfg.get("rate", 200),
        say_runner=_backend.tts.run,
        earcon_player=_backend.earcon.play,
        earcons=cfg.get("earcons"),
    )
```
becomes:
```python
    speaker = Speaker(
        voice=cfg.get("voice"),
        rate=cfg.get("rate", 200),
        say_runner=_backend.tts.run,
        afplay_runner=_backend.earcon.play,   # spearcon audio_path playback (same afplay)
        earcon_player=_backend.earcon.play,
        earcons=cfg.get("earcons"),
    )
```
(Belt-and-suspenders option, implementer's call: in `Speaker.speak` use
`runner = self._afplay_runner or self._earcon_player` for the audio branch — both are the same afplay
function — so the cue can't go silent even if this wiring is ever dropped. Explicit wiring alone is
sufficient.)

**Edit 2 — build the cache + prune.** Currently:
```python
    sessions = SessionManager(background_policy=cfg.get("background_policy", "earcon_only"))
    daemon = SpeechDaemon(speaker, sessions, cfg)
    daemon.run()
```
becomes:
```python
    sessions = SessionManager(background_policy=cfg.get("background_policy", "earcon_only"))
    from sonari.spearcon import SpearconCache
    from sonari.paths import SONARI_DIR
    spearcons = SpearconCache(
        SONARI_DIR / "spearcons",
        voice=cfg.get("spearcon_voice", "Samantha"),
        rate=cfg.get("spearcon_rate", 525),
    )
    spearcons.cleanup()                       # prune stale cache files at daemon start
    daemon = SpeechDaemon(speaker, sessions, cfg, spearcons=spearcons)
    daemon.run()
```
(Run the full suite after 4.0a–c; behavior is unchanged in tests because the fake is all-miss and
bootstrap isn't unit-exercised — should stay 796/1skip.)

### Step 4.1 RED — the 5 cue sites produce spearcon audio_path items on a hit

`tests/test_daemon_spearcon.py`:
```python
import threading

from tests.daemon_helpers import make_daemon


def _hit(daemon, folder, path="/cache/sp.aiff"):
    daemon._spearcons.available[folder] = path
    return path


def test_cycle_uses_spearcon_audio_path_on_hit():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha"); sessions.register("B", cwd="/x/bravo")
    sessions.set_foreground("A")
    p = _hit(daemon, "bravo")
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    item = daemon._stream("B").queue._items[0]
    assert item.audio_path == p
    assert item.names_session and item.mute_exempt
    daemon._speak_loop_once()
    assert speaker.audio_paths == [p]                 # afplayed, not spoken


def test_cycle_falls_back_to_speech_on_miss():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha"); sessions.register("B", cwd="/x/bravo")
    sessions.set_foreground("A")
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    item = daemon._stream("B").queue._items[0]
    assert item.audio_path is None
    daemon._speak_loop_once()
    assert speaker.spoken == ["bravo."]               # unchanged spoken cue
    assert "bravo" in daemon._spearcons.generated     # kicked background gen


def test_nav_crossed_folder_cue_uses_spearcon_on_hit():
    from sonari.sessions import Identity
    daemon, q, speaker, sessions, _ = make_daemon(foreground="b")
    sessions.register("a", cwd="/work/frontend")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sessions.register("b", cwd="/work/api"); sessions.set_foreground("b")
    daemon.history.record("a", "prose", "a-m0"); daemon.history.end_message("a")
    daemon.history.record("a", "prose", "a-m1")
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")
    p = _hit(daemon, "frontend")
    daemon.handle_message({"type": "nav", "to": "prev", "session": "a"})
    cue = daemon._stream("a").queue._items[0]
    assert cue.audio_path == p and cue.names_session


def test_jump_decision_crossed_cue_uses_spearcon_on_hit():
    from sonari.sessions import Identity
    daemon, q, speaker, sessions, _ = make_daemon(foreground="b")
    sessions.register("a", cwd="/work/frontend")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sessions.register("b", cwd="/work/api"); sessions.set_foreground("b")
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")
    p = _hit(daemon, "frontend")
    daemon.handle_message({"type": "jump_decision", "session": "a"})
    cue = daemon._stream("a").queue._items[0]
    assert cue.audio_path == p and cue.names_session


def test_jump_waiting_uses_spearcon_then_keeps_actionable_suffix_on_hit():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/fg")
    sessions.register("bk", cwd="/x/backend")
    daemon._enqueue("bk", "prose", "needs you", True)        # a decision -> jump target
    p = _hit(daemon, "backend")
    daemon.handle_message({"type": "jump_waiting", "session": "fg"})
    items = daemon._stream("bk").queue._items
    assert items[0].audio_path == p and items[0].names_session   # spearcon first
    # "Bring it forward to type." retained as speech when not raising (no audio_path)
    assert any(it.text == "Bring it forward to type." and it.audio_path is None
               for it in items)
    assert not any("Jumping to" in it.text for it in items)      # verb dropped (ear-tunable)


def test_where_am_i_splits_spearcon_then_state_on_hit():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    p = _hit(daemon, "work")
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()                 # spearcon plays first
    daemon._speak_loop_once()                 # then the state speech
    assert speaker.audio_paths[0] == p
    assert speaker.spoken[-1] == "Playing. 0 waiting."


def test_where_am_i_miss_keeps_combined_cue():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()
    assert speaker.spoken == ["work. Playing. 0 waiting."]   # unchanged on miss


def test_session_start_pregenerates_in_background():
    daemon, q, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message({"type": "session_start", "session": "s1", "cwd": "/x/proj"})
    assert "proj" in daemon._spearcons.pregenerated
```
Run → FAIL (cue sites don't pass audio_path; no pregenerate).

### Step 4.2 GREEN — `on_cycle_session` (Task-1-modified tail)

Current (post-Task-1) tail:
```python
    ctx.host.speaker.pitch("up" if step == 1 else "down")   # directional chirp first
    sessions.focus(target)
    ctx.host.speaker.cancel()
    folder = sessions.folder(target)
    cue = folder + "." if folder else "Another session."
    ctx.host._enqueue(target, "prose", cue, False,
                      mute_exempt=True, at_front=True, names_session=True)
    return None
```
becomes:
```python
    ctx.host.speaker.pitch("up" if step == 1 else "down")   # directional chirp first
    sessions.focus(target)
    ctx.host.speaker.cancel()
    folder = sessions.folder(target)
    cue = folder + "." if folder else "Another session."
    ctx.host._enqueue(target, "prose", cue, False,
                      audio_path=ctx.host._spearcon_path(folder),
                      mute_exempt=True, at_front=True, names_session=True)
    return None
```

### Step 4.3 GREEN — `on_nav` crossed cue (Task-1-modified)

Current crossed block:
```python
        folder = sessions.folder(target)
        if folder:
            ctx.host._enqueue(target, "prose", folder + ".", False,
                              mute_exempt=True, at_front=True, names_session=True)
    return None
```
becomes:
```python
        folder = sessions.folder(target)
        if folder:
            ctx.host._enqueue(target, "prose", folder + ".", False,
                              audio_path=ctx.host._spearcon_path(folder),
                              mute_exempt=True, at_front=True, names_session=True)
    return None
```

### Step 4.4 GREEN — `on_jump_decision` crossed cue (`playback.py`)

Current:
```python
    if crossed:
        folder = sessions.folder(target)
        if folder:
            ctx.host._enqueue(target, "prose", folder + ".", False,
                              mute_exempt=True, at_front=True, names_session=True)
    return None
```
becomes:
```python
    if crossed:
        folder = sessions.folder(target)
        if folder:
            ctx.host._enqueue(target, "prose", folder + ".", False,
                              audio_path=ctx.host._spearcon_path(folder),
                              mute_exempt=True, at_front=True, names_session=True)
    return None
```

### Step 4.5 GREEN — `on_jump_waiting` (`focus.py`) split on hit

Current:
```python
    base = ("Jumping to {0}.".format(folder) if folder
            else "Jumping to another session.")
    if not will_raise:
        base += " Bring it forward to type."
    ctx.host._enqueue(target, "prose", base, False,
                      mute_exempt=True, at_front=True, names_session=True)
    if will_raise:
        ctx.host._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: ctx.host._raise_failed(s, f))
    return None
```
becomes:
```python
    spearcon = ctx.host._spearcon_path(folder)
    if spearcon:
        # Spearcon names the destination (replaces the spoken "Jumping to {folder}.");
        # the actionable "Bring it forward to type." stays speech when not raising.
        # Enqueue the suffix FIRST (at_front), then the spearcon (at_front) so the
        # head order is: spearcon, [suffix].
        if not will_raise:
            ctx.host._enqueue(target, "prose", "Bring it forward to type.", False,
                              mute_exempt=True, at_front=True)
        ctx.host._enqueue(target, "prose", folder, False, audio_path=spearcon,
                          mute_exempt=True, at_front=True, names_session=True)
    else:
        base = ("Jumping to {0}.".format(folder) if folder
                else "Jumping to another session.")
        if not will_raise:
            base += " Bring it forward to type."
        ctx.host._enqueue(target, "prose", base, False,
                          mute_exempt=True, at_front=True, names_session=True)
    if will_raise:
        ctx.host._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: ctx.host._raise_failed(s, f))
    return None
```

### Step 4.6 GREEN — `on_where_am_i` (`control.py`) folder→spearcon split

Current tail (from `text = ...` to `return None`):
```python
    text = "{0}. {1}. {2} waiting.".format(folder, state, waiting)
    host.speaker.cancel()                          # barge-in: cut the current utterance
    if cur is not None:
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, mute_exempt=cur.mute_exempt,
                      pause_exempt=cur.pause_exempt, names_session=cur.names_session,
                      at_front=True)
    host._enqueue(fg, "prose", text, False,
                  mute_exempt=True, pause_exempt=True, at_front=True)
    return None
```
becomes:
```python
    host.speaker.cancel()                          # barge-in: cut the current utterance
    # Resume-after-interjection: re-queue the interrupted item FIRST so it ends up
    # DEEPEST (the status cue / spearcon are appendleft'd in front of it below).
    if cur is not None:
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, mute_exempt=cur.mute_exempt,
                      pause_exempt=cur.pause_exempt, names_session=cur.names_session,
                      audio_path=cur.audio_path, at_front=True)
    spearcon = host._spearcon_path(folder)
    if spearcon:
        # Spearcon names the session (replaces the spoken folder); state + count stay
        # speech. Enqueue state FIRST (at_front), then the spearcon (at_front) so the
        # head order is: spearcon, state, [resumed item].
        host._enqueue(fg, "prose", "{0}. {1} waiting.".format(state, waiting),
                      False, mute_exempt=True, pause_exempt=True, at_front=True)
        host._enqueue(fg, "prose", folder, False, audio_path=spearcon,
                      mute_exempt=True, pause_exempt=True, at_front=True,
                      names_session=True)
    else:
        host._enqueue(fg, "prose",
                      "{0}. {1}. {2} waiting.".format(folder, state, waiting),
                      False, mute_exempt=True, pause_exempt=True, at_front=True)
    return None
```
(Note: `cur.audio_path` is now read on the resume re-queue — preserves a spearcon item interrupted
mid-play. Requires the Task-3 `SpeechItem.audio_path`.)

### Step 4.7 GREEN — SessionStart background pre-generation (`lifecycle.py`)

In `on_set_foreground`, the `if t == MsgType.SESSION_START:` block currently ends:
```python
        _maybe_guide_setup(ctx, session, msg.get("plugin_version", ""))
    return None
```
becomes:
```python
        _maybe_guide_setup(ctx, session, msg.get("plugin_version", ""))
        if ctx.host._spearcons is not None:
            # Pre-render spearcons for the known roster in the background (Popen,
            # non-blocking); skips already-cached labels. Never on the hot path.
            ctx.host._spearcons.pregenerate(
                [ctx.host.sessions.folder(s) for s in ctx.host.sessions.session_ids()])
    return None
```
Run `pytest tests/test_daemon_spearcon.py` then the FULL suite → 796/1skip + new green. **Commit:** `feat(sonari): spearcon name-cues at the 5 folder sites + SessionStart pregen`.

---

## Task 5 — Earcon-set shrink (drop `ready`)

Independent of Tasks 1–4. `turn_done` and all decision/waiting/error earcons kept.

**Files:** `src/sonari/platform/macos/earcon.py`, `src/sonari/hooks_entry.py`,
`tests/test_macos_earcon.py`, `tests/test_hooks_entry.py`.

### Step 5.1 RED — default map no longer carries `ready`; idle_prompt emits nothing

`tests/test_macos_earcon.py` `test_default_earcons_are_macos_system_sounds` set becomes:
```python
    assert set(d) == {"permission", "choice", "plan", "error", "turn_done", "waiting"}
```
`tests/test_hooks_entry.py`:
```python
def test_notification_idle_prompt():
    payload = {"session_id": "sess-1", "notification_type": "idle_prompt"}
    assert handle_event("Notification", payload) == []


def test_notification_idle_prompt_from_fixture():
    payload = _load("Notification-idle_prompt.json")
    assert handle_event("Notification", payload) == []
```
Run → FAIL.

### Step 5.2 GREEN — drop `ready`

`src/sonari/platform/macos/earcon.py` `_DEFAULTS` currently:
```python
_DEFAULTS = {
    "permission": "/System/Library/Sounds/Funk.aiff",
    "choice":     "/System/Library/Sounds/Ping.aiff",
    "plan":       "/System/Library/Sounds/Submarine.aiff",
    "error":      "/System/Library/Sounds/Sosumi.aiff",
    "turn_done":  "/System/Library/Sounds/Tink.aiff",
    "ready":      "/System/Library/Sounds/Glass.aiff",
    "waiting":    "/System/Library/Sounds/Pop.aiff",
}
```
becomes (remove the `ready` line):
```python
_DEFAULTS = {
    "permission": "/System/Library/Sounds/Funk.aiff",
    "choice":     "/System/Library/Sounds/Ping.aiff",
    "plan":       "/System/Library/Sounds/Submarine.aiff",
    "error":      "/System/Library/Sounds/Sosumi.aiff",
    "turn_done":  "/System/Library/Sounds/Tink.aiff",
    "waiting":    "/System/Library/Sounds/Pop.aiff",
}
```
`src/sonari/hooks_entry.py` Notification block currently:
```python
        if nt == "idle_prompt":
            return [_msg(type=MsgType.EARCON, kind="ready")]
        return []
```
becomes (remove the branch; `idle_prompt` falls through to the empty return):
```python
        return []
```
Run the FULL suite → 796/1skip + the changed earcon/hooks tests green. **Commit:** `feat(sonari): drop the ready earcon (idle_prompt is silent)`.

---

## Self-Review

### Spec §17 coverage
- **§17.1 spearcons** — `say -v <voice> -r 525 -o <key>.aiff`, voice default Samantha / rate 525 (Task 2
  config + cache); `~/.sonari/spearcons/`, sha256(voice|rate|short_label)[:16] (Task 2 `_key`);
  background Popen pre-gen on SessionStart (Task 4.7), generate-on-first-need (Task 2 `get` miss),
  start-time prune (Task 2 `cleanup` + Task 4.0c), generation never on the hot path (Popen-only);
  queue-integrated playback with cancel_epoch barge-in (Task 3); generalized
  `Speaker.speak(text, audio_path, cancel_epoch)` (Task 3); replaces the 5 sites — cycle / jump_waiting
  / jump_decision-folder / where_am_i-folder / nav-crossed (Task 4); `_attributed_text` prefix left as
  full speech (untouched); first-word/12-char truncation (Task 2 `spearcon_label`). ✓
- **§17.2 pitch chirps** — committed `scripts/gen_pitch_tones.py` (stdlib wave+struct+math); Set A
  440→880 / 880→440, 200 ms, 5 ms cosine fades, 44100/16-bit mono (Task 1); fire-and-forget afplay
  direct from the package asset path, NOT the earcons dict (Task 1 `Speaker.pitch`); 4 directional
  sites cycle/nav/nav_response/answer with chirp-first composition (Tasks 1.7 + the chirp is enqueued
  before the spearcon cue); non-directional handlers get no chirp (verified by the error-case tests). ✓
- **§17.3 earcon shrink** — drop `ready` from the map + the idle_prompt branch; keep
  turn_done/waiting/error/choice/plan/permission (Task 5). ✓
- **§17.4 testing boundary** — cache-key/generation (mocked Popen), audio_path playback + barge-in,
  chirp dispatch at 4 sites, earcon-shrink all unit-tested; the 2 permanent concurrency guards +
  barge-in tests stay green (Task 3 conditional-pass); real say/afplay + on-hardware listening left to
  the sacrificial-HOME dogfood + Nima's gate. ✓

### Placeholder scan
No `TODO`/`...`/`<placeholder>` in any code block; every RED test and GREEN edit quotes real current
code and gives complete replacement code. Generator, cache, Speaker, queue, host, 6 handlers,
bootstrap, earcon map, and hooks entry are all spelled out.

### Type consistency across tasks
- `SpeechItem.audio_path: "str | None" = None` (Task 3) is consumed by `_enqueue(audio_path=None)`
  (Task 3), the speak loop's `item.audio_path` (Task 3), and every Task-4 cue site — consistent
  `str | None`.
- `Speaker.speak(text=None, audio_path=None, cancel_epoch=None) -> bool` (Task 3) matches the loop's
  two-arm calls and the extended `FakeSpeaker.speak` (Task 3). `afplay_runner` is `(path) -> proc|None`
  wired to `_backend.earcon.play` in production — **a concrete edit in Step 4.0c (Edit 1)**, not a flag,
  since no unit test reaches bootstrap and a missing wire makes every spearcon silent.
- `_spearcon_path(folder) -> str | None` (Task 4) → `cache.get(folder) -> str | None`; the real cache
  and `FakeSpearconCache` share the `get/generate/pregenerate/cleanup` surface.
- `spearcon_label(folder) -> str` (Task 2) is the single truncation owner; the daemon and cue sites
  pass raw folders.

### Cheap safety greps (run during the named task)
- **Before Task 5:** `grep -rn 'ready' tests/ src/sonari/` — confirm only `test_macos_earcon` (the set)
  and the 2 hooks tests reference the `ready` earcon; rule out a third daemon-side assertion.
- **Before Task 2:** confirm no test outside `test_config.py` asserts an exact config-dict shape (a
  written-file exact-contents round-trip would break on the 2 new keys; `assert loaded == DEFAULTS`
  cases are safe — both sides move together).

### Open flags for the live gate (dogfood / Nima's ear)
1. **bootstrap `afplay_runner` wiring + cache build** — now a concrete edit (Step 4.0c, Edit 1 & 2);
   neither is unit-test-covered (no test reaches `bootstrap.main()`), so verify spearcons actually
   play and stale files prune at the **sacrificial-HOME dogfood**.
2. **Installed-daemon asset path** — `Speaker.pitch` resolves `Path(__file__).parent/assets`; verify
   `sonari install` copies `assets/*.wav` into APP_DIR (pyproject package-data covers pip). Confirm at
   the sacrificial-HOME dogfood.
3. **`cleanup()` is an mtime-LRU prune**, not label-keyed (no sessions exist at start) — confirm this
   reading with the owner.
4. **jump_waiting verb-drop** ("Jumping to" removed on a spearcon hit) — ear-tunable at the live gate.
