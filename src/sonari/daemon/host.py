from __future__ import annotations

import os
import secrets
import threading
import time

from sonari.protocol import MsgType
from sonari.queue import SpeechItem
from sonari.session_stream import SessionStream
from sonari.paths import LOCK_PATH, ensure_sonari_dir
from sonari.platform import transport
from sonari.daemon.state import SessionState
from sonari.daemon.context import Ctx
from sonari.daemon.registry import handler, dispatch
from sonari.daemon.server import Server
from sonari.daemon.limits import RATE_MIN, RATE_MAX, MINQUEUE_MIN, MINQUEUE_MAX

PERMISSION_WAIT_TIMEOUT = 120.0   # daemon's own wait; MUST be < the hook's client send timeout (130s)
# Side-effect imports: importing each feature module runs its @handler
# decorators, populating the registry (assert_complete in __init__ guards it).
from sonari.daemon.features import control  # noqa: F401
from sonari.daemon.features import decisions  # noqa: F401
from sonari.daemon.features import lifecycle  # noqa: F401
from sonari.daemon.features import navigation  # noqa: F401
from sonari.daemon.features import playback  # noqa: F401
from sonari.daemon.features import focus  # noqa: F401
from sonari.daemon.features import prose  # noqa: F401
from sonari.daemon.features import hotkeys  # noqa: F401
from sonari.daemon.features import chooser  # noqa: F401


def _stream_quiescent(st) -> bool:
    """True when *st* has nothing left to voice: no queued items, no buffered prose,
    no half-assembled sentence. None (no stream) counts as quiescent. The inverse of
    _voice_busy_elsewhere's stream clause (shared so the busy predicate and the
    keep-going gate use one definition)."""
    return st is None or (len(st.queue) == 0
                          and len(st.prose_buffer) == 0
                          and not st.assembler.has_pending())


def _select_keep_going(streams, sessions) -> "str | None":
    """The longest-waiting eligible background session, or None. Eligible = a
    registered session other than the current speaker whose stream exists, is not
    stopped, and has a non-empty queue. Among those, pick the minimum
    SpeechQueue.oldest_id() (the globally-monotonic SpeechItem.id of the oldest unheard
    item). Runs INSIDE the speak-loop lock; never pokes _items.

    §14 is longest-waiting-first AT EACH IDLE WINDOW, not global starvation-freedom:
    re-selection happens only at speaker-idle, so a busy speaker drains FIFO ahead of
    older items elsewhere, and a perpetually-busy autonomous producer defers all
    background sessions indefinitely — the escape is a deliberate ⌃⌘J / ⌃⌘Tab."""
    spk = sessions.speaker()
    best = None
    best_id = None
    for s in sessions.session_ids():
        if s == spk:
            continue
        st = streams.get(s)
        if st is None or st.stopped or len(st.queue) == 0:
            continue
        oid = st.queue.oldest_id()
        if oid is None:
            continue
        if best_id is None or oid < best_id:
            best, best_id = s, oid
    return best


