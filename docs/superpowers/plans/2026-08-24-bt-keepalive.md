# Bluetooth Keep-Alive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hold the audio output device open with a silent stream while ≥1 live session exists, so Bluetooth A2DP never suspends between utterances and utterance heads/earcons stop being swallowed.

**Architecture:** A new `KeepAliveManager` (daemon-owned, `src/sonari/daemon/keepalive.py`) spawns raw `afplay` children of a runtime-generated silent WAV through a dedicated test seam, with an overlap timer so the stream never gaps (a sequential respawn measurably leaks — see spec). The `SpeechDaemon` pushes policy verdicts (`any live session?`) into it from the two lifecycle handlers (prompt start) and once per speak-loop tick (eventual stop for ghost sessions), and a `SET_KEEPALIVE` message + CLI verb toggles it live.

**Tech Stack:** Python stdlib only (`wave`, `threading.Timer`, `subprocess.Popen`). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-24-bt-keepalive-design.md` — read it first; it carries the measured evidence and every ratified decision.

## Global Constraints

- **Every pytest invocation runs under a sacrificial HOME**: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q ...`. Never run tests or ad-hoc probes with the real HOME; never touch `~/.sonari`; never run `sonari install`.
- Baseline at branch point ce56659: **1532 passed, 1 skipped**. The full suite must stay green after every task.
- Python stdlib only in `src/sonari/` (no new deps in `pyproject.toml`).
- Do NOT bump the version files (`pyproject.toml`, `.claude-plugin/plugin.json`, `src/sonari/__init__.py`) — version lockstep is decided at merge because the parked wave-1 branch already claims 0.10.1.
- Commit messages: conventional style matching `git log` (`feat:`, `fix:`, `docs:`, `test:`); **no AI attribution, no Claude-Session trailers** — repo must read human-authored.
- The daemon lock (`SessionState.transaction()`, a non-reentrant `threading.Lock`) is held by callers of manager methods in handlers. **The manager must never acquire the daemon lock.** Its own `_lock` is ordered strictly after the daemon lock and is never held while calling back into daemon/session state.
- Timer discipline mirrors `host.py`'s learn-mode timer (`host.py:703-725`): timer objects close over their own identity, the callback bails unless it is still the live timer, `timer.daemon = True`.

---

### Task 1: Silent WAV asset — paths constant, generator, conftest repoint

**Files:**
- Modify: `src/sonari/paths.py` (add one constant near `STATE_PATH`)
- Create: `src/sonari/daemon/keepalive.py` (module docstring + `ensure_silence_wav` only; the manager class arrives in Task 2)
- Modify: `tests/conftest.py` (add `KEEPALIVE_WAV_PATH` to the `sonari.paths` repoint list in the autouse `_isolate_sonari_dir` fixture)
- Test: `tests/test_keepalive_asset.py`

