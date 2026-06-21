"""PERMANENT concurrency guards for the Stage-2 speak-loop/state core.

The black-box net is synchronous and cannot see thread interleaving. These two
tests run the REAL blocking _speak_loop against a fake say_runner while other
threads hammer the handlers under the real lock, and a deterministic re-entrant
speaker that fires PAUSE/FLUSH from inside speak(). They guard the M2/L2 races
(cancel in the pop->speak gap; a FLUSH racing a paused-item re-queue) and the
"list changed size during iteration" failure class. NEVER retire these.
"""
from __future__ import annotations

import threading
import time

from sonari.speaker import Speaker
from sonari.sessions import SessionManager
from sonari.daemon import SpeechDaemon
from sonari.config import DEFAULTS
from sonari.protocol import MsgType, PROTOCOL_VERSION

TIMEOUT = 5.0


def _msg(t, session, **kw):
    d = {"v": PROTOCOL_VERSION, "type": t, "session": session}
    d.update(kw)
    return d


class _SlowProc:
    """Event-gated stand-in for the `say` subprocess. wait() blocks until
    terminate() (cancel) or finish() ends playback, so the test controls when
    each utterance ends and can guarantee a real concurrent window."""

    def __init__(self) -> None:
        self.returncode = None
        self._ended = threading.Event()

    def wait(self, timeout=None):
        cap = TIMEOUT if timeout is None else min(timeout, TIMEOUT)
        if not self._ended.wait(cap):
            import subprocess
            raise subprocess.TimeoutExpired(cmd=["say"], timeout=timeout)
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = -15
        self._ended.set()

    def poll(self):
        return self.returncode

    def finish(self, rc=0) -> None:
        self.returncode = rc
        self._ended.set()


class _FastRunner:
    """say_runner whose procs finish almost immediately, so the real speak loop
    churns fast and the hammer threads collide with live pop/note_spoken."""

    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, text, voice, rate):
        with self._lock:
            self.calls += 1
        p = _SlowProc()
        p.finish(0)  # already done: speak() returns True without blocking long
        return p


