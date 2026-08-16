"""W8 (spec §9): the daemon announces its own restart. Direct one-shot speaker
thread — an enqueued cue would never voice (no registered sessions at boot; the
loop plays only speaker()'s stream and keep-going scans only registered ids)."""
import threading
import time

import sonari.daemon.bootstrap as bootstrap
from sonari import paths


def test_boot_cue_exact_string_spoken_once():
    spoken = []

    class _Spk:
        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            spoken.append(text)
            return True

    bootstrap._start_boot_cue(_Spk())
    deadline = time.time() + 2.0
    while not spoken and time.time() < deadline:
        time.sleep(0.01)
    assert spoken == ["Sonari restarted. Sessions re-register on their next prompt."]


def test_boot_cue_start_is_non_blocking():
    release = threading.Event()

    class _Blocking:
        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            release.wait(2.0)
            return True

    t0 = time.monotonic()
    bootstrap._start_boot_cue(_Blocking())
    assert time.monotonic() - t0 < 0.5             # returned while speak() still blocked
    release.set()


def test_main_wires_the_cue_before_run(monkeypatch):
    order = []
    monkeypatch.setattr(bootstrap, "_arm_faulthandler", lambda: None)
    monkeypatch.setattr(bootstrap, "socket_connectable", lambda: False)
    monkeypatch.setattr(bootstrap, "ensure_sonari_dir", lambda: None)
    monkeypatch.setattr(bootstrap.transport, "acquire_singleton", lambda p: object())
    monkeypatch.setattr(bootstrap, "load_config", lambda: {"earcons": {}})

    class _FakePlat:
        class tts:  # noqa: D106 - attribute container
            run = staticmethod(lambda *a, **k: None)

        class earcon:  # noqa: D106
            play = staticmethod(lambda *a, **k: None)
            default_earcons = staticmethod(lambda: {})

    monkeypatch.setattr("sonari.platform.get_platform", lambda: _FakePlat)

    class _FakeCache:
        def __init__(self, *a, **k):
            pass

        def cleanup(self):
            pass

    monkeypatch.setattr("sonari.spearcon.SpearconCache", _FakeCache)

    class _FakeDaemon:
        def __init__(self, *a, **k):
            pass

        def run(self):
            order.append("run")

    monkeypatch.setattr(bootstrap, "SpeechDaemon", _FakeDaemon)
    monkeypatch.setattr(bootstrap, "_start_boot_cue", lambda spk: order.append("cue"))

    bootstrap.main()
    assert order == ["cue", "run"]                 # cue armed, THEN the daemon serves


# --- item C (wave1-T4): a failed boot cue must LEAVE A TRACE, not just die quietly ---
# T1 (I3) wired the speak loop's failure-shaped outcomes to a memo doctor can read,
# but the boot cue never goes through the speak loop -- it is this file's own
# one-shot thread, wrapped in `except Exception: pass`. Closing that gap matters
# most exactly here: a dead audio device at daemon start means the FIRST thing
# the daemon ever tries to say fails, and without a memo `sonari doctor` still
# reports healthy at the one moment the user is most likely to first notice
# silence.


def test_boot_cue_failure_writes_the_speak_fail_memo():
    class _Failing:
        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            from sonari.speaker import SpeakFailure
            raise SpeakFailure("synthesized failure")

    assert not paths.SPEAK_FAIL_MEMO_PATH.exists()          # setup: nothing recorded yet
    bootstrap._start_boot_cue(_Failing())
    deadline = time.time() + 2.0
    while not paths.SPEAK_FAIL_MEMO_PATH.exists() and time.time() < deadline:
        time.sleep(0.01)
    assert paths.SPEAK_FAIL_MEMO_PATH.exists(), "a failed boot cue must leave a memo"


def test_boot_cue_success_leaves_no_memo():
    done = threading.Event()

    class _Spk:
        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            done.set()
            return True

    assert not paths.SPEAK_FAIL_MEMO_PATH.exists()
    bootstrap._start_boot_cue(_Spk())
    assert done.wait(2.0), "speak() was never called"
    time.sleep(0.05)                       # let the (no-op, success) thread finish unwinding
    assert not paths.SPEAK_FAIL_MEMO_PATH.exists()