**Interfaces:**
- Consumes: `sonari.paths.SONARI_DIR` (existing).
- Produces: `sonari.paths.KEEPALIVE_WAV_PATH: Path`; `sonari.daemon.keepalive.ensure_silence_wav() -> str` (absolute path, generates if missing/short, idempotent); module constants `SILENCE_S = 300.0`, `_RATE = 8000`. Task 2 and 3 rely on these exact names.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_keepalive_asset.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q tests/test_keepalive_asset.py`
Expected: FAIL — `ImportError` (no `sonari.daemon.keepalive`) / `AttributeError` (`paths.KEEPALIVE_WAV_PATH`).

- [ ] **Step 3: Implement**

In `src/sonari/paths.py`, next to `STATE_PATH`:

```python
KEEPALIVE_WAV_PATH = SONARI_DIR / "keepalive.wav"
```

Create `src/sonari/daemon/keepalive.py`:

```python
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
```

In `tests/conftest.py`, find the `sonari.paths` entry of the autouse repoint fixture and add `KEEPALIVE_WAV_PATH` to its constant list, following the exact style of the neighboring entries (e.g. `STATE_PATH`). Comment it the way the neighbors are commented.

- [ ] **Step 4: Run tests to verify they pass, then the full suite**

Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q tests/test_keepalive_asset.py` → PASS (4)
Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q` → 1536 passed, 1 skipped

- [ ] **Step 5: Commit**

```bash
git add src/sonari/paths.py src/sonari/daemon/keepalive.py tests/conftest.py tests/test_keepalive_asset.py
git commit -m "feat(keepalive): runtime-generated silent WAV asset"
```

---

### Task 2: KeepAliveManager — players, overlap, hold, degraded

**Files:**
- Modify: `src/sonari/daemon/keepalive.py` (add the class)
- Test: `tests/test_keepalive_manager.py`

**Interfaces:**
- Consumes: `ensure_silence_wav()` from Task 1.
- Produces (Task 3/4/5 rely on these exact signatures):
  - `KeepAliveManager(popen=None, timer_factory=None, clock=None)`
  - `.set_enabled(on: bool) -> None` — config knob; `False` stops everything and pins state `"disabled"`.
  - `.set_active(active: bool) -> None` — policy verdict; `True` cancels any hold and ensures a player; `False` arms the hold timer.
  - `.tick() -> None` — reap dead players; respawn with backoff; count fast deaths toward degraded.
  - `.stop() -> None` — shutdown: cancel timers, terminate players, bounded reap.
  - `.status() -> str` — one of `"running"`, `"hold"`, `"idle"`, `"degraded"`, `"disabled"`.
  - Class constants `HOLD_S = 600.0`, `OVERLAP_S = 5.0`, `GIVEUP_N = 5`, `FAST_DEATH_S = 2.0`, `BACKOFF_S = 1.0`.

**Design constraints the implementation MUST honor (from the spec):**
- Overlap, never gap: the next player spawns `OVERLAP_S` before the current file ends (timer-driven — tick cadence is too sparse to hit the window). A measured sequential respawn leaks a teardown at file boundaries.
- Bounded failure: a player that dies within `FAST_DEATH_S` of spawn counts toward `GIVEUP_N` consecutive fast deaths → state `"degraded"`, no more spawns until the next `set_active(False)`→`set_active(True)` edge (which resets the counter). This is the anti-spin-storm requirement; the wave-1 `_signal_speak_failure` Critical is the failure shape being excluded.
- Locking: only the manager's own `self._lock`; never the daemon lock; never hold `self._lock` while calling `popen`/`terminate`? — No: holding it across `popen` is acceptable (popen does not reenter the manager), but the hold/overlap timer callbacks MUST re-check timer identity under `self._lock` exactly like `_learn_mode_expired` re-checks `self._learn_timer is not timer`.
- All spawns go through `self._popen(["afplay", ensure_silence_wav()])`. No shell, no Speaker, no `_play_lock`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_keepalive_manager.py
"""KeepAliveManager unit tests — all subprocess/timer/clock seams faked.
Spec: docs/superpowers/specs/2026-08-24-bt-keepalive-design.md."""
import threading

from sonari.daemon.keepalive import KeepAliveManager


class FakeProc:
    def __init__(self, cmd):
        self.cmd = cmd
        self.terminated = False
        self._rc = None

    def poll(self):
        return self._rc

    def wait(self, timeout=None):
        return self._rc if self._rc is not None else 0

    def terminate(self):
        self.terminated = True
        self._rc = -15

    def die(self, rc=1):
        self._rc = rc


class FakeTimer:
    """Records (interval, fn); fires only when the test calls .fire()."""
    instances = []

    def __init__(self, interval, fn):
        self.interval = interval
        self.fn = fn
        self.cancelled = False
        self.started = False
        self.daemon = False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.fn()


def make_mgr(now=None):
    FakeTimer.instances = []
    spawned = []
    clock = {"t": 0.0}

    def popen(cmd):
        proc = FakeProc(cmd)
        spawned.append(proc)
        return proc

    mgr = KeepAliveManager(popen=popen, timer_factory=FakeTimer,
                           clock=lambda: clock["t"])
    return mgr, spawned, clock


def _timers(kind=None):
    return [t for t in FakeTimer.instances if not t.cancelled and t.started]


def test_activation_spawns_one_player_and_arms_overlap_timer():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    assert len(spawned) == 1
    assert spawned[0].cmd[0] == "afplay"
    assert spawned[0].cmd[1].endswith("keepalive.wav")
    overlaps = [t for t in FakeTimer.instances if t.interval == 295.0]
    assert len(overlaps) == 1 and overlaps[0].started
    assert mgr.status() == "running"


def test_set_active_true_is_idempotent():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    mgr.set_active(True)
    assert len(spawned) == 1


def test_overlap_timer_spawns_next_player_before_reaping_old():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    overlap = [t for t in FakeTimer.instances if t.interval == 295.0][0]
    clock["t"] = 295.0
    overlap.fire()
    assert len(spawned) == 2          # B spawned while A still runs — no gap
    assert not spawned[0].terminated  # A is reaped when it EXITS, never killed early
    rearmed = [t for t in FakeTimer.instances if t.interval == 295.0]
    assert len(rearmed) == 2          # a fresh overlap timer for B


def test_deactivate_arms_hold_then_expiry_stops_players():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    mgr.set_active(False)
    assert mgr.status() == "hold"
    assert not spawned[0].terminated              # still streaming during hold
    hold = [t for t in FakeTimer.instances if t.interval == 600.0][0]
    hold.fire()
    assert spawned[0].terminated
    assert mgr.status() == "idle"


def test_reactivation_during_hold_cancels_stop_and_keeps_stream():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    mgr.set_active(False)
    hold = [t for t in FakeTimer.instances if t.interval == 600.0][0]
    mgr.set_active(True)
    hold.fire()                                   # stale timer must be a no-op
    assert not spawned[0].terminated
    assert len(spawned) == 1                      # stream simply continued
    assert mgr.status() == "running"


def test_tick_respawns_dead_player_after_backoff():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    clock["t"] = 10.0
    spawned[0].die()
    mgr.tick()
    assert len(spawned) == 1                      # backoff: not instantly
    clock["t"] = 11.5
    mgr.tick()
    assert len(spawned) == 2


def test_five_consecutive_fast_deaths_degrade_and_stop_spawning():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    for _ in range(5):
        spawned[-1].die()
        clock["t"] += 0.5                         # died 0.5s after spawn: FAST
        mgr.tick()                                # observe death, counter++
        clock["t"] += 1.0                         # past BACKOFF_S
        mgr.tick()                                # respawn (until degraded)
    assert mgr.status() == "degraded"
    n = len(spawned)                              # 5: initial + 4 respawns
    clock["t"] += 100.0
    mgr.tick()
    assert len(spawned) == n                      # gave up — no spawn storm
    mgr.set_active(False)                         # no players left: no hold timer needed
    mgr.set_active(True)                          # fresh False->True edge resets the give-up
    assert len(spawned) == n + 1
    assert mgr.status() == "running"


def test_slow_death_does_not_count_toward_degraded():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    for _ in range(6):
        clock["t"] += 50.0                        # died long after spawn: SLOW
        spawned[-1].die()
        mgr.tick()                                # observe: counter resets
        clock["t"] += 1.5                         # past BACKOFF_S
        mgr.tick()                                # respawn
    assert mgr.status() == "running"
    assert len(spawned) == 7                      # initial + 6 respawns


def test_disabled_never_spawns_and_terminates_running():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    mgr.set_enabled(False)
    assert spawned[0].terminated
    assert mgr.status() == "disabled"
    mgr.set_active(True)
    assert len(spawned) == 1
    mgr.set_enabled(True)
    mgr.set_active(True)
    assert len(spawned) == 2


def test_stop_cancels_timers_and_terminates_players():
    mgr, spawned, clock = make_mgr()
    mgr.set_active(True)
    mgr.stop()
    assert spawned[0].terminated
    assert all(t.cancelled for t in FakeTimer.instances if t.started)
    assert mgr.status() == "idle"


def test_spawn_failure_goes_degraded_not_raise():
    FakeTimer.instances = []
    def popen(cmd):
        raise OSError("no afplay")
    mgr = KeepAliveManager(popen=popen, timer_factory=FakeTimer,
                           clock=lambda: 0.0)
    mgr.set_active(True)                          # must not raise
    assert mgr.status() == "degraded"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q tests/test_keepalive_manager.py`
