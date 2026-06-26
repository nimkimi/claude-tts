"""Stage 6 — Speaker cancel verification (symptom 2b).

Symptom 2b (spec §1.1): interrupt "didn't cut cleanly" / lag / resumed wrong.
Root-caused to the **Speaker cancel-epoch / synth-gap mechanism** — and, per §1.1,
NOT reproducible with `FakeSpeaker`, which is instant. The existing tests in
`test_speaker.py` carry the right NAMES but drive cancel() synchronously inside an
instant fake, so they never exercise a genuine concurrent window.

These probes use the **real `Speaker`** with an Event-gated slow fake `say_runner`,
firing `cancel()` while `speak()` is truly blocked — in synthesis (no proc to kill
yet) or in playback (proc tracked). They map the symptom's three facets to the
mechanism's real timing windows:

    speak() window              facet of 2b            probe
    -------------------------   --------------------   ---------------------------
    W2  during synth            "lag" (utterance       test_synth_gap_*           (concurrent)
        (before _current set)    played after cancel)
    claim->speak gap            external-epoch (M2)    test_claim_to_speak_gap_*  (sequential)
    W3  during proc.wait()      "didn't cut cleanly"   test_cancel_during_playback_*  (concurrent)
    W4  cancel == completion    completed not unmade   test_cancel_coinciding_*   (in-wait hook)
    none (clean)                baseline sanity        test_clean_completion_*    (concurrent)
    daemon requeue (PAUSE)      "resumed wrong" /      test_real_pause_requeues_* (concurrent)
        real speak() vs cancel   double-play

Window coverage notes:
- W1 (cancel after the baseline read, before synth starts) is code-path-identical to W2:
  speak() has a SINGLE interrupted-check (one `_cancel_epoch != baseline` under the lock),
  so the pre-synth vs in-synth distinction is invisible to the mechanism — W2's probe
  covers it. No separate W1 probe is possible or needed.
- Probe 3 is SEQUENTIAL by design (cancel fired pre-speak() on the test thread): it
  validates the captured-baseline comparison, the distinguishing case a concurrent
  synth-gap cancel cannot reach. Probes 2/4/5 are the genuinely concurrent interleavings.
- The single speak thread means the ONLY real concurrency is speak() vs cancel();
  speak-vs-speak is unreachable by construction and is not (cannot be) probed.

Scope of the "2b solid" conclusion these probes support:
- The cancel-epoch mechanism is the path-agnostic 2b root cause; all 9 production
  cancel() sites invoke the identical epoch-bump + terminate, locked by probes 2-4/4b.
- "resumed wrong" / double-play: the speak loop's stopped-branch re-queue is the sole
  disposition that replays an interrupted item — probe 5 verifies it end-to-end against
  the real Speaker. Both STOP_SESSION and STOP_ALL set `stopped` and replay via the
  stopped-branch. The other 7 dispositions (FLUSH/STOP/SKIP/JUMP/JUMP_DECISION/NAV/
  NAV_RESPONSE) drop or mark-heard and are covered at the daemon level by the existing
  FakeSpeaker tests.
- OUT of 2b scope (and intentionally excluded): speak()'s OWN fallback wait-timeout
  terminate (the say-hung safety net) — a non-cancel path; probes 4/4b/5 assert
  `wait_timed_out is False` precisely to prove the CANCEL, not the timeout, did the work.
  That path is covered by test_speaker.py::test_speak_wait_timeout_terminates_hung_proc.
- The pause-requeue's `_last_spoken_session` attribution rollback is covered by
  test_daemon_streams.py::test_attribution_survives_pause_on_switch (not re-tested here).

Determinism: no sleeps. Bidirectional Events synchronize the worker and the test, and
EVERY blocking wait — fake wait()/synth gate, test Event waits, thread join — is bounded
(TIMEOUT for test patience, _HANG_CEILING for a wedged proc.wait) and asserted to have
completed, so a deadlocked probe fails in seconds instead of wedging the suite or leaking
the speak thread (workers are daemon threads).
"""
from __future__ import annotations

import subprocess
import threading

from sonari.speaker import Speaker

# Test patience: every Event wait and thread join is bounded by this, so a
# deadlocked probe fails in ~TIMEOUT seconds instead of wedging the suite.
TIMEOUT = 2.0
# A wedged proc.wait() can hang no longer than this. Kept well ABOVE TIMEOUT so it
# never masks the cancel-vs-timeout discrimination: when cancel works the proc is
# terminated immediately (wait returns at once); when it's broken the proc.wait sits
# here until the bounded join() has already failed the probe.
_HANG_CEILING = 10.0


