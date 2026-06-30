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
    say_runner while threads hammer PAUSE/FLUSH/SET_FOREGROUND/JUMP_WAITING. This
    guard's invariants, under interleaving pressure: no crash (no 'dictionary/list
    changed size during iteration' — captured via the boundary wrappers below, since
    production deliberately swallows handler/loop exceptions), no deadlock (every
    thread joins), the speak loop stays live (runner.calls > 0), and no ORPHANED
    pending-heard marker survives the storm. It does NOT bound queue length (cap-
    exempt cues exceed _backlog_cap by design — see the assertion note). Lost/
    duplicated/resurrected TRACKED items are pinned deterministically by
    test_reentrant_stop_flush_requeues_item_exactly_once. Probabilistic by design."""
    runner = _FastRunner()
    daemon, speaker = _make_real_daemon(runner, foreground="s0")
    sessions = daemon.sessions
    for s in ("s0", "s1", "s2"):
        sessions.register(s, cwd="/x/" + s)

    # Passive keep-going target: preloaded backlog, NO feeder/hammer thread, and no
    # hammer thread ever sends a SET_FOREGROUND/STOP/FLUSH at it. Its items are the
    # OLDEST in the daemon (enqueued before the storm -> smallest SpeechItem.ids), so
    # whenever the speaker goes idle, _select_keep_going prefers it. A non-zero
    # set_speaker count therefore proves the new in-lock scan+pop ran under real
    # contention. (JUMP_WAITING/CYCLE could ALSO happen to focus it; that does not
    # affect the > 0 assertion — keep-going still fires on every other idle window.)
    sessions.register("s_bg", cwd="/x/s_bg")
    for i in range(50):
        daemon._enqueue("s_bg", "prose", "bg {0}.".format(i), False)
    keep_going_fires = [0]
    _orig_set_speaker = sessions.set_speaker
    def _counting_set_speaker(s):                  # set_speaker is called ONLY by keep-going
        keep_going_fires[0] += 1
        return _orig_set_speaker(s)
    sessions.set_speaker = _counting_set_speaker

    errors: list = []
    # Capture crashes at the boundary BEFORE the production swallow layers eat
    # them: _dispatch_hotkey wraps handle_message in `except Exception: pass`, and
    # _speak_loop catches+continues every _speak_loop_once exception by design. We
    # record-then-reraise so a real race ("list changed size during iteration",
    # lost/duplicated/resurrected item) is OBSERVED here, then still swallowed by
    # production (the speak thread must stay alive). Instance attrs shadow the
    # bound methods, so _speak_loop's `self._speak_loop_once()` and
    # _dispatch_hotkey's `self.handle_message(...)` hit these wrappers.
    _orig_loop_once = daemon._speak_loop_once
    def _capture_loop_once():
        try:
            _orig_loop_once()
        except Exception as e:  # noqa: BLE001 - record, then re-raise into _speak_loop's handler
            errors.append(("speak_loop", repr(e)))
            raise
    daemon._speak_loop_once = _capture_loop_once
    _orig_handle = daemon.handle_message
    def _capture_handle(m):
        try:
            return _orig_handle(m)
        except Exception as e:  # noqa: BLE001 - record, then re-raise into _dispatch_hotkey's handler
            errors.append(("handle_message", repr(e)))
            raise
    daemon.handle_message = _capture_handle
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
                time.sleep(0.001)                  # brief gaps so the speaker goes idle -> keep-going fires
            except Exception as e:  # noqa: BLE001
                errors.append(("feeder", sess, e))
                return

    def hammer(sess):
        # CYCLE_SESSION calls sessions.focus() (resets BOTH pointers), racing keep-going's
        # set_speaker() (which moves only _speaker) -> exercises diverge-then-resync under
        # contention. will_attempt(None) is False (no identities registered), so raise_async
        # never fires -- same no-raise shape as the existing JUMP_WAITING op.
        ops = [MsgType.STOP_SESSION, MsgType.FLUSH, MsgType.SET_FOREGROUND,
               MsgType.JUMP_WAITING, MsgType.CYCLE_SESSION]
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
    # The speak loop actually did work — not merely "didn't deadlock at the end".
    assert runner.calls > 0, "speak loop never spoke anything; the stress window was empty"
    # The new in-lock keep-going scan+pop actually ran under contention: the passive
    # s_bg backlog can only advance the voice via set_speaker(), which keep-going alone
    # calls. (Do NOT weaken this; if it ever flakes, widen the idle window -- raise the
    # feeder sleep or extend the storm -- never drop the assertion.)
    assert keep_going_fires[0] > 0, "keep-going never fired; the idle window was empty"
    # This guard does NOT bound queue LENGTH. Cap-exempt cues (PAUSE "Paused./
    # Resumed.", JUMP_WAITING "Jumping to...") use enqueue_front, which is
    # deliberately NOT subject to _backlog_cap (queue.py:42-45), so under this
    # synthetic cue-storm a stream legitimately exceeds the cap (verified: ~50% of
    # isolated runs reach len up to ~1000). Those cues drain and carry no pending-
    # heard marker, so it is not a leak. The real leak/resurrection invariant lives
    # in test_reentrant_stop_flush_requeues_item_exactly_once. Here, in the quiescent
    # end state (storm over, speak thread joined), assert only that every pending-
    # heard marker still maps to a LIVE queued item or the in-flight claim — no
    # ORPHANED markers. Cue-volume-independent, so it keeps its teeth no matter how
    # many cap-exempt cues piled up.
    with daemon._lock:
        live_ids = {it.id for st in daemon._streams.values()
                    for it in st.queue._items}
        if daemon._current_item is not None:
            live_ids.add(daemon._current_item.id)
        orphaned = [k for k in daemon._pending_heard if k not in live_ids]
        assert orphaned == [], "orphaned pending-heard markers: {0}".format(orphaned[:5])


class _ReentrantSpeaker:
    """Deterministic re-entrant FakeSpeaker: its speak() fires STOP_SESSION then
    FLUSH (in that order) BEFORE returning not-completed, exactly reproducing the
    L2 race — a FLUSH landing between speak() returning and the stop re-queue.
    STOP_SESSION sets stream.stopped (sticky); FLUSH clears the queue but NOT
    stopped. The L2 re-queue check inside the lock sees stopped=True and re-queues
    the interrupted item (stop wins; sticky resume-from-spot). The queue has only
    the one re-queued item — FLUSH's clear ran first, so there is no duplication.
    _last_spoken_session is rolled back (the item will be re-attributed on resume)."""

    def __init__(self, daemon):
        self.daemon = daemon
        self.log: list = []
        self._epoch = 0
        self._fired = False

    def speak(self, text, cancel_epoch=None):
        self.log.append(text)
        if not self._fired:
            self._fired = True
            # STOP_SESSION sets stream.stopped and enqueues "Stopped." (pause_exempt);
            # FLUSH then clears the queue (including "Stopped.") but leaves stopped=True.
            self.daemon.handle_message(_msg(MsgType.STOP_SESSION, "fg"))
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


def test_reentrant_stop_flush_requeues_item_exactly_once():
    """L2 (deterministic): speak() fires STOP_SESSION then FLUSH before returning
    not-completed. STOP_SESSION sets stream.stopped (sticky); FLUSH clears the
    queue but NOT stopped. The re-queue check is INSIDE the lock: it sees stopped=True
    and re-queues the interrupted item into the now-empty (flushed) queue. The item
    is held (not spoken) because stopped=True. _last_spoken_session is rolled back
    so the resumed utterance re-gets its attribution prefix. No duplication: the
    item appears exactly once because the FLUSH clear and the re-queue run under
    the same lock — they cannot interleave."""
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

    assert speaker.log == ["interrupted"]              # spoken once, not replayed yet
    assert daemon._stream("fg").stopped is True        # sticky: FLUSH did NOT clear it
    assert len(daemon._stream("fg").queue) == 1        # re-queued exactly once (no dup)
    assert daemon._stream("fg").queue._items[0].text == "interrupted"
    assert daemon._current_item is None                # claim released
    assert daemon._last_spoken_session is None         # rolled back to pre-speak baseline


class _ReentrantFlusher:
    """speak() fires FLUSH(bg) once, before returning not-completed -- a FLUSH landing
    in the keep-going pop->speak gap. bg is NOT stopped, so the L2 re-queue check sees
    stopped=False and does NOT re-queue; note_spoken drops the marker. No orphan survives."""

    def __init__(self, daemon, bg):
        self.daemon = daemon
        self.bg = bg
        self._epoch = 0
        self._fired = False

    def speak(self, text, audio_path=None, cancel_epoch=None):
        if not self._fired:
            self._fired = True
            self.daemon.handle_message(_msg(MsgType.FLUSH, self.bg))
        return False

    def cancel_epoch(self):
        return self._epoch

    def cancel(self):
        self._epoch += 1

    def earcon(self, kind):
        pass


class _HeardEntry:
    def __init__(self):
        self.heard = False


def test_keep_going_flush_race_leaves_no_orphan():
    """M1: a kept-going item is popped+claimed under the lock; a FLUSH fired during
    speak() clears bg's (already-emptied) queue and, because bg is not stopped, the L2
    check does NOT re-queue -> note_spoken drops the marker. If scan+pop were not atomic
    this would strand a claim with no marker."""
    sessions = SessionManager()
    sessions.set_foreground("fg")                  # speaker=fg, empty/idle
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    daemon = SpeechDaemon(None, sessions, config)
    speaker = _ReentrantFlusher(daemon, bg="bg")
    daemon.speaker = speaker
    daemon._last_spoken_session = None
    sessions.register("bg", cwd="/x/bg")
    daemon._enqueue("bg", "prose", "race", False, entry=_HeardEntry())

    daemon._speak_loop_once()                      # keep-going pops bg, claims, speaks; FLUSH races

    assert daemon._current_item is None            # claim released
    assert len(daemon._stream("bg").queue) == 0    # item popped; FLUSH cleared nothing extra
    assert daemon._pending_heard == {}             # NO orphaned marker