Expected: FAIL — `ImportError: cannot import name 'KeepAliveManager'`.

- [ ] **Step 3: Implement `KeepAliveManager` in `src/sonari/daemon/keepalive.py`**

```python
class KeepAliveManager:
    """Owns the silent afplay children. Policy (who is live) stays in the
    daemon; this class only obeys set_enabled/set_active/tick/stop. Never
    touches the daemon lock or Speaker state — raw spawns via the _popen seam,
    the same isolation the §7 witness alarm uses."""

    HOLD_S = 600.0
    OVERLAP_S = 5.0
    GIVEUP_N = 5
    FAST_DEATH_S = 2.0
    BACKOFF_S = 1.0

    def __init__(self, popen=None, timer_factory=None, clock=None):
        import subprocess
        import threading
        import time
        self._popen = popen or subprocess.Popen
        self._timer_factory = timer_factory or threading.Timer
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._enabled = True
        self._want = False
        self._players = []          # (proc, spawned_at)
        self._overlap_timer = None
        self._hold_timer = None
        self._fast_deaths = 0
        self._degraded = False
        self._last_death = None     # monotonic time of last observed death
```

Implementation notes the code must follow (write real code, these are the invariants):
- `set_active(True)` has **ensure semantics, no early return on `_want` already true**: under `self._lock` — cancel+clear `_hold_timer`; on a **False→True edge only**, reset `_fast_deaths`/`_degraded`; set `_want = True`; then ensure: if `_enabled` and not `_degraded` and there is no player with `poll() is None` → `_spawn_locked()`. (Idempotence falls out: a live player blocks the spawn. A hold-in-progress just continues — its player is alive. After `set_enabled(False)`→`set_enabled(True)`, the players list is already empty because `_stop_players_locked()` cleared it, so the next `set_active(True)` respawns even though `_want` never went False.)
- `set_active(False)`: under lock — if not `_want`, return; clear `_want`; if players exist, arm `_hold_timer = timer_factory(HOLD_S, callback)` with the learn-timer identity discipline (callback closes over the timer object; under lock it bails unless `self._hold_timer is timer and not self._want`; then `_stop_players_locked()`, state → idle).
- `_spawn_locked()`: `proc = self._popen(["afplay", ensure_silence_wav()])` in try/except `Exception` → on failure set `_degraded = True` (a spawn that cannot even start is an immediate give-up; the OSError test pins this); on success append `(proc, self._clock())`, cancel any old `_overlap_timer`, arm a new one at `SILENCE_S - OVERLAP_S` (295.0) whose callback (identity-checked) spawns the next player via `_spawn_locked()` and prunes exited players (`poll() is not None` → drop; never `terminate()` a player that hasn't exited — the overlap exists so A and B run together).
- `tick()`: under lock — prune exited players, recording each observed death: if `now - spawned_at < FAST_DEATH_S` increment `_fast_deaths` else reset it to 0; if `_fast_deaths >= GIVEUP_N` set `_degraded = True` and `_stop_players_locked()` (defensive) — degraded spawns nothing until reset by a False→True edge; else if `_want and _enabled and not _players and not _degraded` and `now - (_last_death or 0) >= BACKOFF_S` → `_spawn_locked()`.
- `set_enabled(False)`: under lock — `_enabled = False`, cancel timers, `_stop_players_locked()`. `set_enabled(True)`: just flips the flag (the next `set_active`/`tick` re-evaluates).
- `stop()`: under lock — cancel both timers, `_stop_players_locked()`, clear `_want`.
- `_stop_players_locked()`: for each `(proc, _)`: `proc.terminate()` in try/except, then `proc.wait(timeout=2.0)` in try/except (bounded reap — mirror `_AfplayHandle.terminate`); clear the list.
- `status()` precedence: `"disabled"` if not enabled; `"degraded"` if degraded; `"running"` if `_want` (even during a momentary backoff gap with no player — the manager is actively trying); `"hold"` if players remain and not `_want`; else `"idle"`.
- `tick()` death-observation detail: a death is counted (fast vs slow) from `now - spawned_at` at OBSERVATION time; `_stop_players_locked()` clears the list before anything can observe those exits, so deliberate terminations never count toward degraded.

- [ ] **Step 4: Run tests to verify they pass, then the full suite**

Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q tests/test_keepalive_manager.py` → PASS (11)
Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q` → 1547 passed, 1 skipped

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/keepalive.py tests/test_keepalive_manager.py
git commit -m "feat(keepalive): manager — overlapped players, trailing hold, bounded failure"
```

---

### Task 3: SpeechDaemon wiring — policy pushes, tick, shutdown

**Files:**
- Modify: `src/sonari/daemon/host.py` (`__init__`, `_speak_loop_once`, `run()`'s `finally`)
- Modify: `src/sonari/daemon/features/lifecycle.py` (both handlers)
- Modify: `tests/daemon_helpers.py` (`make_daemon` injects inert keep-alive seams — **hermeticity-critical, see Step 3**)
- Test: `tests/test_keepalive_wiring.py`

**Interfaces:**
- Consumes: `KeepAliveManager` (Task 2), `sessions.is_live` / `sessions.session_ids` (existing).
- Produces: `SpeechDaemon.keepalive` (the manager instance) and `SpeechDaemon._keepalive_recheck() -> None` — Task 4's handler and Task 5's STATUS field use these exact names.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_keepalive_wiring.py
"""Keep-alive policy wiring: SESSION_START spawns, pending-only roster does not,
SESSION_END arms the hold, the tick notices ghosts, shutdown terminates.
Uses make_daemon + the manager's injectable seams."""
from sonari.protocol import PROTOCOL_VERSION, MsgType
from tests.daemon_helpers import make_daemon
from tests.test_keepalive_manager import FakeProc, FakeTimer


def _msg(t, session="s1", **kw):
    m = {"v": PROTOCOL_VERSION, "type": t, "session": session}
    m.update(kw)
    return m


def _seam(daemon):
    FakeTimer.instances = []
    spawned = []

    def popen(cmd):
        proc = FakeProc(cmd)
        spawned.append(proc)
        return proc

    daemon.keepalive._popen = popen
    daemon.keepalive._timer_factory = FakeTimer
    return spawned


def test_session_start_with_live_session_spawns_player():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SESSION_START))
    assert len(spawned) == 1
    assert daemon.keepalive.status() == "running"


def test_restored_pending_only_roster_does_not_spawn():
    # THE load-bearing policy test: registration alone is not liveness.
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    sessions.load_state({"ghost": "folder"})      # restored => _provisional => "pending"
    daemon._keepalive_recheck()
    assert spawned == []
    assert daemon.keepalive.status() == "idle"


def test_session_end_arms_hold_not_immediate_stop():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SESSION_START))
    daemon.handle_message(_msg(MsgType.SESSION_END))
    assert not spawned[0].terminated
    assert daemon.keepalive.status() == "hold"


def test_tick_notices_dead_tty_ghost(monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SESSION_START))
    assert daemon.keepalive.status() == "running"
    # The session's tty dies with no SESSION_END: liveness flips lazily.
    monkeypatch.setattr("sonari.sessions.SessionManager.is_live",
                        lambda self, s: False)
    daemon._keepalive_recheck()
    assert daemon.keepalive.status() == "hold"    # event never came; tick caught it


def test_keepalive_disabled_by_config_never_spawns():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    config["keepalive_enabled"] = False
    daemon.keepalive.set_enabled(False)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SESSION_START))
    assert spawned == []
    assert daemon.keepalive.status() == "disabled"


def test_recheck_never_raises_into_the_speak_loop(monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    monkeypatch.setattr("sonari.sessions.SessionManager.session_ids",
                        lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    daemon._keepalive_recheck()                   # must swallow, not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q tests/test_keepalive_wiring.py`
Expected: FAIL — `AttributeError: 'SpeechDaemon' object has no attribute 'keepalive'`.

- [ ] **Step 3: Implement**

`host.py` `__init__` (near where `self._persistence` is built):

```python
from sonari.daemon.keepalive import KeepAliveManager
self.keepalive = KeepAliveManager()
self.keepalive.set_enabled(bool(self.config.get("keepalive_enabled", True)))
```

`host.py`, new method next to `_check_witness`:

```python
def _keepalive_recheck(self) -> None:
    """Push the policy verdict into the keep-alive manager and let it reap.
    Called from the lifecycle handlers (under the daemon lock) and once per
    speak-loop tick (lock-free — session_ids() returns a snapshot copy and
    is_live() only reads; same discipline as _check_witness). A keep-alive
    bug must never take down speech: swallow everything."""
    try:
        alive = any(self.sessions.is_live(s)
                    for s in self.sessions.session_ids())
        self.keepalive.set_active(alive)
        self.keepalive.tick()
    except Exception:
        pass
```

`_speak_loop_once`: add `self._keepalive_recheck()` on the line directly after the existing `self._check_witness()` call (host.py:1017).

`run()`'s `finally` block: add `self.keepalive.stop()` alongside `self._persistence.stop()`.

`lifecycle.py`: at the end of the `if t == MsgType.SESSION_START:` body add `ctx.host._keepalive_recheck()`; in `on_session_end`, directly after `ctx.host.sessions.unregister(session)` add `ctx.host._keepalive_recheck()`.

**`tests/daemon_helpers.py` — hermeticity-critical, do this in the same commit as the wiring.** Roughly half the existing suite goes `make_daemon()` → `handle_message(SESSION_START)`; the moment the wiring lands, every one of those tests would hit `set_active(True)` on the DEFAULT seam and spawn a REAL `afplay` playing 300s of silence (orphaned past the suite, nondeterministic under the sandbox where afplay is blocked and the manager would flip to degraded). Every daemon built by `make_daemon` must therefore come out inert by default; the keep-alive tests override the seams afterward with their recording fakes, exactly as their `_seam()` helpers already do. Add to `daemon_helpers.py` (next to `FakeSpeaker`):

```python
class InertKeepaliveProc:
    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass


class InertKeepaliveTimer:
    def __init__(self, interval, fn):
        self.daemon = False

    def start(self):
        pass

    def cancel(self):
        pass
```

and in `make_daemon`, right after `daemon = SpeechDaemon(...)`:

```python
daemon.keepalive._popen = lambda cmd: InertKeepaliveProc()
daemon.keepalive._timer_factory = InertKeepaliveTimer
```

Note for the pending-roster test: read `SessionManager.load_state`'s actual signature in `src/sonari/sessions.py` (~line 358) before writing the call — the plan's `{"ghost": "folder"}` assumes the roster is an id→folder mapping; if the real shape differs, match it (the point of the test is only: restored ⇒ `_provisional` ⇒ not live ⇒ no spawn).

- [ ] **Step 4: Run tests to verify they pass, then the full suite**

Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q tests/test_keepalive_wiring.py` → PASS (6)
Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q` → 1553 passed, 1 skipped
(The `make_daemon` inert seams from Step 3 are what keep the existing ~1532 green — any real-`afplay` symptom here means those seams were skipped or a test constructs `SpeechDaemon` without `make_daemon`; grep for direct `SpeechDaemon(` constructions in tests/ and give any such site the same inert seams.)

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/host.py src/sonari/daemon/features/lifecycle.py tests/test_keepalive_wiring.py
git commit -m "feat(keepalive): session-scoped policy wiring in the daemon"
```

---

### Task 4: Config knob — SET_KEEPALIVE, CLI verb, slash command, docs regen

**Files:**
- Modify: `src/sonari/config.py` (DEFAULTS), `src/sonari/protocol.py` (MsgType), `src/sonari/daemon/features/control.py` (handler), `src/sonari/cli/control.py` (+ `src/sonari/cli/__init__.py` registration)
- Create: `commands/keepalive.md`
- Modify: `README.md` (regenerated island only — run `scripts/gen_docs.py`, never hand-edit between the markers)
- Test: `tests/test_keepalive_toggle.py`

**Interfaces:**
- Consumes: `SpeechDaemon.keepalive` / `_keepalive_recheck` (Task 3).
- Produces: `MsgType.SET_KEEPALIVE`; config key `"keepalive_enabled": True` in `DEFAULTS`; CLI `sonari keepalive on|off`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_keepalive_toggle.py
"""SET_KEEPALIVE: live toggle without a daemon restart, persisted to config."""
from sonari.config import DEFAULTS
from sonari.protocol import PROTOCOL_VERSION, MsgType
from tests.daemon_helpers import make_daemon
from tests.test_keepalive_manager import FakeProc, FakeTimer


def _msg(t, session="s1", **kw):
    m = {"v": PROTOCOL_VERSION, "type": t, "session": session}
    m.update(kw)
    return m


def _seam(daemon):
    FakeTimer.instances = []
    spawned = []
    daemon.keepalive._popen = lambda cmd: spawned.append(FakeProc(cmd)) or spawned[-1]
    daemon.keepalive._timer_factory = FakeTimer
    return spawned


def test_default_is_enabled():
    assert DEFAULTS["keepalive_enabled"] is True


def test_set_keepalive_off_terminates_and_persists():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SESSION_START))
    assert len(spawned) == 1
    daemon.handle_message(_msg(MsgType.SET_KEEPALIVE, enabled=False))
    assert spawned[0].terminated
    assert config["keepalive_enabled"] is False
    assert daemon.keepalive.status() == "disabled"


def test_set_keepalive_on_reapplies_policy_immediately():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    spawned = _seam(daemon)
    daemon.handle_message(_msg(MsgType.SET_KEEPALIVE, enabled=False))
    daemon.handle_message(_msg(MsgType.SESSION_START))
    assert spawned == []
    daemon.handle_message(_msg(MsgType.SET_KEEPALIVE, enabled=True))
    assert len(spawned) == 1                      # no restart, no new SESSION_START needed


def test_non_bool_payload_is_ignored():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    _seam(daemon)
    daemon.handle_message(_msg(MsgType.SET_KEEPALIVE, enabled="maybe"))
    assert config["keepalive_enabled"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q tests/test_keepalive_toggle.py`
Expected: FAIL — `AttributeError: SET_KEEPALIVE`.

- [ ] **Step 3: Implement**

- `config.py` `DEFAULTS`: add `"keepalive_enabled": True,` after `"submit_ack_enabled": False,`.
- `protocol.py`: add `SET_KEEPALIVE = "set_keepalive"` following the exact style of `SET_MINQUEUE`. **Grep for a MsgType-inventory-count test** (`grep -rn "MsgType" tests/ | grep -i -E "count|inventory|len"`) and update the pinned count if one exists.
- `daemon/features/control.py`, mirroring `on_set_minqueue` (control.py:268-277):

```python
@handler(MsgType.SET_KEEPALIVE)
def on_set_keepalive(ctx, msg):
    v = msg.get("enabled")
    if not isinstance(v, bool):
        return None
    ctx.host.config["keepalive_enabled"] = v
    save_config(ctx.host.config)
    ctx.host.keepalive.set_enabled(v)
    ctx.host._keepalive_recheck()
    return None
```

- `cli/control.py`, mirroring `_cmd_minqueue` (control.py:68-72):

```python
def _cmd_keepalive(args):
    enabled = args.state == "on"
    _send({"type": MsgType.SET_KEEPALIVE, "enabled": enabled})
    print(f"keepalive {'on' if enabled else 'off'}")
```

(Match the module's actual send helper and print conventions — read `_cmd_minqueue` and copy its shape exactly, including error handling on daemon-unreachable.)

- `cli/__init__.py`: register next to the minqueue parser:

```python
p = sub.add_parser("keepalive", help="Hold the audio device open while sessions are live (fixes Bluetooth clipping)")
p.add_argument("state", choices=["on", "off"])
p.set_defaults(func=control._cmd_keepalive)
```

- `commands/keepalive.md`, following `commands/minqueue.md`'s front-matter shape exactly:

```markdown
---
description: "Toggle the Bluetooth keep-alive (holds the audio device open while sessions are live; fixes clipped speech on Bluetooth headsets)"
---

Run `sonari keepalive $ARGUMENTS` (on|off).

While on and any session is live, Sonari streams silence so a Bluetooth
headset never suspends its audio link between utterances — without it, the
first fraction of each utterance (and whole short earcons) can be swallowed.
Costs while active: the headset's radio streams continuously (battery use
comparable to music playback) and the Mac will not idle-sleep.
```

- Regenerate the README island: `python3 scripts/gen_docs.py` (or the repo's documented invocation — check the script header), commit the regenerated README.

- [ ] **Step 4: Run tests to verify they pass, then the full suite (docs-sync test included)**

Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q tests/test_keepalive_toggle.py` → PASS (4)
Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q` → 1557 passed, 1 skipped (test_docs_sync green proves the regen)

- [ ] **Step 5: Commit**

```bash
git add src/sonari/config.py src/sonari/protocol.py src/sonari/daemon/features/control.py src/sonari/cli/control.py src/sonari/cli/__init__.py commands/keepalive.md README.md tests/test_keepalive_toggle.py
git commit -m "feat(keepalive): live on/off toggle — SET_KEEPALIVE, CLI verb, slash command"
```

---

### Task 5: Observability — STATUS field + doctor row

**Files:**
- Modify: `src/sonari/daemon/features/control.py` (`on_status`), `src/sonari/cli/doctor.py`
- Test: `tests/test_keepalive_doctor.py`

**Interfaces:**
- Consumes: `keepalive.status()` (Task 2), the STATUS handler's reply dict, doctor's `(name, ok, detail)` row convention.
- Produces: STATUS reply key `"keepalive"`; doctor row named `"keepalive"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_keepalive_doctor.py
"""STATUS carries keepalive state; doctor renders it as a row that only fails
on 'degraded' (idle/hold/disabled are all healthy-by-policy)."""
from sonari.protocol import PROTOCOL_VERSION, MsgType
from tests.daemon_helpers import make_daemon
from tests.test_keepalive_manager import FakeProc, FakeTimer


def _msg(t, session="s1", **kw):
    m = {"v": PROTOCOL_VERSION, "type": t, "session": session}
    m.update(kw)
    return m


def test_status_reply_carries_keepalive_state():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon.keepalive._popen = lambda cmd: FakeProc(cmd)
    daemon.keepalive._timer_factory = FakeTimer
    reply = daemon.handle_message(_msg(MsgType.STATUS))
    assert reply["keepalive"] == "idle"
    daemon.handle_message(_msg(MsgType.SESSION_START))
    reply = daemon.handle_message(_msg(MsgType.STATUS))
    assert reply["keepalive"] == "running"


def test_doctor_row_ok_for_policy_states_fail_for_degraded():
    from sonari.cli.doctor import _keepalive_row
    assert _keepalive_row({"keepalive": "running"}) == ("keepalive", True, "running")
    assert _keepalive_row({"keepalive": "idle"}) == ("keepalive", True, "idle")
    assert _keepalive_row({"keepalive": "disabled"}) == ("keepalive", True, "disabled")
    name, ok, detail = _keepalive_row({"keepalive": "degraded"})
    assert (name, ok) == ("keepalive", False)
    assert "degraded" in detail
    name, ok, detail = _keepalive_row({})
    assert (name, ok) == ("keepalive", False)     # old daemon / no field = surface it
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q tests/test_keepalive_doctor.py`
Expected: FAIL — `KeyError: 'keepalive'` / `ImportError: _keepalive_row`.

- [ ] **Step 3: Implement**

- `on_status` (daemon/features/control.py): add `"keepalive": ctx.host.keepalive.status(),` to the reply dict it builds.
- `doctor.py`: add a module-level helper plus one row in `doctor()`'s STATUS-based section (right after the existing rows that read the `st` reply, inside the same daemon-reachable branch):

```python
def _keepalive_row(st):
    state = st.get("keepalive")
    if state in ("running", "idle", "hold", "disabled"):
        return ("keepalive", True, state)
    if state == "degraded":
        return ("keepalive", False,
                "degraded: silent-stream spawns kept dying; Bluetooth clipping is back")
    return ("keepalive", False, "daemon reported no keepalive state")
```

and in `doctor()`:

```python
try:
    results.append(_keepalive_row(st))
except Exception as exc:  # noqa: BLE001
    results.append(("keepalive", False, f"error: {exc}"))
```

Check whether any existing test pins the doctor row COUNT (`grep -rn "doctor" tests/ | grep -i -E "len\(|count|rows"`) and update the pin if so. Also check how the existing STATUS-based rows behave when the daemon is unreachable (`st` is `{}` from the `or {}` guard) — the new row must not crash there; `_keepalive_row({})` covers it.

- [ ] **Step 4: Run tests to verify they pass, then the full suite**

Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q tests/test_keepalive_doctor.py` → PASS (2)
Run: `SAC=$(mktemp -d "$TMPDIR/sac.XXXX") && HOME="$SAC" pytest -q` → 1559 passed, 1 skipped

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/features/control.py src/sonari/cli/doctor.py tests/test_keepalive_doctor.py
git commit -m "feat(keepalive): STATUS field + doctor row"
```

---

## After the tasks (controller work, not a dispatchable task)

1. Whole-branch dual review — **two seats, fable AND opus** (the 2026-08-16 lesson: single-seat was a coin flip on exactly this daemon's concurrency). Review focus: lock ordering (daemon lock → manager lock, never reversed), timer identity races, spawn-storm exclusion, the pending-roster policy test, and whether any existing suite test can now leak a REAL afplay spawn (the default seam is live `subprocess.Popen` — hermeticity: prove no test path reaches `set_active(True)` with the default seam and a live session).
2. Fix wave for findings, each fix independently re-reviewed.
3. Full suite + guards green; park on `build/bt-keepalive`, NOT merged/pushed/installed — owner's gate. Update `.claude/HANDOFF.md` + memory topic file with the parked state and the live-verification protocol (owner installs → one readout → `log show` suspend count should be 0 between items).

## Self-Review (performed at authoring)

- Spec coverage: policy (T3), overlap/no-gap (T2), degraded/anti-spin (T2), config + live toggle + docs regen (T4), doctor/STATUS (T5), WAV + conftest repoint (T1), shutdown teardown (T3), version files untouched (global constraint). Trailing-hold ratified value (600s) is a T2 class constant. ✔
- Placeholder scan: every step carries real code or an exact copy-source pointer (`on_set_minqueue`, `commands/minqueue.md`, learn-timer). Two deliberate "read the neighbor and mirror" instructions remain (CLI send helper shape, conftest entry style) — those are copy-exact-from-named-source instructions, not gaps. ✔
- Type consistency: `keepalive.status()` strings (`running|hold|idle|degraded|disabled`) match T5's row logic and T3's assertions; `set_active/set_enabled/tick/stop` signatures consistent across T2→T5; `KEEPALIVE_WAV_PATH`/`ensure_silence_wav` consistent T1→T2. FakeProc/FakeTimer imported from T2's test module in T3/T4/T5 tests (cross-test-module import is the repo's existing pattern via `tests.daemon_helpers`). ✔
- Known risk flagged for implementers: expected suite counts between tasks (1536/1547/... — corrected 2026-08-24: Task 2 defines 11 tests, prose said 12) assume no collisions with existing tests; treat drift as investigate-first, and the exact numbers as expectations, not gates to force.

## Post-review amendment to Task 2 (2026-08-24, fix round 1 — controller ruling)

Task 2's review found the brief's own pseudocode carried a hole: `set_active(False)`
arms the hold only "if players exist" while the overlap callback is ungated on
`_want` — so a player that dies on its own in the pre-respawn window leaves an
armed overlap timer that resurrects a player chain forever on an idle manager.
Binding corrections (Tasks 3–5 build on THIS contract):
- `_overlap_due` gates: after the identity check and timer-null, `if not
  self._want and self._hold_timer is None: return` — the chain continues only
  while wanted or while a hold is in flight (mid-hold crash-resurrection is
  DESIGNED behavior: the hold's purpose is keeping the device open for a
  returning user).
- `status()` hold arm is `self._players or self._hold_timer is not None` — an
  armed hold with a momentarily-empty player list reads "hold", not "idle".
- Timer-identity guards (hold + overlap) carry direct tests that invoke a stale
  timer's callback (`timer.fn()`, bypassing FakeTimer.fire's cancelled guard),
  plus asserts that both arms set `timer.daemon = True`.
- Wiring rule for Task 3 (review Important 2): lifecycle handlers run under the
  daemon lock and must call ONLY `set_active(...)` (which never reaps); `tick()`
  runs solely from the lock-free speak-loop site and manager timer threads, so a
  wedged afplay's bounded reap can never stall the daemon lock.
