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

    # ---- public API -----------------------------------------------------

    def set_enabled(self, on: bool) -> None:
        """Config knob. Disabling stops everything and pins status "disabled";
        re-enabling only flips the flag — the next set_active/tick re-evaluates."""
        doomed = ()
        with self._lock:
            self._enabled = bool(on)
            if not self._enabled:
                self._cancel_hold_locked()
                doomed = self._detach_players_locked()
        self._reap(doomed)

    def set_active(self, active: bool) -> None:
        """Policy verdict from the daemon: True == at least one live session."""
        with self._lock:
            if active:
                self._cancel_hold_locked()
                if not self._want:
                    # False->True EDGE only: a fresh activation forgives a
                    # previous give-up. Re-asserting an already-true want must
                    # not, or a per-tick set_active(True) would erase the bound.
                    self._fast_deaths = 0
                    self._degraded = False
                self._want = True
                # Ensure semantics — deliberately NO early return on _want being
                # already true: a set_enabled(False)->(True) cycle leaves _want
                # true with an empty player list, and only re-ensuring here
                # restores the stream. Idempotence falls out of the liveness
                # check instead: a player that is still running blocks the spawn.
                if self._enabled and not self._degraded and not self._live_locked():
                    self._spawn_locked()
            else:
                if not self._want:
                    return
                self._want = False
                if self._players:
                    # Trailing hold: keep the device open for HOLD_S in case the
                    # user comes back. With no players there is nothing to hold.
                    self._arm_hold_locked()

    def tick(self) -> None:
        """Reap dead players, respawn after backoff, bound consecutive failures."""
        doomed = ()
        with self._lock:
            now = self._clock()
            self._observe_deaths_locked(now)
            if self._fast_deaths >= self.GIVEUP_N:
                # Anti-spin-storm: GIVEUP_N consecutive fast deaths means the
                # spawn itself is broken (no afplay, no output device, ...).
                # Stop until the next set_active(False)->(True) edge.
                self._degraded = True
                doomed = self._detach_players_locked()   # defensive: none expected
            elif (self._want and self._enabled and not self._degraded
                    and not self._players
                    and now - (self._last_death or 0.0) >= self.BACKOFF_S):
                self._spawn_locked()
        self._reap(doomed)

    def stop(self) -> None:
        """Shutdown: cancel timers, terminate players, bounded reap."""
        with self._lock:
            self._cancel_hold_locked()
            doomed = self._detach_players_locked()
            self._want = False
        self._reap(doomed)

    def status(self) -> str:
        with self._lock:
            if not self._enabled:
                return "disabled"
            if self._degraded:
                return "degraded"
            if self._want:
                # "running" even in a momentary backoff gap with no player: the
                # manager is actively trying, which is what an operator asks about.
                return "running"
            if self._players or self._hold_timer is not None:
                # An armed hold with a momentarily empty list (a player crashed and
                # the chain has not replaced it yet) is still a hold, not idle.
                return "hold"
            return "idle"

    # ---- players --------------------------------------------------------

    def _live_locked(self) -> bool:
        return any(proc.poll() is None for proc, _ in self._players)

    def _spawn_locked(self) -> None:
        try:
            # ensure_silence_wav() stages through a FIXED .part path, so two
            # concurrent calls would clobber each other's staging file. Every
            # spawn in this class goes through here, under self._lock — that is
            # what serializes them (timer callbacks included).
            proc = self._popen(["afplay", ensure_silence_wav()])
        except Exception:
            # A player that cannot even start is an immediate give-up: retrying a
            # missing afplay on every tick is exactly the storm this class bounds.
            self._degraded = True
            return
        self._players.append((proc, self._clock()))
        self._arm_overlap_locked()

    def _observe_deaths_locked(self, now: float) -> None:
        """Prune players that have EXITED, scoring each death fast or slow.

        Fast/slow is measured from ``now - spawned_at`` at OBSERVATION time.
        Deliberate terminations can never be scored here: _detach_players_locked()
        empties the list under the lock before the reap, so nothing a shutdown or a
        hold expiry killed is left for a later tick to observe.
        """
        alive = []
        for proc, spawned_at in self._players:
            if proc.poll() is None:
                alive.append((proc, spawned_at))
                continue
            self._last_death = now
            if now - spawned_at < self.FAST_DEATH_S:
                self._fast_deaths += 1
            else:
                self._fast_deaths = 0     # one healthy run clears the streak
        self._players = alive

    def _prune_exited_locked(self) -> None:
        """Drop exited players without scoring them. Never terminate() one that
        is still running — the overlap window exists so A and B stream together."""
        self._players = [(p, t) for (p, t) in self._players if p.poll() is None]

    def _detach_players_locked(self) -> list:
        """Cancel the chain and hand the players to the caller to reap UNLOCKED.

        THE INVARIANT: self._lock is NEVER held across a child wait(). The reap is
        terminate() + up to 2s of wait() per player, and every other entry point
        queues on this lock — including set_active(), which the daemon's lifecycle
        handlers call while holding the DAEMON lock. Reaping under the manager lock
        therefore leaks a ~2s-per-player stall into the daemon's transaction lock
        (every socket message, hotkey and speak-loop claim), which is exactly what
        the Task-2 wiring rule exists to prevent. So: decide here, kill outside.

        The player list is emptied HERE, under the lock, so status()/_live_locked()
        see the truth immediately and a concurrent set_active(True) re-spawns rather
        than adopting a doomed child.

        The overlap chain dies with the players it was chaining. Without this cancel,
        a hold expiry (or a give-up) would leave an armed overlap timer that spawns a
        fresh player minutes later — its callback intentionally ignores _want,
        because the hold has to outlast one file.
        """
        if self._overlap_timer is not None:
            self._overlap_timer.cancel()
            self._overlap_timer = None
        doomed = [proc for proc, _ in self._players]
        self._players = []
        return doomed

    @staticmethod
    def _reap(players) -> None:
        """Terminate + bounded wait. MUST run with self._lock released (see
        _detach_players_locked). Bounded so a wedged afplay cannot hang shutdown —
        mirrors _AfplayHandle.terminate in platform/macos/tts.py."""
        for proc in players:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass

    # ---- timers ---------------------------------------------------------
    # Both timers use host.py's _arm_learn_timer identity discipline: the
    # callback closes over its OWN timer object and, under self._lock, bails
    # unless it is still the live one. A stale timer that fired just past its
    # cancel window can then neither act on nor orphan a re-armed successor.

    def _arm_overlap_locked(self) -> None:
        if self._overlap_timer is not None:
            self._overlap_timer.cancel()
        # Overlap, never gap: the successor starts OVERLAP_S before this file
        # ends. A sequential respawn leaks a teardown at every file boundary,
        # and the daemon's tick cadence is far too sparse to hit that window.
        timer = self._timer_factory(SILENCE_S - self.OVERLAP_S,
                                    lambda: self._overlap_due(timer))
        timer.daemon = True
        self._overlap_timer = timer
        timer.start()

    def _overlap_due(self, timer) -> None:
        with self._lock:
            if self._overlap_timer is not timer:
                return
            # We are the live timer and we have fired: drop the reference so a
            # failed spawn below cannot leave a dead timer recorded as live.
            self._overlap_timer = None
            if not self._enabled or self._degraded:
                return
            # Chain only while the stream is WANTED, or while a hold is in flight.
            # Not gated on _want alone: HOLD_S outlasts a single file, so the hold
            # would break mid-chain. But not ungated either — a player that crashed
            # on its own is pruned by tick(), so the next set_active(False) arms no
            # hold and nothing cancels this timer; without this line the callback
            # would spawn a player AND re-arm itself on an idle manager, forever.
            if not self._want and self._hold_timer is None:
                return
            self._spawn_locked()
            self._prune_exited_locked()

    def _arm_hold_locked(self) -> None:
        self._cancel_hold_locked()
        timer = self._timer_factory(self.HOLD_S, lambda: self._hold_expired(timer))
        timer.daemon = True
        self._hold_timer = timer
        timer.start()

    def _cancel_hold_locked(self) -> None:
        if self._hold_timer is not None:
            self._hold_timer.cancel()
            self._hold_timer = None

    def _hold_expired(self, timer) -> None:
        doomed = ()
        with self._lock:
            # Still ours, and still unwanted: a re-activation that cancelled us a
            # beat too late must not tear down the stream it just kept.
            if self._hold_timer is not timer or self._want:
                return
            self._hold_timer = None
            doomed = self._detach_players_locked()
        # Outside the lock (see _detach_players_locked): this runs on a Timer
        # thread, and a wedged child here would otherwise block the daemon's next
        # lifecycle handler for ~2s per player.
        self._reap(doomed)