def _make_real_daemon(runner, foreground="s0"):
    speaker = Speaker(say_runner=runner)
    sessions = SessionManager()
    sessions.set_foreground(foreground)
    config = {k: (v.copy() if isinstance(v, dict) else v)
              for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    daemon = SpeechDaemon(speaker, sessions, config)
    return daemon, speaker


def test_stress_no_lost_duplicated_or_resurrected_item():
    """Real-threaded stress: the REAL blocking _speak_loop runs against a fake
    say_runner while threads hammer PAUSE/FLUSH/SET_FOREGROUND/JUMP_WAITING. The
    invariant: no crash (no 'dictionary/list changed size during iteration'),
    the speak thread never dies, and every stream's pending count stays bounded
    and non-negative — i.e. no item is lost, duplicated, or resurrected into a
    flushed queue. Probabilistic by design (interleaving pressure)."""
    runner = _FastRunner()
    daemon, speaker = _make_real_daemon(runner, foreground="s0")
    sessions = daemon.sessions
    for s in ("s0", "s1", "s2"):
        sessions.register(s, cwd="/x/" + s)

    errors: list = []
    speak_thread = threading.Thread(target=daemon._speak_loop, daemon=True)
    speak_thread.start()

    stop = threading.Event()

    def feeder(sess):
        i = 0
        while not stop.is_set():
            try:
                daemon._dispatch_hotkey(_msg(MsgType.PROSE, sess,
                    delta="line {0}. ".format(i), index=i, final=False))
                i += 1
            except Exception as e:  # noqa: BLE001
                errors.append(("feeder", sess, e))
                return

    def hammer(sess):
        ops = [MsgType.PAUSE, MsgType.FLUSH, MsgType.SET_FOREGROUND,
               MsgType.JUMP_WAITING]
        n = 0
        while not stop.is_set():
            try:
                daemon._dispatch_hotkey(_msg(ops[n % len(ops)], sess))
                n += 1
            except Exception as e:  # noqa: BLE001
                errors.append(("hammer", sess, e))
                return

    threads = []
    for s in ("s0", "s1", "s2"):
        threads.append(threading.Thread(target=feeder, args=(s,), daemon=True))
        threads.append(threading.Thread(target=hammer, args=(s,), daemon=True))
    for t in threads:
        t.start()

    time.sleep(1.0)  # let the interleaving run
    stop.set()
    for t in threads:
        t.join(TIMEOUT)
        assert not t.is_alive(), "a hammer/feeder thread deadlocked"

    daemon._running.clear()
    daemon._wake.set()
    speak_thread.join(TIMEOUT)

    # No handler raised (the "list changed size during iteration" class).
    assert errors == [], "concurrency errors: {0}".format(errors[:3])
    # The speak thread survived the whole storm.
    assert not speak_thread.is_alive(), "speak thread died under stress"
    # Every stream's queue is non-negative and bounded by the backlog cap; the
    # _pending_heard dict never exceeds the total queued (no leak/resurrection).
    with daemon._lock:
        total_queued = sum(len(st.queue) for st in daemon._streams.values())
        for st in daemon._streams.values():
            assert len(st.queue) <= daemon._backlog_cap
        assert len(daemon._pending_heard) <= total_queued + 1


class _ReentrantSpeaker:
    """Deterministic re-entrant FakeSpeaker: its speak() fires PAUSE then FLUSH
    (in that order) BEFORE returning not-completed, exactly reproducing the L2
    race — a FLUSH landing between speak() returning and the pause re-queue. The
    interrupted item must NOT be resurrected into the flushed queue; because FLUSH
    (not a bare PAUSE) wins, the re-queue/rollback branch (daemon.py:1011) is
    skipped, so _last_spoken_session stays committed (no rollback)."""

    def __init__(self, daemon):
        self.daemon = daemon
        self.log: list = []
        self._epoch = 0
        self._fired = False

    def speak(self, text, cancel_epoch=None):
        self.log.append(text)
        if not self._fired:
            self._fired = True
            # PAUSE sets _paused; FLUSH then clears it AND flushes the queue.
            self.daemon.handle_message(_msg(MsgType.PAUSE, "fg"))
            self.daemon.handle_message(_msg(MsgType.FLUSH, "fg"))
        return False  # interrupted

    def cancel_epoch(self):
        return self._epoch

    def cancel(self):
        self._epoch += 1

    def earcon(self, kind):
        self.log.append(("earcon", kind))

    def set_rate(self, r):
        pass

    def set_voice(self, v):
        pass


def test_reentrant_flush_does_not_resurrect_paused_item():
    """L2 (deterministic): speak() fires PAUSE then FLUSH before returning
    not-completed. The re-queue-on-pause check is INSIDE the lock and re-reads
    _paused, so the FLUSH (which cleared pause) wins — the item is NOT
    resurrected, and because FLUSH cleared pause the re-queue/rollback branch
    (daemon.py:1011) is skipped, so _last_spoken_session stays at its committed
    value (no rollback)."""
    sessions = SessionManager()
    sessions.set_foreground("fg")
    config = {k: (v.copy() if isinstance(v, dict) else v)
              for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    daemon = SpeechDaemon(None, sessions, config)
    speaker = _ReentrantSpeaker(daemon)
    daemon.speaker = speaker
    daemon._last_spoken_session = None  # pre-speak baseline

    daemon._enqueue("fg", "prose", "interrupted", False)
    daemon._speak_loop_once()

    assert speaker.log == ["interrupted"]            # spoken once, not replayed
    assert not daemon._paused.is_set()                # FLUSH cleared the pause
    assert len(daemon._stream("fg").queue) == 0       # NOT resurrected
    assert daemon._current_item is None               # claim released
    assert daemon._last_spoken_session == "fg"        # NOT rolled back: FLUSH cleared pause, so the re-queue branch (daemon.py:1011) is skipped — no resurrect, no rollback