class GatedProc:
    """Event-gated stand-in for subprocess.Popen.

    wait() blocks until terminate() (cancel) or finish() (clean completion), so the
    test controls exactly when "playback" ends. returncode mimics `say`: 0 on a clean
    exit, -15 (SIGTERM) on a kill — matching what speak() inspects to decide completed.
    """

    def __init__(self) -> None:
        self.returncode = None
        self.terminate_calls = 0
        self.wait_calls = 0
        self.wait_timed_out = False           # True iff speak()'s OWN fallback timeout
                                              # ended playback (NOT a cancel) — lets a
                                              # cancel probe prove the cancel did the work
        self._playing = threading.Event()   # set once wait() begins blocking
        self._ended = threading.Event()      # set by terminate() or finish()

    def wait(self, timeout=None):
        self.wait_calls += 1
        self._playing.set()
        cap = _HANG_CEILING if timeout is None else min(timeout, _HANG_CEILING)
        if not self._ended.wait(cap):
            self.wait_timed_out = True
            raise subprocess.TimeoutExpired(cmd=["say"], timeout=timeout)
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        # Popen-faithful: terminate() on an already-exited process is a no-op and leaves
        # the original returncode intact. Only a still-live proc gets the SIGTERM code.
        # (Unconditionally stamping -15 would manufacture a phantom "cancel unmade a
        # completed utterance" in the W4 window — see test_cancel_coinciding_with_*.)
        if self.returncode is None:
            self.returncode = -15
        self._ended.set()

    def poll(self):
        return self.returncode

    def finish(self, rc: int = 0) -> None:
        """Test-driven clean completion of playback (no cancel)."""
        self.returncode = rc
        self._ended.set()

    def wait_until_playing(self) -> None:
        assert self._playing.wait(TIMEOUT), "playback never reached proc.wait()"


class GatedRunner:
    """Fake say_runner. With gate_synth=True it blocks INSIDE synthesis (before any
    proc exists) until released, opening the W2 synth-gap window for a concurrent
    cancel. With gate_synth=False synthesis returns at once and the window of interest
    is W3 (proc.wait)."""

    def __init__(self, gate_synth: bool = False) -> None:
        self.calls: list = []
        self.procs: list[GatedProc] = []
        self._gate_synth = gate_synth
        self._synthesizing = threading.Event()   # set when synth begins blocking
        self._synth_release = threading.Event()   # test lets synth finish
        self._cond = threading.Condition()         # guards procs for multi-iteration waits

    def __call__(self, text, voice, rate) -> GatedProc:
        with self._cond:
            self.calls.append((text, voice, rate))
        if self._gate_synth:
            self._synthesizing.set()
            assert self._synth_release.wait(TIMEOUT), "synth release never signaled"
        proc = GatedProc()
        with self._cond:
            self.procs.append(proc)
            self._cond.notify_all()
        return proc

    def wait_until_synthesizing(self) -> None:
        assert self._synthesizing.wait(TIMEOUT), "synthesis never started"

    def release_synth(self) -> None:
        self._synth_release.set()

    def wait_until_proc_count(self, n: int) -> None:
        with self._cond:
            ok = self._cond.wait_for(lambda: len(self.procs) >= n, TIMEOUT)
        assert ok, f"expected >= {n} procs, got {len(self.procs)}"


