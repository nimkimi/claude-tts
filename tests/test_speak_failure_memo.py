"""I3: a broken audio path must be detectable — the speak-failure memo.

Owner-ruled closure design (whole-branch review finding I3): speak()'s
failure paths feed _signal_speak_failure AND write SPEAK_FAIL_MEMO_PATH;
doctor's speech-path row reads the memo (tests/test_doctor_speech_path.py).
Log-scraping speechd.log was rejected as fragile — this is the durable,
on-disk trace an eyes-free user's `sonari doctor` can actually see.

Session-full items throughout (advisor guidance): a sessionless failure
routes its word through voiceout.speak_direct (a real `say` shell-out) —
unrelated to what this file tests, and worth not triggering mid-suite.
"""
from unittest import mock

from sonari import paths
from tests.daemon_helpers import make_daemon


def _raise_speak_failure(*a, **k):
    from sonari.speaker import SpeakFailure
    raise SpeakFailure("synthesized failure")


# ---------------------------------------------------------------------------
# (a) spawn-fail-shaped failure: signal fires once, memo written
# ---------------------------------------------------------------------------


def test_a_speak_failure_fires_the_signal_and_writes_the_memo(monkeypatch):
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    monkeypatch.setattr(speaker, "speak", _raise_speak_failure)
    daemon._enqueue("fg", "prose", "hello", False)

    assert not paths.SPEAK_FAIL_MEMO_PATH.exists()      # setup: nothing recorded yet
    daemon._speak_loop_once()                           # exception contained

    assert speaker.earcons == ["error_system"]           # the existing W6 signal still fires
    assert paths.SPEAK_FAIL_MEMO_PATH.exists(), "a SpeakFailure must leave a memo"


def test_a_speak_failure_on_the_pause_exempt_held_branch_also_writes_the_memo(monkeypatch):
    """The speak loop has two call sites (the normal branch and the stopped/
    pause-exempt held branch) — both feed the same _signal_speak_failure, so
    both must leave the memo."""
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    monkeypatch.setattr(speaker, "speak", _raise_speak_failure)
    daemon._stream("fg").stopped = True
    daemon._enqueue("fg", "prose", "Stopped.", False, pause_exempt=True)

    daemon._speak_loop_once()

    assert paths.SPEAK_FAIL_MEMO_PATH.exists()


# ---------------------------------------------------------------------------
# (c) cancel-shaped outcome: NO signal, NO memo — the hard-fail guard
# ---------------------------------------------------------------------------


def test_a_cancelled_utterance_writes_no_memo(monkeypatch):
    """speak() returning False (barge-in / interrupt) is not an error — it must
    not fire the failure earcon (existing regression guard,
    test_daemon_speak_resilience.py::test_cancelled_utterance_does_not_fire_error_earcon)
    AND, new for I3, must not touch the memo either. A naive False->failure
    wiring would fire the memo on every ordinary barge-in — the brief's
    explicit hard-fail condition."""
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    speaker.complete = False                     # FakeSpeaker reports not-completed, no raise
    daemon._enqueue("fg", "prose", "hello", False)

    daemon._speak_loop_once()

    assert speaker.earcons == []
    assert not paths.SPEAK_FAIL_MEMO_PATH.exists()


# ---------------------------------------------------------------------------
# (d) a later success clears a previously-written memo
# ---------------------------------------------------------------------------


def test_a_completed_utterance_clears_a_previously_recorded_failure(monkeypatch):
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._signal_speak_failure(session="fg")    # simulate the earlier recorded failure
    assert paths.SPEAK_FAIL_MEMO_PATH.exists(), "setup: a failure should leave a memo"

    speaker.complete = True
    daemon._enqueue("fg", "prose", "hello", False)
    daemon._speak_loop_once()                     # this utterance completes cleanly

    assert not paths.SPEAK_FAIL_MEMO_PATH.exists(), "a completed utterance must clear the memo"


def test_an_incomplete_utterance_does_not_clear_the_memo(monkeypatch):
    """Only a COMPLETED utterance clears it — an ordinary requeue/barge-in must
    not paper over a real, still-unresolved failure."""
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._signal_speak_failure(session="fg")
    assert paths.SPEAK_FAIL_MEMO_PATH.exists()

    speaker.complete = False
    daemon._enqueue("fg", "prose", "hello", False)
    daemon._speak_loop_once()

    assert paths.SPEAK_FAIL_MEMO_PATH.exists(), "an incomplete utterance must not clear the memo"


