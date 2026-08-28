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
from unittest import mock

from sonari.speaker import Speaker
from sonari.sessions import SessionManager
from sonari.daemon import SpeechDaemon
import sonari.daemon.host as host_mod
from sonari.config import DEFAULTS
from sonari.protocol import MsgType, PROTOCOL_VERSION
from tests.daemon_helpers import FakeSpearconCache

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


def _fast_afplay(path):
    """afplay runner for pre-roll spearcon items: completes immediately, like
    _FastRunner's say procs, so the storm churns through audio items too."""
    p = _SlowProc()
    p.finish(0)
    return p


def _make_real_daemon(runner, foreground="s0"):
    speaker = Speaker(say_runner=runner, afplay_runner=_fast_afplay)
    sessions = SessionManager()
    sessions.set_foreground(foreground)
    config = {k: (v.copy() if isinstance(v, dict) else v)
              for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    # W13: arm the keep-going pre-roll under the storm — every folder resolves a
    # (fake) cached spearcon, so each genuine keep-going fire synthesizes and
    # claims an audio item INSIDE the locked block, hammering the new path.
    spearcons = FakeSpearconCache()
    for s in ("s0", "s1", "s2", "s_bg"):
        spearcons.available[s] = "/x/fake-spearcon.aiff"
    daemon = SpeechDaemon(speaker, sessions, config, spearcons=spearcons)
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

    # SP4: real-threaded frontier-monotonicity instrumentation (synthesis §5). The
    # deterministic hammer (test_frontier_never_retreats_across_write_paths) proves
    # non-retreat logically across every write path; this proves it holds under
    # ACTUAL thread contention, catching a lock-discipline regression the
    # deterministic test cannot (e.g. a future caller invoking advance_frontier
    # outside self._lock). Instance-attr shadow, self-contained per fresh stream —
    # no restore needed in `finally`.
    frontier_log = {s: [] for s in ("s0", "s1", "s2")}
    def _make_logging_advance(sess, st, orig):
        def _logging_advance(key):
            orig(key)
            frontier_log[sess].append(st.frontier)
        return _logging_advance
    for _s in ("s0", "s1", "s2"):
        _st = daemon._stream(_s)
        _st.advance_frontier = _make_logging_advance(_s, _st, _st.advance_frontier)

    # Passive keep-going target: preloaded backlog, NO feeder thread. Its items are the
    # OLDEST in the daemon (enqueued before the storm -> smallest SpeechItem.ids), so
    # whenever the CURRENT speaker goes idle (and is itself non-stopped -- see below),
    # _select_keep_going (host.py:42, which explicitly skips the current speaker)
    # prefers s_bg over any other competing backlog.
    # STOP_ALL (T5) stops s_bg too, under voice_state=stopped-all, which gates
    # keep-going off outright. s_bg is NOT permanently stopped, though: post-T4 the
    # un-stop path runs through the WORKSPACE, not the speaker. a chooser commit (CHOOSER_STEP→CHOOSER_COMMIT)
    # (features/chooser.py) still calls sessions.focus(target), which has no .stopped check, so a
    # hammer thread's a chooser commit (CHOOSER_STEP→CHOOSER_COMMIT) can still park _foreground (the workspace) on a
    # stopped s_bg -- but T4 immediately releases the SPEAKER off a stopped landing
    # (sessions.set_speaker(None)), so the voice itself is no longer parked there.
    # STOP_SESSION (T4, Fork 4 asymmetric) is what then acts on s_bg: it now targets
    # sessions.workspace() when that stream is stopped, else sessions.speaker() --
    # NOT "whichever stream is CURRENTLY speaker()" as pre-T4. So if s_bg is still the
    # workspace when a STOP_SESSION fires, it un-stops s_bg, calls
    # sessions.set_speaker(s_bg) (moving the voice onto it directly), and lifts
    # voice_state to flowing. a chooser commit (CHOOSER_STEP→CHOOSER_COMMIT) (T4) ALSO unconditionally sets
    # voice_state="flowing" on every fire (not only muted-landings) -- a second,
    # independent lift path with no pre-T4 equivalent. Because a REAL keep-going fire
    # additionally needs the scan to run with the CURRENT speaker idle/non-stopped
    # (the held branch returns before keep-going ever executes otherwise), and the
    # STOP_SESSION-start path above moves the voice by a direct set_speaker() call
    # rather than via the scan, s_bg being picked up by genuine keep-going (as opposed
    # to being un-stopped by STOP_SESSION-start) is a narrower window post-T4. The
    # true keep-going-only fire count is the COMMITTED real_keep_going_fires counter
    # in the stress test (a _select_keep_going wrap -- verified single call site at
    # host.py:490) -- NOT the keep_going_fires[0] counter below, which
    # is no longer keep-going-exclusive post-T4 (see its own comment).
    sessions.register("s_bg", cwd="/x/s_bg")
    for i in range(50):
        daemon._enqueue("s_bg", "prose", "bg {0}.".format(i), False)
    keep_going_fires = [0]
    _orig_set_speaker = sessions.set_speaker
    def _counting_set_speaker(s):
        # NOTE (T4): set_speaker() is no longer keep-going's exclusive caller --
        # on_stop_session's resume branch (Fork 4 asymmetric start) and
        # the chooser commit (features/chooser.py)'s muted-landing branch both call it now too.
        # This counter is therefore a general "the voice moved via set_speaker()"
        # count, not a keep-going-only one; see the s_bg/hammer() comments below and
        # real_keep_going_fires below for the keep-going-exclusive proof.
        keep_going_fires[0] += 1
        return _orig_set_speaker(s)
    sessions.set_speaker = _counting_set_speaker

    # Keep-going-EXCLUSIVE counter (T4 review fix, restores the guard's original
    # meaning). _select_keep_going (host.py:42) has exactly one call site (host.py:490,
    # verified via grep), so wrapping it and counting non-None returns is immune to the
    # two new set_speaker() callers above -- it can ONLY go non-zero when the in-lock
    # scan actually selects a background session. This is a MODULE-level patch (unlike
    # the instance shadows above, which are self-contained per fresh daemon/sessions
    # objects), so it MUST be restored in the finally below regardless of outcome --
    # otherwise a failed assertion mid-storm would leave every daemon in the process
    # (including later tests) running the wrapped version.
    real_keep_going_fires = [0]
    _orig_select_keep_going = host_mod._select_keep_going
    def _counting_select_keep_going(streams, sess):
        result = _orig_select_keep_going(streams, sess)
        if result is not None:
            real_keep_going_fires[0] += 1
        return result
    host_mod._select_keep_going = _counting_select_keep_going

    try:
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
            # a chooser commit (CHOOSER_STEP→CHOOSER_COMMIT) calls sessions.focus() (resets BOTH pointers), then (T4) checks
            # the landed target: if its stream is stopped, sessions.set_speaker(None)
            # releases the SPEAKER again right away -- so focus() still parks _foreground
            # (the workspace) on a stopped stream (including s_bg), but no longer leaves
            # the SPEAKER parked there. a chooser commit (CHOOSER_STEP→CHOOSER_COMMIT) (T4) ALSO unconditionally sets
            # voice_state="flowing" on every fire, regardless of the landed target's
            # stopped state -- a second, independent lift path alongside STOP_SESSION
            # below. will_attempt(None) is False (no identities registered), so
            # raise_async never fires -- same no-raise shape as the existing JUMP_WAITING
            # op.
            # STOP_ALL (SP3/T5) sets voice_state=stopped-all (suppressing keep-going) and
            # stops EVERY stream -- including the passive s_bg -- under the one lock.
            # STOP_SESSION (T4, Fork 4 asymmetric) targets sessions.workspace() when that
            # stream is stopped, else sessions.speaker() -- NOT "whichever stream is
            # CURRENTLY speaker()" as pre-T4. So when a chooser commit (CHOOSER_STEP→CHOOSER_COMMIT) has just parked the
            # workspace on a stopped stream (including s_bg), the next STOP_SESSION in the
            # rotation un-stops THAT stream (the workspace), calls sessions.set_speaker(fg)
            # to move the voice onto it, and lifts voice_state to flowing -- this, plus
            # a chooser commit (CHOOSER_STEP→CHOOSER_COMMIT)'s own unconditional lift, are the two ops in this rotation
            # that can lift stopped-all back to flowing. JUMP_WAITING still can't
            # (_waiting_target skips every stopped stream, so under stopped-all it always
            # finds no target and takes the no-lift "nothing waiting" branch -- it lifts
            # quiet-hold only, where the speaker's stream alone is held). So flowing
            # windows recur via STOP_SESSION-start and/or a chooser commit (CHOOSER_STEP→CHOOSER_COMMIT), and keep-going
            # still fires -- proven by real_keep_going_fires below (NOT the
            # keep_going_fires[0] counter, which T4 also feeds from on_stop_session's
            # resume-branch set_speaker(fg) and the chooser commit (features/chooser.py)'s muted-landing
            # set_speaker(None), so it no longer isolates keep-going alone -- see the
            # counter's own comment). on_stop_all iterates _streams.values() under the
            # one lock, so the born-muted read and the enum write are lock-consistent with
            # the speak loop's gate read (no torn read; F3/F5).
            ops = [MsgType.STOP_SESSION, MsgType.FLUSH, MsgType.SET_FOREGROUND,
                   MsgType.JUMP_WAITING, MsgType.CHOOSER_STEP, MsgType.CHOOSER_DIGIT,
                   MsgType.CHOOSER_COMMIT, MsgType.CHOOSER_CANCEL, MsgType.STOP_ALL,
                   # REPEAT_LAST (W12) hammers the capture+park path against the
                   # loop's tail-lock write.
                   MsgType.REPEAT_LAST,
                   # SP4: exercise the pile-skip's queue.clear() + frontier advance under
                   # contention; the orphaned-marker assertion below covers its enqueues.
                   MsgType.SKIP_PILE]
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
        # This counter is no longer keep-going-exclusive post-T4 (set_speaker() gained two
        # more legitimate callers -- on_stop_session's resume branch and the chooser commit (features/chooser.py)'s
        # muted-landing release; see the _counting_set_speaker and hammer() comments
        # above), so a non-zero count alone no longer PROVES the in-lock keep-going scan
        # ran under contention -- it can be satisfied entirely by the two new T4 callers.
        # Kept as a coarser "the voice moved under contention" signal. Coverage is
        # RESTORED by the additive real_keep_going_fires assertion directly below, which
        # is immune to those two callers. (Do NOT weaken this assertion; if it ever
        # flakes, widen the idle window -- raise the feeder sleep or extend the storm --
        # never drop the assertion.)
        assert keep_going_fires[0] > 0, "keep-going never fired; the idle window was empty"
        # T4 REVIEW FIX: the assertion above lost its exclusivity when T4 added two more
        # set_speaker() callers (see its comment). This one is immune to them: it counts
        # non-None returns of _select_keep_going itself (host.py:490, the ONLY call
        # site), so it can only go non-zero when the in-lock keep-going scan actually
        # selected a background session -- exactly what the pre-T4 keep_going_fires
        # assertion proved. ADDITIVE, not a replacement (the never-weaken mandate keeps
        # the original assertion as-is). (Do NOT weaken this either; if it ever flakes,
        # widen the idle window -- never drop the assertion.)
        assert real_keep_going_fires[0] > 0, \
            "keep-going scan never actually selected a background session; the idle window was empty"
        # W13 (additive): with every folder's spearcon armed above, each genuine
        # keep-going fire ran the pre-roll resolution inside the locked block
        # (set_speaker -> folder -> _spearcon_path -> cache.get). Non-zero here
        # proves the NEW path was exercised by this storm, not merely compiled.
        assert len(daemon._spearcons.requested) > 0, \
            "keep-going fired but the pre-roll spearcon path never resolved a label"
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
        # SP4: frontier monotonicity under REAL contention (synthesis §5) — every
        # advance_frontier call recorded above must be non-decreasing per session;
        # a race that ever let the frontier retreat would show up here as a drop.
        for sess, log in frontier_log.items():
            assert all(b >= a for a, b in zip(log, log[1:])), \
                "frontier retreated under contention for {0}: {1}".format(sess, log)
    finally:
        host_mod._select_keep_going = _orig_select_keep_going


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
            # STOP_SESSION sets stream.stopped and enqueues "Stopped." (control_cue);
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

    # _ReentrantFlusher has no .transient(), so cue() raises inside
    # _signal_speak_failure and trips T15's gap-B fallback — which shells out to
    # a REAL `say`. Pre-T15 that AttributeError was swallowed with no fallback,
    # so this test was silent; now it speaks aloud on every run. The owner works
    # eyes-free and runs this suite constantly. Mocked, not "fixed" in the stub:
    # the raise is what drives the failure path this race test needs.
    with mock.patch("sonari.cli.voiceout.speak_direct"):
        daemon._speak_loop_once()                  # keep-going pops bg, claims, speaks; FLUSH races

    assert daemon._current_item is None            # claim released
    assert len(daemon._stream("bg").queue) == 0    # item popped; FLUSH cleared nothing extra
    assert daemon._pending_heard == {}             # NO orphaned marker


class _RaisingSpeaker:
    """speak() always raises — the W6 failure-tone path: the real loop must
    signal error_system audibly and survive (an eyes-free swallowed exception
    is a SILENT no-op, the worst outcome)."""

    def __init__(self):
        self.earcons: list = []
        self._epoch = 0

    def speak(self, text=None, audio_path=None, cancel_epoch=None):
        raise RuntimeError("synthesized failure")

    def cancel_epoch(self):
        return self._epoch

    def cancel(self):
        self._epoch += 1

    def earcon(self, kind):
        self.earcons.append(kind)

    def transient(self, kind):
        # Same list as earcon() — the earcon->transient migration must not
        # change what this assertion observes.
        self.earcons.append(kind)


def test_speak_failure_signals_error_system_and_the_loop_survives():
    """W6 guard: a raising utterance inside the REAL _speak_loop_once fires the
    error_system tone via _signal_speak_failure, releases the claim, and leaves
    the loop able to keep going. PERMANENT, like everything in this file."""
    sessions = SessionManager()
    sessions.set_foreground("fg")
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    daemon = SpeechDaemon(None, sessions, config)
    speaker = _RaisingSpeaker()
    daemon.speaker = speaker
    daemon._enqueue("fg", "prose", "doomed", False)
    daemon._speak_loop_once()                      # must NOT propagate
    assert "error_system" in speaker.earcons
    assert daemon._current_item is None            # claim released, no wedge


def test_frontier_provenance_gated_advance_is_permanent():
    """B1 PERMANENT: the frontier is the max over FORWARD-provenance completions, NOT
    over the heard flag. A browse replay completes and flips heard=True on an entry
    ABOVE the frontier, yet the frontier does NOT move; a forward readout advances it.
    NEVER weaken this — a heard-derived frontier is the B1 corruption."""
    from sonari.queue import SpeechItem
    sessions = SessionManager(); sessions.set_foreground("s0")
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    daemon = SpeechDaemon(_RaisingSpeaker(), sessions, config)
    st = daemon._stream("s0")
    e0 = daemon.history.record("s0", "prose", "a"); daemon.history.end_message("s0")
    e1 = daemon.history.record("s0", "prose", "b")          # (1,0), ABOVE e0
    br = SpeechItem(id=1, session="s0", kind="prose", text="b",
                    is_decision=False, forward=False)       # browse replay
    daemon._pending_heard[br.id] = e1; daemon._current_item = br
    daemon.note_spoken(br, completed=True)
    assert e1.heard is True and st.frontier is None         # heard flipped, frontier did NOT move
    fw = SpeechItem(id=2, session="s0", kind="prose", text="a",
                    is_decision=False, forward=True)        # forward readout
    daemon._pending_heard[fw.id] = e0; daemon._current_item = fw
    daemon.note_spoken(fw, completed=True)
    assert st.frontier == (e0.msg_id, e0.seq)               # forward readout advances it


def test_frontier_never_retreats_across_write_paths():
    """Monotonicity: forward-hear -> advance; browse-replay-above -> no move; pile-skip
    -> advance to live edge; new arrival -> no move. The frontier never retreats."""
    from sonari.queue import SpeechItem
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    daemon = SpeechDaemon(_RaisingSpeaker(), sessions, config)
    st = daemon._stream("s0")
    es = []
    for i in range(4):
        es.append(daemon.history.record("s0", "prose", "p{0}".format(i)))
        daemon.history.end_message("s0")                    # es -> (0,0)..(3,0)
    seen = []
    it = SpeechItem(id=1, session="s0", kind="prose", text="p1",
                    is_decision=False, forward=True)
    daemon._pending_heard[it.id] = es[1]; daemon._current_item = it
    daemon.note_spoken(it, completed=True); seen.append(st.frontier)      # (1,0)
    br = SpeechItem(id=2, session="s0", kind="prose", text="p3",
                    is_decision=False, forward=False)
    daemon._pending_heard[br.id] = es[3]; daemon._current_item = br
    daemon.note_spoken(br, completed=True); seen.append(st.frontier)      # (1,0) — no move
    with daemon._state.transaction():
        daemon.handle_message(_msg(MsgType.SKIP_PILE, "s0"))
    seen.append(st.frontier)                                              # (3,0) — skip to live
    daemon.history.record("s0", "prose", "p4"); seen.append(st.frontier)  # (3,0) — arrival no move
    assert seen == [(1, 0), (1, 0), (3, 0), (3, 0)]
    assert all(b >= a for a, b in zip(seen, seen[1:]))                    # never retreats
