from __future__ import annotations

import subprocess
import threading

_DEFAULT_WAIT_TIMEOUT = 120  # seconds; generous upper bound for even long TTS

# Failure/expiry earcon kinds added AFTER GA: bootstrap merges platform defaults
# only when the whole `earcons` config key is absent (bootstrap.py:73-74), so on
# an EXISTING install a new config-dict kind would be SILENTLY disabled — the
# worst eyes-free failure. These kinds therefore resolve config-first, then fall
# back to the built-in asset (the pitch-asset precedent). A config entry
# always wins, so the owner swaps assets without a code change. Old kinds keep
# today's silent-no-op semantics when unconfigured.
_FALLBACK_EARCONS = {
    "error_misdirected": "/System/Library/Sounds/Basso.aiff",
    "error_system": "/System/Library/Sounds/Blow.aiff",
    "permission_expired": "/System/Library/Sounds/Purr.aiff",
    # D2 §6.6 crossing prelude — resolved via host._asset_path (config-first),
    # never through transient(). PROVISIONAL asset (ear-batch-2).
    "crossing": "/System/Library/Sounds/Frog.aiff",
    "alarm_daemon_down": "/System/Library/Sounds/Hero.aiff",
    "alarm_hotkeys_down": "/System/Library/Sounds/Glass.aiff",
}


class Speaker:
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
        self._earcon_player = earcon_player
        self._earcons = dict(earcons) if earcons else {}
        self._current = None
        self._current_lock = threading.Lock()
        self._cancel_epoch = 0          # bumped by cancel(); closes the synth-gap race
        self._transient_proc = None     # the one-slot arbiter's current tone
        self._terminated_procs: list = []   # superseded tones awaiting a reap
        self._transient_lock = threading.Lock()
        self._wait_timeout = _wait_timeout

    def cancel_epoch(self) -> int:
        """The current cancel epoch. The daemon captures this at CLAIM time (under
        its own lock) and passes it to speak(), so a cancel landing in the gap
        between claim and speak() is detected (M2)."""
        with self._current_lock:
            return self._cancel_epoch

    def speak(self, text=None, audio_path=None, cancel_epoch=None, voice=None) -> bool:
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
        # Establish the baseline epoch BEFORE synthesis/spawn. say_runner (TTS
        # synthesis) can take tens-hundreds of ms, during which there is no proc to
        # cancel — a cancel() arriving in that window used to be a silent no-op and
        # the utterance played anyway. If the epoch advanced past the baseline while
        # we synthesized, a cancel landed: honor it by terminating immediately and
        # reporting the utterance as NOT completed (so the caller replays it).
        with self._current_lock:
            epoch = self._cancel_epoch if cancel_epoch is None else cancel_epoch
        say_voice = voice if voice is not None else self._voice
        proc = (runner(audio_path) if audio_path is not None
                else runner(text, say_voice, self._rate))
        if proc is None:
            return False                # afplay could not spawn / the file vanished
        with self._current_lock:
            interrupted = self._cancel_epoch != epoch
            if not interrupted:
                self._current = proc
        if interrupted:
            proc.terminate()
            return False
        try:
            try:
                proc.wait(timeout=self._wait_timeout)
            except subprocess.TimeoutExpired:
                # 'say' hung past the generous deadline; kill it and move on.
                proc.terminate()
        finally:
            with self._current_lock:
                if self._current is proc:
                    self._current = None
        return getattr(proc, "returncode", None) == 0

    def cancel(self) -> None:
        with self._current_lock:
            self._cancel_epoch += 1     # so a speak() mid-synthesis aborts on return
            proc = self._current
        if proc is not None:
            proc.terminate()

    def transient(self, kind: str) -> None:
        """One-slot transient arbiter (D8 law 3): short non-verbal tones bypass
        the queue; a new transient TERMINATES a still-playing one (latest-wins,
        no stacking). Transients may coexist with speech, never with each other.
        Called from handler threads (under the daemon lock) AND the speak thread
        (_signal_speak_failure) — hence its own lock, never the daemon's. Asset
        resolution: the config dict first, then _FALLBACK_EARCONS; an unconfigured
        legacy kind stays a silent no-op."""
        if self._earcon_player is None:
            return
        path = self._earcons.get(kind)
        if path is None:
            path = _FALLBACK_EARCONS.get(kind)   # never-silent NEW kinds only
        if path is None:
            return
        with self._transient_lock:
            # Deterministic reap: purge previously-terminated tones that have
            # exited before spawning a new one (the old earcon reap's guarantee —
            # dropping the reference to a terminated proc leaves reaping to
            # non-deterministic CPython GC).
            self._reap_terminated_procs()
            prev = self._transient_proc
            if prev is not None and prev.poll() is None:
                prev.terminate()
                self._terminated_procs.append(prev)   # retain until the next reap
            proc = self._earcon_player(path)
            self._transient_proc = (proc if proc is not None
                                    and hasattr(proc, "poll") else None)

    def _reap_terminated_procs(self) -> None:
        """Non-blocking poll: drop superseded tones whose process has finished.
        Called under _transient_lock."""
        self._terminated_procs = [p for p in self._terminated_procs
                                  if p.poll() is None]

    def pitch_asset(self, direction: str) -> "str | None":
        """Path of the packaged pitch chirp (up = yes/next, down = no/prev), or
        None for an unknown direction. Package-direct (never the configurable
        earcons dict) so an existing user's `earcons` config can never silently
        disable it (bootstrap merges with a whole-key guard)."""
        if direction not in ("up", "down"):
            return None
        from pathlib import Path
        return str(Path(__file__).resolve().parent
                   / "assets" / "pitch_{0}.wav".format(direction))

    def set_voice(self, v) -> None:
        self._voice = v

    def set_rate(self, r) -> None:
        self._rate = r
