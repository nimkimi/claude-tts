"""The speak thread must survive ANY exception in its loop body — a crash in
pop_next/note_spoken/etc. previously killed the thread permanently (daemon alive,
earcons firing, but mute forever until a restart). Regression guard."""
import time as _time

from tests.daemon_helpers import make_daemon


def test_speak_loop_survives_internal_exception(monkeypatch):
    daemon = make_daemon()[0]
    seen = []

    def boom_first_then_stop():
        seen.append(1)
        if len(seen) == 1:
            raise RuntimeError("boom in the loop body")   # iteration 1 crashes
        daemon._running.clear()                            # iteration 2: end loop

    monkeypatch.setattr(daemon, "_speak_loop_once", boom_first_then_stop)
    daemon._running.set()
    daemon._speak_loop()   # must return normally despite the iteration-1 raise
    assert len(seen) >= 2  # the loop kept going after the exception


def test_speak_loop_once_speaks_and_notes(monkeypatch):
    # The extracted body still does the normal work: speak then note_spoken.
    daemon, queue, speaker, sessions, config = make_daemon()
    noted = []
    monkeypatch.setattr(daemon, "note_spoken", lambda item, completed: noted.append(completed))
    daemon._enqueue("fg", "prose", "hello", False)
    daemon._speak_loop_once()
    assert noted == [True]          # FakeSpeaker.speak returns True (completed)


def test_speak_thread_keeps_speaking_after_a_bad_note_spoken(monkeypatch):
    # End-to-end: note_spoken raises on the first item; the second item must
    # still be spoken (thread did not die).
    import threading
    import time
    daemon, queue, speaker, sessions, config = make_daemon()
    n = {"calls": 0}

    def flaky_note(item, completed):
        n["calls"] += 1
        if n["calls"] == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(daemon, "note_spoken", flaky_note)
    daemon._enqueue("fg", "prose", "first", False)
    daemon._enqueue("fg", "prose", "second", False)
    t = threading.Thread(target=daemon._speak_loop, daemon=True)
    t.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and n["calls"] < 2:
        time.sleep(0.02)
    daemon.stop()
    t.join(timeout=1.0)
    assert n["calls"] >= 2          # the second item reached note_spoken -> survived


# ---------------------------------------------------------------------------
# A swallowed speak() exception used to be a SILENT no-op (the worst outcome for
# an eyes-free user — e.g. a Kokoro voice synced from a box with the [kokoro]
# extra to one without it). The loop must still survive, but now it also signals
# the failure audibly (error earcon) and logs it. (#41)
# ---------------------------------------------------------------------------

def _raise(exc):
    def _boom(*a, **k):
        raise exc
    return _boom


def test_speak_failure_fires_error_earcon_and_notes_not_completed(monkeypatch):
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    noted = []
    monkeypatch.setattr(daemon, "note_spoken", lambda item, completed: noted.append(completed))
    monkeypatch.setattr(speaker, "speak", _raise(RuntimeError("kokoro extra not installed")))
    daemon._enqueue("fg", "prose", "hello", False)

    daemon._speak_loop_once()                    # exception contained, must not raise

    assert speaker.earcons == ["error_system"]   # W6: eyes-free user hears the failure
    assert noted == [False]                       # still marked not-completed (unchanged)


def test_speak_failure_on_control_cue_fires_error_earcon(monkeypatch):
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    monkeypatch.setattr(speaker, "speak", _raise(RuntimeError("synth blew up")))
    # Simulate the stopped state (per-session stop replaces the old global pause).
    daemon._stream("fg").stopped = True
    daemon._enqueue("fg", "prose", "Stopped.", False, control_cue=True)

    daemon._speak_loop_once()                    # stopped-branch failure, contained

    assert speaker.earcons == ["error_system"]   # W6


def test_cancelled_utterance_does_not_fire_error_earcon(monkeypatch):
    # speak() returning False is an INTERRUPT (terminate), not an error — it must
    # NOT fire the error earcon. Only a raised exception is an error.
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    speaker.complete = False                     # next speak() reports not-completed
    daemon._enqueue("fg", "prose", "hello", False)

    daemon._speak_loop_once()

    assert speaker.earcons == []                 # no false-positive error signal


def test_error_earcon_failure_is_contained(monkeypatch):
    # If signaling the error itself raises, the loop must still not die.
    from unittest import mock

    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    monkeypatch.setattr(speaker, "speak", _raise(RuntimeError("synth blew up")))
    monkeypatch.setattr(speaker, "transient", _raise(RuntimeError("earcon backend down")))
    daemon._enqueue("fg", "prose", "hello", False)

    # D4 T15: cue() raising here now trips the #54 gap-B fallback (speak_direct,
    # a real `say` shell-out) — mocked so the suite stays silent; this test is
    # about containment, not about that call landing.
    with mock.patch("sonari.cli.voiceout.speak_direct"):
        daemon._speak_loop_once()                # must return normally despite both raising


# ---------------------------------------------------------------------------
# _signal_speak_failure must log the traceback to stderr AND fire the error
# earcon.  Before the fix, `traceback` and `sys` were not in scope inside
# _signal_speak_failure (they are imported locally in other methods), causing
# a NameError that the bare `except` swallowed — stderr was always empty.
# ---------------------------------------------------------------------------

def test_signal_speak_failure_logs_traceback_to_stderr():
    import io
    import contextlib
    from unittest import mock

    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    buf = io.StringIO()
    # D4 T15: the session-less branch now also speaks via voiceout.speak_direct
    # (a real `say` shell-out) — mocked here so the suite stays silent; the
    # earcon/traceback behaviour under test is unrelated to that call.
    with mock.patch("sonari.cli.voiceout.speak_direct"):
        try:
            raise RuntimeError("synthetic synth failure")
        except RuntimeError:
            with contextlib.redirect_stderr(buf):
                daemon._signal_speak_failure()

    stderr_output = buf.getvalue()
    assert "Traceback (most recent call last)" in stderr_output, (
        f"Expected traceback in stderr but got: {stderr_output!r}"
    )
    assert speaker.earcons == ["error_system"], (  # W6
        f"Expected error_system earcon but got: {speaker.earcons!r}"
    )


# ---------------------------------------------------------------------------
# DIAG-3: heartbeat — _last_drain advances each time note_spoken is called.
# ---------------------------------------------------------------------------

def test_last_drain_is_none_before_any_drain():
    """Heartbeat sentinel starts as None (no drain has happened yet)."""
    daemon = make_daemon()[0]
    assert daemon._last_drain is None


def test_started_at_is_set_at_construction():
    """_started_at is a wall-clock timestamp captured in __init__."""
    before = _time.time()
    daemon = make_daemon()[0]
    after = _time.time()
    assert hasattr(daemon, "_started_at")
    assert before <= daemon._started_at <= after


def test_heartbeat_advances_after_each_drain():
    """_last_drain is set (and advances) each time the speak loop drains an item."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    assert daemon._last_drain is None

    daemon._enqueue("fg", "prose", "first", False)
    daemon._speak_loop_once()
    first_drain = daemon._last_drain
    assert first_drain is not None, "_last_drain must be set after the first drain"

    # Let monotonic advance (even sub-ms is fine — we just need the assignment to fire again).
    daemon._enqueue("fg", "prose", "second", False)
    _time.sleep(0.01)
    daemon._speak_loop_once()
    second_drain = daemon._last_drain
    assert second_drain >= first_drain, "_last_drain must not go backwards"
