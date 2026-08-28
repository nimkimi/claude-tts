from __future__ import annotations

import subprocess
import threading

_DEFAULT_WAIT_TIMEOUT = 120  # seconds; generous upper bound for even long TTS


class SpeakFailure(Exception):
    """Raised by Speaker.speak() when an utterance did not play for a reason
    that is NOT a cancel/barge-in: no runner configured, a runner that failed
    to spawn a process, or a process that exited nonzero entirely on its own
    (the AudioQueueStart(-66681) shape — `say`/`afplay` print an error and
    exit nonzero without anyone ever calling cancel()). Distinguished from a
    cancel-shaped outcome (still reported via a plain `False` return, never
    raised) so callers such as the daemon speak loop's existing
    `except Exception -> _signal_speak_failure` can tell a broken audio path
    from an ordinary interrupt (I3: a broken audio path used to produce total,
    untraceable silence)."""


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
        # D8: ONE voice. _current_lock guards only the handle and the epoch —
        # proc.wait(), i.e. the whole duration of the utterance, ran outside any
        # lock, so two threads could have `say` playing at once. That is not
        # hypothetical: on a daemon restart bootstrap._start_boot_cue() speaks
        # W8 on its own thread while daemon.run()'s speak loop is already
        # draining the queue, and the two talked over each other. Transient
        # earcons keep their own lock and are deliberately NOT serialised here —
        # the tone is meant to be instant, only the words queue.
        self._play_lock = threading.Lock()
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
        (a spearcon); otherwise say *text*. Return True iff it COMPLETED (exit 0);
        return False iff it was cancelled/interrupted (barge-in) so the caller
        leaves it marked unheard (sentence-granular replay). Anything else that
        kept the utterance from playing — no runner configured, the runner failed
        to spawn, or the process exited nonzero entirely on its own (I3: the
        AudioQueueStart(-66681) shape) — RAISES SpeakFailure instead of folding
        into that same False, so a genuinely broken audio path is distinguishable
        from an ordinary interrupt.

        *cancel_epoch* is the baseline to compare against (see cancel_epoch()); a
        cancel arriving between the daemon's claim and this call is detected. The
        afplay proc is tracked as _current exactly like say, so cancel() interrupts
        it identically (barge-in parity)."""
        if audio_path is not None:
            runner = self._afplay_runner
        else:
            runner = self._say_runner
        if runner is None:
            raise SpeakFailure("no {0} runner configured".format(
                "afplay" if audio_path is not None else "say"))
        # Establish the baseline epoch BEFORE synthesis/spawn. say_runner (TTS
        # synthesis) can take tens-hundreds of ms, during which there is no proc to
        # cancel — a cancel() arriving in that window used to be a silent no-op and
        # the utterance played anyway. If the epoch advanced past the baseline while
        # we synthesized, a cancel landed: honor it by terminating immediately and
        # reporting the utterance as NOT completed (so the caller replays it).
        # Held for the whole utterance so a second caller waits its turn rather
        # than playing over this one. cancel() takes only _current_lock, so a
        # barge-in still interrupts the utterance in flight and releases this
        # promptly; the waiter then re-reads the epoch below and honours it.
        with self._play_lock:
            with self._current_lock:
                epoch = self._cancel_epoch if cancel_epoch is None else cancel_epoch
            say_voice = voice if voice is not None else self._voice
            proc = (runner(audio_path) if audio_path is not None
                    else runner(text, say_voice, self._rate))
            if proc is None:
                raise SpeakFailure("runner failed to spawn a process")
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
            if getattr(proc, "returncode", None) == 0:
                return True
            # Nonzero exit. This must NOT simply become a raise: a cancel() that
            # lands mid-wait (real barge-in, SIGTERM sets a nonzero returncode)
            # reaches this exact point too — the pre-wait `interrupted` check above
            # only catches a cancel that lands BEFORE proc.wait() is called. cancel()
            # bumps _cancel_epoch (under _current_lock) strictly BEFORE it calls
            # terminate(), so by the time proc.wait() returns, an epoch mismatch here
            # means OUR OWN cancel() is why this proc isn't reporting a clean exit —
            # that is cancel-shaped, not a failure. (The W4 race — a cancel landing
            # at the exact instant of a clean exit — never reaches this branch at
            # all: returncode is already 0 there, handled above.) Anything else, no
            # cancel ever touched this call, is a genuine failure and must be
            # surfaced, not silently folded into an ordinary "not completed".
            with self._current_lock:
                cancelled = self._cancel_epoch != epoch
            if cancelled:
                return False
            raise SpeakFailure("say/afplay exited with code {0!r}".format(
                getattr(proc, "returncode", None)))

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
        resolution: one lookup in the config dict; an unconfigured kind stays a
        silent no-op."""
        if self._earcon_player is None:
            return
        path = self._earcons.get(kind)
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