class SpeechDaemon:
    def __init__(self, speaker, sessions, config, raise_service=None,
                 spearcons=None) -> None:
        self.speaker = speaker
        self.sessions = sessions
        self.config = config
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._state = SessionState(self._lock)
        self._ctx = Ctx(self)
        self._token = None
        self._server = Server(
            dispatch=self._handle_message_guarded,
            token_provider=lambda: self._token,
            running=self._running,
        )
        self._poll_interval = 0.1
        from sonari.history import SessionHistory
        self.history = SessionHistory(cap=int(config.get("history_cap", 200)))
        self._backlog_cap = int(config.get("backlog_cap", 200))
        self._reload_lock = threading.Lock()      # serializes off-lock hotkey reloads
        self.raise_service = raise_service        # lazily built on first jump
        self._spearcons = spearcons              # SpearconCache, or None (no spearcons)
        # Pending permission decisions: session_id -> {"event": Event, "behavior": str|None}.
        # Mutated ONLY under self._lock (handlers); the Event is waited on ONLY outside
        # the lock (in _handle_message_guarded, after the transaction exits).
        self._pending_decisions: dict = {}
        # The open session-chooser gesture (features/chooser.py ChooserState), or
        # None. Mutated ONLY under self._lock (all chooser handlers run inside
        # _state.transaction()); the speak loop never reads it.
        self._chooser = None
        # Diagnostics: wall-clock start time and monotonic drain heartbeat.
        # _last_drain is None until the first item drains; updated as a bare
        # assignment in note_spoken (no lock — a float write is atomic in CPython,
        # and this is observe-only data for status/diagnosis).
        self._started_at: float = time.time()
        self._last_drain: "float | None" = None

    # --- Ledger shims (Step 7): storage lives on SessionState. The hot path
    # (speak loop + kernel ops) goes through self._state._X directly; these
    # properties bridge the self._X name for cold-path callers (tests, the
    # concurrency guards, feature modules on the connection thread). 3 are
    # read/write (rebindable scalars); 3 are read-only (mutated in place). ---
    @property
    def _streams(self):
        return self._state._streams

    @property
    def _pending_heard(self):
        return self._state._pending_heard

    @property
    def _wake(self):
        return self._state._wake

    @property
    def _current_item(self):
        return self._state._current_item

    @_current_item.setter
    def _current_item(self, value):
        self._state._current_item = value

    @property
    def _last_spoken_session(self):
        return self._state._last_spoken_session

    @_last_spoken_session.setter
    def _last_spoken_session(self, value):
        self._state._last_spoken_session = value

    @property
    def _last_utterance(self):
        return self._state._last_utterance

    @_last_utterance.setter
    def _last_utterance(self, value):
        self._state._last_utterance = value

    @property
    def _next_id(self):
        return self._state._next_id

    @_next_id.setter
    def _next_id(self, value):
        self._state._next_id = value

    @property
    def voice_state(self):
        """The voice-global mode ("flowing"/"quiet-hold"/"stopped-all"). COLD-PATH
        shim (⌃⌘W, STATUS, tests, handlers); the speak-loop gate reads
        self._state._voice_state directly (Step-7 hot-path discipline)."""
        return self._state._voice_state

    @voice_state.setter
    def voice_state(self, value):
        self._state._voice_state = value

    def _raise(self):
        """The RaiseService, built lazily on first use (so tests can inject a fake
        via `daemon.raise_service` before any jump). Cached after the first call."""
        if self.raise_service is None:
            from sonari.raise_service import RaiseService
            from sonari.platform import get_platform
            self.raise_service = RaiseService(get_platform().raise_backend, self.config)
        return self.raise_service

    def _raise_failed(self, session: str, folder) -> None:
        """Raise thread reported failure for a still-current jump: tell the user
        to bring the window forward by hand. Acquires the daemon lock (this runs
        off the message-handler path)."""
        # Diagnostic: log raise failure so backend (AppleScript/TCC) failures are visible.
        import sys
        try:
            print(f"sonari[focus]: raise FAILED session={session} folder={folder}",
                  file=sys.stderr)
        except Exception:
            pass  # Never raise from diagnostic emit
        text = ("Bring {0} forward to type.".format(folder) if folder
                else "Bring it forward to type.")
        with self._lock:
            self._enqueue(session, "prose", text, False,
                          mute_exempt=True, at_front=True)

    def _alloc_id(self) -> int:
        self._state._next_id += 1
        return self._state._next_id

    def _stream(self, session: str) -> SessionStream:
        s = self._state._streams.get(session)
        if s is None:
            s = SessionStream(queue_cap=self._backlog_cap)
            if self._state._voice_state == "stopped-all":
                # Born-muted (F2/M2, SPEC:270-272): a session created while the voice
                # is stopped-all is born stopped, so the ungated primary pop can't
                # speak it (the gate covers only the keep-going scan). ONLY under
                # stopped-all — under quiet-hold a new session piles + dings.
                s.stopped = True
            self._state._streams[session] = s
        return s

    def _voice_busy_elsewhere(self, session: str) -> bool:
        """True when a DIFFERENT session currently owns active speech — an in-flight
        utterance, queued backlog, or buffered (not-yet-flushed) prose. A background
        session's prompt event (a /loop tick or a background-task completion firing
        UserPromptSubmit -> SET_FOREGROUND) must NOT seize the voice from such a
        session (#65); it accumulates as a jump-to-waiting target instead. When the
        voice is idle — or `session` already owns it — the switch is harmless and
        proceeds as before. Read under self._lock (the handler path holds it), so the
        snapshot is consistent with the speak loop's pop+claim."""
        cur = self._state._current_item
        if cur is not None and cur.session != session:
            return True                       # an utterance from another session is in flight
        spk = self.sessions.speaker()
        if spk is not None and spk != session:
            st = self._state._streams.get(spk)
            if not _stream_quiescent(st):
                return True                   # the voice owner still has speech to deliver
        return False

    def _enqueue(self, session: str, kind: str, text: str, is_decision: bool,
                 entry=None, mute_exempt: bool = False,
                 pause_exempt: bool = False, at_front: bool = False,
                 names_session: bool = False, audio_path=None,
                 forward: bool = False) -> int:
        """Returns the new item's id (W7: on_permission_request tracks its queued
        ask); all other callers ignore it."""
        item = SpeechItem(
            id=self._alloc_id(),
            session=session,
            kind=kind,
            text=text,
            is_decision=is_decision,
            mute_exempt=mute_exempt,
            pause_exempt=pause_exempt,
            names_session=names_session,
            audio_path=audio_path,
            forward=forward,
        )
        st = self._stream(session)
        if entry is not None:
            self._state._pending_heard[item.id] = entry
        if at_front:
            st.queue.enqueue_front(item)
        else:
            evicted = st.queue.enqueue(item)
            if evicted is not None:
                self._drop_pending([evicted])
        self._state._wake.set()
        return item.id

    def _minqueue(self) -> int:
        try:
            return max(MINQUEUE_MIN, min(MINQUEUE_MAX, int(self.config.get("minqueue", 1))))
        except (TypeError, ValueError):
            return 1

    def _buffer_prose(self, session: str, text: str, entry) -> None:
        """Hold prose until the per-session buffer reaches the minqueue threshold,
        then flush it all to the speech queue at once (drain-all, then re-gate).
        With minqueue == 1 this flushes on every item — today's behaviour."""
        st = self._stream(session)
        st.prose_buffer.append((text, entry))
        if len(st.prose_buffer) >= self._minqueue():
            self._flush_prose_buffer(session)

    def _flush_prose_buffer(self, session: str) -> None:
        """Enqueue everything buffered for *session* (e.g. at the turn boundary, so
        a message that ended below the threshold is still read)."""
        st = self._stream(session)
        buf = st.prose_buffer
        if not buf:
            return
        st.prose_buffer = []
        for text, entry in buf:
            self._enqueue(session, "prose", text, False, entry=entry, forward=True)

    def _drop_pending(self, items) -> None:
        for it in items:
            self._state._pending_heard.pop(it.id, None)

    def _spearcon_path(self, folder) -> "str | None":
        """The cached spearcon audio file for *folder*'s short label, or None when no
        cache is wired or the file isn't generated yet (the caller then falls back to
        plain speech and the cache kicks off background generation for next time).
        Never blocks; never on the hot path."""
        if not folder or self._spearcons is None:
            return None
        return self._spearcons.get(folder)

    def _attributed_text(self, item) -> str:
        """item.text, prefixed with the session's folder name when the voice switches
        to a session different from the one last spoken — so the user knows who's
        talking. Never prefixes the very first utterance (last == None), a self-naming
        cue (names_session), or a control cue (mute_exempt). Updates _last_spoken_session.
        Called under self._lock from the speak loop."""
        text = item.text
        if item.names_session:
            # Self-naming cue (e.g. "Jumping to backend."):
            # claim this session as last-spoken so the NEXT item from it is
            # NOT prefixed again — suppresses the double-announce.
            self._state._last_spoken_session = item.session
        elif not item.mute_exempt:
            if (self._state._last_spoken_session is not None
                    and item.session != self._state._last_spoken_session):
                folder = self.sessions.folder(item.session)
                if folder:
                    text = "{0}. {1}".format(folder, item.text)
            self._state._last_spoken_session = item.session
        return text

    def note_spoken(self, item, completed: bool) -> None:
        """Speak-loop bookkeeping: confirm (or decline) the heard-marker for a
        finished utterance, and release the current-item claim."""
        # Heartbeat: stamp the drain time BEFORE acquiring the lock so this
        # observe-only write never adds latency to or contends with the lock path.
        # A float write is atomic in CPython; no lock needed for a diagnostic field.
        self._last_drain = time.monotonic()
        with self._lock:
            self._state._current_item = None
            entry = self._state._pending_heard.pop(item.id, None)
            if entry is not None and completed:
                entry.heard = True
                if item.forward:
                    # Frontier write-path (a): a forward-readout item completing.
                    # O(1) — the key is already on the entry, no history scan (R-3).
                    # Gated on item.forward so a browse replay (forward=False) that
                    # flips heard above cannot drag the frontier (B1); gated on
                    # completed so a mid-item barge-in never advances it (R-8).
                    st = self._state._streams.get(item.session)
                    if st is not None:
                        st.advance_frontier((entry.msg_id, entry.seq))

    def _await_permission_decision(self, session: str, timeout: float) -> dict:
        """Block (OUTSIDE the daemon lock) until the focused-session answer arrives
        or the wait expires. Returns {"decision": "allow"|"deny"|None}; None means the
        hook falls through to Claude Code's normal terminal prompt (fail-closed)."""
        with self._lock:
            pd = self._pending_decisions.get(session)
        if pd is None:
            return {"decision": None}
        got = pd["event"].wait(timeout)
        with self._lock:
            behavior = pd["behavior"] if got else None
            # Pop only if still ours (a newer request for the same session may have replaced it).
            if self._pending_decisions.get(session) is pd:
                self._pending_decisions.pop(session, None)
                if not got:
                    # W7: the ask silently died at the wall. Mark it audibly and
                    # remove the now-unanswerable queued text so a later read/⌃⌘D
                    # never voices a dead ask as live. History is KEPT (transcript
                    # replay is explicit archaeology; only the QUEUE must not lie).
                    self._expire_permission(session, pd)
        return {"decision": behavior}

    def _expire_permission(self, session: str, pd: dict) -> None:
        """Timeout housekeeping for a dead blocking permission (W7). Caller holds
        self._lock — the cleanup takes the lock exactly as the existing pop does,
        no new lock ordering. The earcon is a fire-and-forget Popen. If the text
        is IN FLIGHT (already popped) remove_by_id misses: it finishes playing and
        the expiry earcon beside it is the honest context (accepted edge)."""
        try:
            self.speaker.earcon("permission_expired")
        except Exception:  # noqa: BLE001 - expiry signaling must never break the reply
            pass
        item_id = pd.get("item_id")
        if item_id is None:
            return
        st = self._state._streams.get(session)
        if st is None:
            return
        removed = st.queue.remove_by_id(item_id)
        if removed is not None:
            self._state._pending_heard.pop(item_id, None)

    def handle_message(self, msg):
        self._ctx.bind(msg)
        return dispatch(self._ctx, msg)

    def stop(self) -> None:
        self._running.clear()
        self._state._wake.set()
        self._stop_hotkeys()
        self._server.stop()

    def _start_hotkeys(self) -> None:
        """Start the platform's global-hotkey listener. On Windows this spawns an
        in-process RegisterHotKey thread; on macOS it is a no-op (the hotkeyd is a
        separate process)."""
        # Kill-switch: a ~/.sonari/no_hotkeys file (or SONARI_DISABLE_HOTKEYS=1)
        # runs speech-only (no in-process hotkey thread). A FILE flag is honoured
        # by EVERY daemon however it is spawned (hooks inherit their own env, not
        # ours), so it reliably isolates the hotkey thread when diagnosing crashes.
        flag = os.path.join(os.path.expanduser("~"), ".sonari", "no_hotkeys")
        if os.environ.get("SONARI_DISABLE_HOTKEYS") or os.path.exists(flag):
            return
        from sonari.platform import get_platform
        try:
            get_platform().hotkey.start(self._dispatch_hotkey)
        except Exception:  # noqa: BLE001 - hotkeys are non-essential; speech must run
            pass

    def _stop_hotkeys(self) -> None:
        from sonari.platform import get_platform
        try:
            get_platform().hotkey.stop()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            pass

    def _reload_hotkeys(self) -> None:
        """Apply a keymap.json change to the live hotkeys. Runs OFF the daemon lock
        (see the RELOAD_KEYMAP handler) and is serialized by _reload_lock so two
        rapid reloads can't interleave their stop/start cycles. Honors the
        no_hotkeys kill switch, then delegates to the platform backend's reload()
        seam: Windows does a (thread-joined) stop+start; macOS rewrites the resolved
        keymap and reloads the separate hotkeyd process."""
        with self._reload_lock:
            flag = os.path.join(os.path.expanduser("~"), ".sonari", "no_hotkeys")
            if os.environ.get("SONARI_DISABLE_HOTKEYS") or os.path.exists(flag):
                self._stop_hotkeys()
                return
            from sonari.platform import get_platform
            try:
                get_platform().hotkey.reload(self._dispatch_hotkey)
            except Exception:  # noqa: BLE001 - hotkeys are non-essential; speech must run
                pass

    def _dispatch_hotkey(self, message: dict) -> None:
        """A hotkey fire is handled exactly like an inbound socket message.

        MUST hold self._lock around handle_message, identical to the socket path
        (_handle_conn): the hotkey thread mutates shared state (queue, history,
        config) concurrently with the speak loop, so without the lock it races
        -> 'list changed size during iteration' / corruption. handle_message and
        its callees never acquire self._lock (note_spoken/speak run on the speak
        thread), so this is deadlock-free. An enqueue-based action (skip_back /
        jump_waiting) is likewise safe from losing its item to that race.
        """
        try:
            with self._state.transaction():
                self.handle_message(message)
        except Exception:  # noqa: BLE001 - one bad hotkey must not kill the pump
            pass

    def _speak_loop(self) -> None:
        self._running.set()
        while self._running.is_set():
            try:
                self._speak_loop_once()
            except Exception:  # noqa: BLE001 - NOTHING may permanently kill the
                # speak thread. A crash in pop_next/note_spoken/etc. used to leave
                # the daemon alive (earcons kept firing) but mute forever until a
                # restart. Log the traceback (captured by the daemon log) and keep
                # going; a short wait avoids a tight error-spin.
                import sys
                import traceback
                traceback.print_exc(file=sys.stderr)
                self._state._wake.wait(0.1)

    def _signal_speak_failure(self) -> None:
        """An utterance raised (missing TTS extra, synth/playback failure, ...).
        The inner speak-loop handlers swallow it so one bad item can't wedge the
        loop — but for an eyes-free user a swallowed exception is a SILENT no-op,
        the worst outcome (#41). Signal it audibly (error earcon) and log the
        traceback. Never raises — error signaling must not itself re-break the
        loop. Call only from within an active `except` block (print_exc reads the
        handled exception)."""
        try:
            self.speaker.earcon("error_system")   # W6: "Sonari itself failed; content preserved unheard"
        except Exception:  # noqa: BLE001 - signaling failure must not wedge the loop
            pass
        try:
            import sys
            import traceback
            traceback.print_exc(file=sys.stderr)
        except Exception:  # noqa: BLE001 - logging failure must not wedge the loop
            pass

    def _speak_loop_once(self) -> None:
        """One iteration of the speak loop. May raise; _speak_loop contains it.

        The voice plays the SPEAKER session's stream: every pop reads the
        speaker stream's own queue. Background streams accumulate untouched
        until they become the speaker. When the speaker stream is per-session
        STOPPED (⌃⌘S / ⌃⌘M), the loop is held — only a pause-exempt cue
        ("Stopped." / "All stopped.") is voiced — until it is started again.
        SP1: speaker() == foreground(); SP2 keep-going advances speaker()
        independently."""
        fg0 = self.sessions.speaker()
        st0 = self._state._streams.get(fg0)
        if st0 is not None and st0.stopped:
            # Held: scan the speaker stream for a pause-exempt cue; otherwise
            # wait. Pop+claim under the lock, mirroring the normal branch.
            with self._lock:
                fg = self.sessions.speaker()
                st = self._state._streams.get(fg)
                item = st.queue.pop_pause_exempt() if st is not None else None
                self._state._current_item = item
                cancel_epoch = self.speaker.cancel_epoch()
            if item is None:
                self._state._wake.wait(self._poll_interval)
                self._state._wake.clear()
                return
            try:
                if item.audio_path:
                    completed = self.speaker.speak(
                        item.text, audio_path=item.audio_path, cancel_epoch=cancel_epoch)
                else:
                    completed = self.speaker.speak(item.text, cancel_epoch=cancel_epoch)
            except Exception:  # noqa: BLE001 - one bad cue must not wedge the hold
                self._signal_speak_failure()
                completed = False
            self.note_spoken(item, completed)
            return
        # Pop and CLAIM the speaker stream's next item atomically under the lock.
        # speaker() is read here too, so a switch arriving on another connection
        # (also under the lock) is observed consistently. STOP/FLUSH run under this
        # lock, so they can't slip into the gap between pop and claim.
        with self._lock:
            fg = self.sessions.speaker()
            st = self._state._streams.get(fg)
            item = st.queue.pop_next() if st is not None else None
            if item is None and _stream_quiescent(st) and self._state._voice_state == "flowing":
                # KEEP-GOING (M1): the speaker is at its live edge and fully idle.
                # Advance the VOICE (only _speaker) to the longest-waiting eligible
                # background session and pop ITS oldest item — scan+select+set_speaker+
                # pop+claim ALL inside this one lock so a FLUSH/STOP can't race the
                # TOCTOU gap. _foreground is untouched: the workspace never moves on its
                # own (R12/D10). The scan is gated on the voice-global state in the
                # condition above: keep-going advances the voice ONLY while `flowing`
                # (a deliberate quiet-hold / stopped-all suppresses it — R7 "lasting
                # quiet"). Read directly off _state on the hot path.
                next_sess = _select_keep_going(self._state._streams, self.sessions)
                if next_sess is not None:
                    self.sessions.set_speaker(next_sess)
                    st = self._state._streams.get(next_sess)
                    # W13 PRE-ROLL (inside this SAME locked block — M1): the most
                    # frequent voice switch gets the same spearcon cue as a
                    # deliberate jump. On a cache hit, synthesize the ~200ms
                    # folder spearcon, enqueue_front it, and let the pop below
                    # claim IT — the content item stays queued (popped next
                    # iteration, attribution claimed via names_session, so
                    # _attributed_text no longer splices the folder prefix).
                    # The QUEUE, not a local, carries the content across
                    # iterations: FLUSH/STOP semantics are inherited for free.
                    # Miss -> today's splice byte-identically; _spearcon_path
                    # never blocks (a cache stat + non-blocking Popen kick).
                    if st is not None:
                        folder = self.sessions.folder(next_sess)
                        sp = self._spearcon_path(folder)
                        if sp is not None:
                            st.queue.enqueue_front(SpeechItem(
                                id=self._alloc_id(), session=next_sess,
                                kind="prose", text=folder, is_decision=False,
                                mute_exempt=True, names_session=True,
                                audio_path=sp))
                    # pop_next() is guaranteed non-None: _select_keep_going verified
                    # len(queue) > 0 for next_sess inside this same held lock.
                    item = st.queue.pop_next() if st is not None else None
            self._state._current_item = item
            # Capture the speaker's cancel baseline atomically with the claim, so a
            # cancel() arriving during speak() is detected (M2 — the pop->speak gap).
            cancel_epoch = self.speaker.cancel_epoch()
            text = None
            # Snapshot before _attributed_text so we can roll back if a stop interrupts.
            prev = self._state._last_spoken_session
            if item is not None:
                # Compute the attributed text under the lock so _last_spoken_session
                # is updated atomically with the pop — a concurrent JUMP_WAITING or
                # SET_FOREGROUND can't race the attribution read.
                text = self._attributed_text(item)
        if item is None:
            # Foreground stream empty (or no foreground): wait until woken.
            self._state._wake.wait(self._poll_interval)
            self._state._wake.clear()
            return
        try:
            if item.audio_path:
                completed = self.speaker.speak(
                    text, audio_path=item.audio_path, cancel_epoch=cancel_epoch)
            else:
                completed = self.speaker.speak(text, cancel_epoch=cancel_epoch)
        except Exception:  # noqa: BLE001 - one bad utterance must not abort the item
            self._signal_speak_failure()
            completed = False
        requeued = False
        with self._lock:
            # Re-check INSIDE the lock (L2). A FLUSH also runs under this lock and
            # clears the stream's queue; checking outside let a FLUSH land between
            # check and enqueue_front, resurrecting a flushed item. Re-queue when the
            # spoken item's OWN session got stopped mid-utterance (⌃⌘S on the
            # foreground, or ⌃⌘M) so resume picks back up here.
            if not completed and self._stream(item.session).stopped:
                self._state._current_item = None
                self._stream(item.session).queue.enqueue_front(item)
                self._state._last_spoken_session = prev
                requeued = True
            elif completed and not item.mute_exempt:
                # W12 capture: the last COMPLETED content utterance, AS SPOKEN
                # (attributed text, prefix included). mute_exempt chrome (⌃⌘W
                # readouts, jump cues, the repeat playback itself) is excluded —
                # which also makes repeat idempotent. One assignment under the
                # EXISTING tail lock: no new locked region, no gap (M1).
                self._state._last_utterance = (text, item.audio_path)
        if not requeued:
            self.note_spoken(item, completed)

    def _handle_message_guarded(self, msg):
        """Dispatch one socket message under the lock; if the handler asks to block
        for a permission decision, do that AFTER the lock is released. Contained so a
        malformed/buggy message logs a traceback instead of killing the connection
        thread. Returns the reply or None."""
        try:
            with self._state.transaction():
                result = self.handle_message(msg)
        except Exception:  # noqa: BLE001 - one bad message must not drop the connection
            import sys
            import traceback
            traceback.print_exc(file=sys.stderr)
            if isinstance(msg, dict) and msg.get("type") == MsgType.PERMISSION_REQUEST:
                return {"decision": None}   # fail-closed: don't strand the blocking hook for 130s
            return None
        if isinstance(result, dict) and result.get("__await_decision__"):
            try:
                return self._await_permission_decision(
                    result["session"], PERMISSION_WAIT_TIMEOUT)
            except Exception:  # noqa: BLE001 - the wait must never auto-allow on error
                import sys
                import traceback
                traceback.print_exc(file=sys.stderr)
                return {"decision": None}   # fail-closed: fall through to terminal
        return result

    def run(self) -> None:
        ensure_sonari_dir()
        self._token = secrets.token_hex(32)
        port = self._server.bind()
        transport.write_lockfile(
            LOCK_PATH, transport.HOST, port, self._token, os.getpid())
        self._running.set()
        speak_thread = threading.Thread(target=self._speak_loop, daemon=True)
        speak_thread.start()
        self._server.serve()          # accept thread starts after speak (matches original order)
        self._start_hotkeys()
        try:
            while self._running.is_set():
                self._server.join(timeout=0.25)
                if not self._server.is_alive():
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            try:
                os.unlink(LOCK_PATH)
            except FileNotFoundError:
                pass