def _run(fn):
    """Run fn() on a worker thread, capturing return value and any exception.
    Returns (thread, result_dict) where result_dict has 'value' or 'error' once joined.
    """
    out: dict = {}

    def target():
        try:
            out["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - surface worker crashes to the test
            out["error"] = e

    # daemon=True so a probe that wedges its worker never blocks interpreter exit;
    # the bounded join() below has already failed the probe by then.
    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, out


def _join(t) -> None:
    t.join(TIMEOUT)
    assert not t.is_alive(), "worker thread did not finish — probe deadlocked"


# ---------------------------------------------------------------------------
# Probe 1 — clean completion (baseline; no cancel)
# ---------------------------------------------------------------------------

def test_clean_completion_returns_true_and_clears_current():
    runner = GatedRunner(gate_synth=False)
    sp = Speaker(say_runner=runner)   # default wait timeout; GatedProc bounds the wedge

    t, out = _run(lambda: sp.speak("done cleanly"))
    runner.wait_until_proc_count(1)
    proc = runner.procs[0]
    proc.wait_until_playing()
    assert sp._current is proc            # tracked as current while playing
    proc.finish(0)                         # clean exit, no cancel
    _join(t)

    assert out.get("value") is True
    assert proc.terminate_calls == 0
    assert sp._current is None             # finally-block released the claim


# ---------------------------------------------------------------------------
# Probe 2 — synth-gap: concurrent cancel WHILE synthesizing (W2), no proc yet
# ---------------------------------------------------------------------------

def test_synth_gap_concurrent_cancel_aborts_before_play():
    runner = GatedRunner(gate_synth=True)
    sp = Speaker(say_runner=runner)   # default wait timeout; GatedProc bounds the wedge

    t, out = _run(lambda: sp.speak("hello"))
    runner.wait_until_synthesizing()       # speak is blocked in synth; _current is None
    assert sp._current is None
    sp.cancel()                             # cancel lands in the synth gap (from this thread)
    runner.release_synth()                   # let synth finish; speak re-checks the epoch
    _join(t)

    assert out.get("value") is False        # interrupted -> not completed -> replayable
    assert runner.procs[0].terminate_calls == 1   # the just-made proc was terminated
    assert runner.procs[0].wait_calls == 0        # never played
    assert sp._current is None


# ---------------------------------------------------------------------------
# Probe 3 — claim->speak gap (M2): external baseline epoch, concurrent cancel
# ---------------------------------------------------------------------------

def test_claim_to_speak_gap_honors_external_epoch():
    # The daemon captures cancel_epoch() at CLAIM time (under its lock), releases the
    # lock, then calls speak(cancel_epoch=...). A cancel landing in THAT gap bumps the
    # live epoch BEFORE speak() ever reads it — so speak() must compare against the
    # captured baseline, not the already-bumped live value. The distinguishing case the
    # synth-gap probe can't reach: fire the cancel before speak() runs. (The worker +
    # bounded join is what turns a regression into a fast failure instead of a hang: if
    # the baseline were ignored, speak() would fall through to a real proc.wait().)
    runner = GatedRunner(gate_synth=False)
    sp = Speaker(say_runner=runner)   # default wait timeout; GatedProc bounds the wedge
    epoch0 = sp.cancel_epoch()             # captured at claim (== 0)
    sp.cancel()                             # lands in the claim->speak gap (live epoch -> 1)

    t, out = _run(lambda: sp.speak("hi", cancel_epoch=epoch0))
    _join(t)

    assert out.get("value") is False        # stale baseline (0) != live (1) -> interrupted
    assert len(runner.procs) == 1
    assert runner.procs[0].terminate_calls == 1
    assert runner.procs[0].wait_calls == 0  # aborted before playback
    assert sp._current is None


# ---------------------------------------------------------------------------
# Probe 4 — cancel during playback (W3): proc tracked as _current, then killed
# ---------------------------------------------------------------------------

def test_cancel_during_playback_terminates_current_and_returns_false():
    runner = GatedRunner(gate_synth=False)
    sp = Speaker(say_runner=runner)   # default wait timeout; GatedProc bounds the wedge

    t, out = _run(lambda: sp.speak("playing"))
    runner.wait_until_proc_count(1)
    proc = runner.procs[0]
    proc.wait_until_playing()               # speak is blocked in proc.wait()
    assert sp._current is proc
    sp.cancel()                             # bumps epoch AND terminates the current proc
    _join(t)

    assert out.get("value") is False
    assert proc.terminate_calls == 1
    assert proc.returncode == -15           # killed, not a clean exit
    assert proc.wait_timed_out is False     # ended by the CANCEL, not speak()'s fallback
    assert sp._current is None


# ---------------------------------------------------------------------------
# Probe 4b — W4: a cancel coinciding with completion must NOT unmake it
# ---------------------------------------------------------------------------

def test_cancel_coinciding_with_completion_does_not_unmake_it():
    """W4: a cancel() arriving at the instant the utterance finishes — after proc.wait()
    returns its clean exit but while _current still points at the just-finished proc —
    must NOT turn a COMPLETED utterance into a cancelled one. A real Popen.terminate() on
    an exited process is a no-op, so the returncode stays 0 and speak() returns True. If
    it returned False the daemon would replay an utterance the user already fully heard —
    a double-play. Driven deterministically from inside wait() (the only way to land in
    this gap, which has no Event seam in speak())."""
    sp_box: dict = {}

    class FinishThenCancelProc:
        def __init__(self) -> None:
            self.returncode = None
            self.terminate_calls = 0
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            self.returncode = 0              # the utterance finished playing cleanly...
            sp_box["sp"].cancel()            # ...and a cancel lands at that exact instant
            return self.returncode

        def terminate(self) -> None:
            self.terminate_calls += 1
            if self.returncode is None:      # Popen-faithful no-op on an exited proc
                self.returncode = -15

        def poll(self):
            return self.returncode

    made: list = []

    def runner(text, voice, rate):
        proc = FinishThenCancelProc()
        made.append(proc)
        return proc

    sp = Speaker(say_runner=runner)
    sp_box["sp"] = sp
    completed = sp.speak("finished as the cancel arrived")

    assert completed is True                 # completion stands; NOT replayed
    assert made[0].returncode == 0           # terminate() did not flip a clean exit to -15
    assert made[0].terminate_calls == 1      # cancel did fire (proc was still _current)
    assert sp._current is None               # claim released


# ---------------------------------------------------------------------------
# Probe 5 — daemon requeue with the REAL Speaker: "resumed wrong" / double-play
# ---------------------------------------------------------------------------

def _make_real_daemon(runner, foreground="fg"):
    """A SpeechDaemon driving the REAL Speaker (not FakeSpeaker), so the requeue path
    composes against a genuine cancel()->speak()==False, like production."""
    from sonari.sessions import SessionManager
    from sonari.daemon import SpeechDaemon
    from sonari.config import DEFAULTS

    speaker = Speaker(say_runner=runner)   # default wait timeout; GatedProc bounds the wedge
    sessions = SessionManager()
    sessions.set_foreground(foreground)
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    daemon = SpeechDaemon(speaker, sessions, config)
    return daemon, speaker


def test_real_stop_requeues_once_and_replays_without_double_play():
    runner = GatedRunner(gate_synth=False)
    daemon, speaker = _make_real_daemon(runner)
    entry = daemon.history.record("fg", "prose", "interrupted sentence")
    daemon._enqueue("fg", "prose", "interrupted sentence", False, entry=entry)
    # A FOLLOWER already waits behind it: the requeued item must land AHEAD of this on
    # resume ("resumed wrong" guard). Without it the deque holds one item and
    # enqueue_front vs enqueue are indistinguishable — the ordering check would be vacuous.
    daemon._enqueue("fg", "prose", "second sentence", False)
    q = daemon._stream("fg").queue
    assert len(q) == 2

    # --- a stop lands mid-utterance while the real speak() is in playback ---
    t, out = _run(daemon._speak_loop_once)   # pops + plays "interrupted sentence"
    runner.wait_until_proc_count(1)
    proc = runner.procs[0]
    proc.wait_until_playing()               # daemon blocked in REAL speak() playback
    # Set stopped directly (bypassing the handler) so we don't inject the "Stopped."
    # pause-exempt cue into the queue — the queue-count assertions below assume only
    # the original two items exist after re-queue.
    daemon._stream("fg").stopped = True      # stop flag set mid-synthesis...
    speaker.cancel()                         # ...and cancel: real terminate -> speak False
    _join(t)
    assert "error" not in out, out.get("error")

    # Requeued EXACTLY once, at the FRONT — ahead of the waiting follower; claim cleared,
    # heard-marker preserved for the eventual replay.
    assert len(q) == 2
    assert q._items[0].text == "interrupted sentence"   # back at the head, not bumped behind
    assert q._items[1].text == "second sentence"        # follower still strictly behind it
    assert daemon._current_item is None
    assert entry.heard is False
    assert entry in daemon._pending_heard.values()
    assert proc.terminate_calls == 1
    assert proc.wait_timed_out is False      # the cancel cut it, not speak()'s fallback
    assert len(runner.calls) == 1            # synthesized once so far — no double-synth

    # --- resume: clear stopped flag, let the replay complete cleanly ---
    daemon._stream("fg").stopped = False
    t2, out2 = _run(daemon._speak_loop_once)
    runner.wait_until_proc_count(2)
    proc2 = runner.procs[1]
    proc2.wait_until_playing()
    proc2.finish(0)
    _join(t2)
    assert "error" not in out2, out2.get("error")

    assert runner.calls[1][0] == "interrupted sentence"  # replayed the SAME text, uncorrupted
    assert entry.heard is True               # recorded heard exactly once, on the replay
    assert len(q) == 1                        # only the follower remains — no duplication
    assert q._items[0].text == "second sentence"
    assert len(runner.calls) == 2            # interrupted once + replayed once