# --- I3 (wave1 whole-branch review): the memo was WRITE-ONLY at boot ---
# Item C above wired the failure direction only. `_clear_speak_failure_memo` is
# reachable from note_spoken(completed=True) alone, and at boot no session is
# registered, so nothing the daemon does at startup could ever clear it: audio
# breaks -> memo -> the user fixes it -> the daemon restarts -> the user AUDIBLY
# HEARS the restart line -> `sonari doctor` still reports "speech failure
# recorded Nm ago" for up to 24h. The boot cue is the best available proof the
# audio path works, and this branch already treats it as authoritative in the
# failure direction; it must be symmetric.


def test_boot_cue_success_clears_a_stale_memo():
    done = threading.Event()

    class _Spk:
        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            done.set()
            return True

    paths.SPEAK_FAIL_MEMO_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.SPEAK_FAIL_MEMO_PATH.touch()          # a failure recorded BEFORE this restart
    bootstrap._start_boot_cue(_Spk())
    assert done.wait(2.0), "speak() was never called"
    deadline = time.time() + 2.0
    while paths.SPEAK_FAIL_MEMO_PATH.exists() and time.time() < deadline:
        time.sleep(0.01)
    assert not paths.SPEAK_FAIL_MEMO_PATH.exists(), (
        "a restart the user could HEAR still left doctor reporting a speech failure")


def test_boot_cue_cut_short_does_not_clear_the_memo():
    """Gated on the RETURN, not on 'did not raise' — symmetric with note_spoken,
    where only a COMPLETED utterance clears the memo. speak() returns False iff
    it was cancelled/interrupted; a barge-in is not proof the audio path works
    and must not paper over a recorded failure."""
    done = threading.Event()

    class _Cut:
        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            done.set()
            return False

    paths.SPEAK_FAIL_MEMO_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.SPEAK_FAIL_MEMO_PATH.touch()
    bootstrap._start_boot_cue(_Cut())
    assert done.wait(2.0), "speak() was never called"
    time.sleep(0.05)                       # let the thread finish unwinding
    assert paths.SPEAK_FAIL_MEMO_PATH.exists(), "an interrupted cue cleared a real failure"


def test_boot_cue_memo_clear_failure_does_not_propagate(monkeypatch):
    """The 'Never raises' contract holds on the clear side too."""
    escaped = []
    monkeypatch.setattr(threading, "excepthook", lambda args: escaped.append(args))
    attempted = threading.Event()

    def _raise_on_clear():
        attempted.set()
        raise OSError("boom")

    monkeypatch.setattr(bootstrap, "_clear_speak_failure_memo", _raise_on_clear)

    class _Spk:
        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            return True

    bootstrap._start_boot_cue(_Spk())
    assert attempted.wait(2.0), "the memo clear was never attempted"
    time.sleep(0.05)
    assert escaped == [], "a memo-clear failure inside the boot cue must not escape the thread"


def test_boot_cue_memo_write_failure_does_not_propagate(monkeypatch):
    """The docstring's contract is absolute: '_start_boot_cue' 'Never raises'.
    That must hold even when the memo write ITSELF blows up -- point the memo
    helper at a fake that raises and confirm nothing escapes the daemon thread
    (threading.excepthook is the only place an uncaught thread exception would
    surface; a passing test here means it never fired)."""
    escaped = []
    monkeypatch.setattr(threading, "excepthook", lambda args: escaped.append(args))
    memo_attempted = threading.Event()

    def _raise_on_memo():
        memo_attempted.set()
        raise OSError("boom")

    monkeypatch.setattr(bootstrap, "_mark_speak_failure", _raise_on_memo)

    class _Failing:
        def speak(self, text=None, audio_path=None, cancel_epoch=None):
            from sonari.speaker import SpeakFailure
            raise SpeakFailure("synthesized failure")

    bootstrap._start_boot_cue(_Failing())
    assert memo_attempted.wait(2.0), "the memo write was never attempted"
    time.sleep(0.05)                       # let the thread finish unwinding
    assert escaped == [], "a memo-write failure inside the boot cue must not escape the thread"