def test_a_completed_spearcon_does_not_clear_a_say_path_failure_memo():
    """C1: "completed" is only ever proof of the path the item actually took.

    speaker.py:speak() routes on audio_path -- an item that carries one is
    played by AFPLAY, and one that does not is played by SAY. They are separate
    binaries over separate audio routes, so a completed spearcon proves afplay
    works and says NOTHING about say. A say-only outage is precisely the shape
    the memo exists to record (an eyes-free user hears nothing and has only
    `sonari doctor` to ask), and every jump between sessions plays a cached
    spearcon -- the owner has ~70 of them cached. Ungated on the route, one
    ordinary jump wipes the memo and doctor's speech-path row goes back to
    reporting the daemon healthy straight through a total speech outage.
    """
    import sonari.daemon.host as daemon_host
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon_host._mark_speak_failure()
    assert paths.SPEAK_FAIL_MEMO_PATH.exists(), "setup: a failure should be recorded"

    speaker.complete = True
    daemon._enqueue("fg", "prose", "backend", False,
                    audio_path="/cached/spearcons/backend.wav")
    daemon._speak_loop_once()

    assert speaker.audio_paths == ["/cached/spearcons/backend.wav"], (
        "setup: the item must actually have taken the afplay route")
    assert paths.SPEAK_FAIL_MEMO_PATH.exists(), (
        "a completed spearcon plays through afplay -- it is no evidence the say "
        "path recovered, so it must not clear the memo")


# ---------------------------------------------------------------------------
# (f) memo I/O is total — an unwritable SONARI_DIR must never raise
# ---------------------------------------------------------------------------


def test_a_memo_write_failure_never_raises(tmp_path, monkeypatch):
    """Mirrors test_client_ensure_backoff.py::test_a_memo_write_failure_never_raises
    for DAEMON_FAIL_MEMO_PATH: point the memo at a path whose parent is a plain
    FILE (not a directory) so mkdir/touch hit a real OSError, and confirm the
    speak loop still returns normally — _signal_speak_failure's memo write must
    never itself wedge the one thread carrying all of Sonari's speech."""
    blocked = tmp_path / "blocker_file"
    blocked.write_text("not a directory")
    bogus_memo_path = blocked / "speak.fail_memo"

    import sonari.daemon.host as daemon_host
    monkeypatch.setattr(daemon_host, "SPEAK_FAIL_MEMO_PATH", bogus_memo_path, raising=False)

    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    monkeypatch.setattr(speaker, "speak", _raise_speak_failure)
    daemon._enqueue("fg", "prose", "hello", False)

    daemon._speak_loop_once()                     # must not raise despite the bogus memo path

    assert speaker.earcons == ["error_system"]      # signaling still worked
    assert not bogus_memo_path.exists()


# ---------------------------------------------------------------------------
# (b) end-to-end seam: a REAL Speaker whose proc exits nonzero, uncancelled —
# the AudioQueueStart(-66681) shape. FakeSpeaker-based tests above prove the
# LOOP; this proves the Speaker->loop CONTRACT actually connects (mock-
# blindness warning, shared-context.md).
# ---------------------------------------------------------------------------


def test_a_real_speaker_nonzero_exit_signals_failure_and_writes_the_memo():
    from sonari.speaker import Speaker
    from sonari.sessions import SessionManager
    from sonari.daemon import SpeechDaemon
    from sonari.config import DEFAULTS

    class _BrokenProc:
        """Mimics `say` printing an AudioQueueStart error and exiting nonzero —
        never terminated, never cancelled, just a genuine playback failure."""
        returncode = 17

        def wait(self, timeout=None):
            return 17

        def terminate(self):
            pass    # never called in this scenario

    played_earcons = []
    speaker = Speaker(say_runner=lambda t, v, r: _BrokenProc(),
                      earcon_player=lambda p: played_earcons.append(p) or None)
    sessions = SessionManager()
    sessions.set_foreground("fg")
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    daemon = SpeechDaemon(speaker, sessions, config)
    noted = []
    with mock.patch.object(daemon, "note_spoken", lambda item, completed: noted.append(completed)):
        daemon._enqueue("fg", "prose", "hello", False)
        daemon._speak_loop_once()                  # must not raise out of the loop

    assert played_earcons, "the failure earcon must have fired through the REAL Speaker"
    assert noted == [False]
    assert paths.SPEAK_FAIL_MEMO_PATH.exists()
