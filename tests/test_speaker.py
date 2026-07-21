import subprocess

from sonari.speaker import Speaker


class FakePopen:
    """Stand-in for subprocess.Popen exposing wait()/terminate()."""

    returncode = 0  # FakePopen always "exits 0"; mirrors _DoneProc so speak() returns True

    def __init__(self):
        self.wait_calls = 0
        self.terminate_calls = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        return 0

    def terminate(self):
        self.terminate_calls += 1


class RecordingRunner:
    """Records say_runner(text, voice, rate) calls; returns a fresh FakePopen each time."""

    def __init__(self):
        self.calls = []
        self.procs = []

    def __call__(self, text, voice, rate):
        proc = FakePopen()
        self.calls.append((text, voice, rate))
        self.procs.append(proc)
        return proc


def test_speak_calls_say_runner_with_voice_rate_and_blocks_on_wait():
    runner = RecordingRunner()
    sp = Speaker(voice="Ava", rate=180, say_runner=runner)
    sp.speak("hello world")
    assert runner.calls == [("hello world", "Ava", 180)]
    assert runner.procs[0].wait_calls == 1


def test_speak_tracks_current_proc():
    runner = RecordingRunner()
    sp = Speaker(say_runner=runner)
    sp.speak("one")
    sp.speak("two")
    assert len(runner.procs) == 2
    assert runner.procs[0].wait_calls == 1
    assert runner.procs[1].wait_calls == 1


def test_cancel_after_speak_completes_is_noop():
    runner = RecordingRunner()

    # A runner whose returned proc does NOT auto-finish on wait(): we drive
    # cancel() before the (simulated) blocking wait by calling speak in a way
    # that lets us inspect the tracked proc. Here we use a runner that records
    # the proc and lets us cancel after wait returns, then assert terminate.
    sp = Speaker(say_runner=runner)
    sp.speak("blah")
    # After speak returns, current proc is cleared; cancel must be a safe no-op.
    sp.cancel()
    assert runner.procs[0].terminate_calls == 0


def test_cancel_terminates_active_proc_mid_speak():
    # Use a runner whose proc.wait() invokes a hook so we can cancel while the
    # proc is still tracked as current.
    captured = {}

    class CancelOnWaitPopen(FakePopen):
        def __init__(self, speaker):
            super().__init__()
            self._speaker = speaker

        def wait(self, timeout=None):
            # While we are "blocking", the speaker treats us as current.
            self._speaker.cancel()
            return super().wait(timeout=timeout)

    class HookRunner:
        def __init__(self):
            self.procs = []

        def __call__(self, text, voice, rate):
            proc = CancelOnWaitPopen(captured["speaker"])
            self.procs.append(proc)
            return proc

    runner = HookRunner()
    sp = Speaker(say_runner=runner)
    captured["speaker"] = sp
    sp.speak("active")
    assert runner.procs[0].terminate_calls == 1


def test_cancel_with_no_current_proc_is_noop():
    sp = Speaker(say_runner=RecordingRunner())
    # Never called speak; cancel must not raise.
    sp.cancel()


# ---------------------------------------------------------------------------
# Lock / race-condition / timeout tests
# ---------------------------------------------------------------------------


def test_cancel_terminates_proc_tracked_as_current():
    """cancel() must call terminate() on the proc held in _current."""
    terminated = []
    captured_speaker = {}

    class TrackedPopen(FakePopen):
        def wait(self, timeout=None):
            # The proc is current at this point; cancel the speaker.
            captured_speaker["sp"].cancel()
            return super().wait(timeout=timeout)

        def terminate(self):
            terminated.append(True)
            super().terminate()

    class TrackedRunner:
        def __call__(self, text, voice, rate):
            return TrackedPopen()

    sp = Speaker(say_runner=TrackedRunner())
    captured_speaker["sp"] = sp
    sp.speak("test")
    # terminate() must have been called once from cancel().
    assert len(terminated) == 1


