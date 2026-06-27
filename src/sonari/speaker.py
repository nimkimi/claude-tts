from __future__ import annotations

import subprocess
import threading

_DEFAULT_WAIT_TIMEOUT = 120  # seconds; generous upper bound for even long TTS


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
        self._earcon_procs: list = []
        self._wait_timeout = _wait_timeout

    def cancel_epoch(self) -> int:
        """The current cancel epoch. The daemon captures this at CLAIM time (under
        its own lock) and passes it to speak(), so a cancel landing in the gap
        between claim and speak() is detected (M2)."""
        with self._current_lock:
            return self._cancel_epoch

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
        # Establish the baseline epoch BEFORE synthesis/spawn. say_runner (TTS
        # synthesis) can take tens-hundreds of ms, during which there is no proc to
        # cancel — a cancel() arriving in that window used to be a silent no-op and
        # the utterance played anyway. If the epoch advanced past the baseline while
        # we synthesized, a cancel landed: honor it by terminating immediately and
        # reporting the utterance as NOT completed (so the caller replays it).
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

    def _reap_earcon_procs(self) -> None:
        """Non-blocking poll: discard entries whose process has finished."""
        self._earcon_procs = [p for p in self._earcon_procs if p.poll() is None]

    def earcon(self, kind: str) -> None:
        if self._earcon_player is None:
            return
        # Reap any finished earcon processes before launching a new one.
        self._reap_earcon_procs()
        path = self._earcons.get(kind)
        if path is None:
            return
        proc = self._earcon_player(path)
        if proc is not None and hasattr(proc, "poll"):
            self._earcon_procs.append(proc)

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

    def set_voice(self, v) -> None:
        self._voice = v

    def set_rate(self, r) -> None:
        self._rate = r