def test_speak_wait_timeout_terminates_hung_proc():
    """A proc whose wait() always raises TimeoutExpired must be terminated."""

    class HungPopen(FakePopen):
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd=["say"], timeout=timeout)
            # No-timeout call should never happen in normal flow.
            return 0

    class HungRunner:
        def __call__(self, text, voice, rate):
            return HungPopen()

    # Inject a very small timeout (0.01 s) so the test is instantaneous.
    sp = Speaker(say_runner=HungRunner(), _wait_timeout=0.01)
    sp.speak("will hang")
    # The fake proc's terminate must have been called.
    # We verify by checking terminate_calls via a shared reference.
    procs = []

    class TrackingHungPopen(FakePopen):
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd=["say"], timeout=timeout)
            return 0

    class TrackingHungRunner:
        def __call__(self, text, voice, rate):
            p = TrackingHungPopen()
            procs.append(p)
            return p

    sp2 = Speaker(say_runner=TrackingHungRunner(), _wait_timeout=0.01)
    sp2.speak("will hang tracked")
    assert len(procs) == 1
    assert procs[0].terminate_calls == 1


class _DoneProc:
    returncode = 0
    def wait(self, timeout=None):
        return 0
    def terminate(self):
        self.returncode = -15


class _KilledProc:
    returncode = None
    def wait(self, timeout=None):
        self.returncode = -15
        return -15
    def terminate(self):
        self.returncode = -15


def test_speak_returns_true_when_say_completes():
    from sonari.speaker import Speaker
    s = Speaker(say_runner=lambda text, voice, rate: _DoneProc())
    assert s.speak("Hello there.") is True


def test_speak_returns_false_when_say_terminated():
    from sonari.speaker import Speaker
    s = Speaker(say_runner=lambda text, voice, rate: _KilledProc())
    assert s.speak("Hello there.") is False


def test_cancel_during_synthesis_aborts_before_play():
    """Regression (#2/#9): a cancel() that lands while say_runner is still
    synthesizing — when there is no proc to terminate yet — must still abort the
    utterance: speak() returns False and the just-created proc is terminated, not
    waited on / played."""
    made = []
    sp = None

    def runner(text, voice, rate):
        sp.cancel()                      # cancel lands mid-synthesis, before a proc exists
        proc = FakePopen()
        made.append(proc)
        return proc

    sp = Speaker(say_runner=runner)
    completed = sp.speak("hello")
    assert completed is False              # not completed -> caller replays / leaves unheard
    assert made[0].terminate_calls == 1    # the new proc was terminated
    assert made[0].wait_calls == 0         # never waited on / played


def test_speak_honors_external_cancel_epoch_baseline():
    """M2: the daemon captures the cancel epoch at CLAIM time (under its lock),
    then calls speak() AFTER releasing the lock. A cancel landing in that gap bumps
    the epoch; speak() must compare against the passed-in baseline (not re-read the
    already-bumped value) and report the utterance as cancelled."""
    sp = Speaker(say_runner=lambda t, v, r: FakePopen())
    epoch0 = sp.cancel_epoch()             # captured at claim
    sp.cancel()                            # cancel lands in the pop->speak gap
    proc_made = []

    def runner(text, voice, rate):
        proc = FakePopen()
        proc_made.append(proc)
        return proc

    sp = Speaker(say_runner=runner)
    epoch0 = sp.cancel_epoch()
    sp.cancel()                            # bump past the captured baseline
    completed = sp.speak("hello", cancel_epoch=epoch0)
    assert completed is False              # baseline mismatch -> interrupted
    assert proc_made[0].terminate_calls == 1
    assert proc_made[0].wait_calls == 0


def test_speak_without_external_epoch_uses_current_baseline():
    """Backward-compatible: with no cancel_epoch passed, speak() reads the current
    epoch as its baseline (a prior cancel with no pending speak is not retroactive)."""
    made = []

    def runner(text, voice, rate):
        proc = FakePopen()
        made.append(proc)
        return proc

    sp = Speaker(say_runner=runner)
    sp.cancel()                            # bumps epoch, but no speak was in flight
    sp.speak("hello")                      # next speak starts clean
    assert made[0].wait_calls == 1         # played, not retroactively cancelled
    assert made[0].terminate_calls == 0


# ---------------------------------------------------------------------------
# audio_path / afplay_runner tests (Task 3)
# ---------------------------------------------------------------------------


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

    proc = CancelOnWait()
    sp = Speaker(afplay_runner=lambda path: proc)
    captured["sp"] = sp
    sp.speak(audio_path="/cache/x.aiff")
    # the tracked afplay proc was terminated by cancel() — barge-in parity with say
    assert proc.terminate_calls == 1


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
