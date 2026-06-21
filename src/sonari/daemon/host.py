from __future__ import annotations

import os
import secrets
import threading

from sonari.protocol import MsgType
from sonari.queue import SpeechItem
from sonari.session_stream import SessionStream
from sonari.paths import (
    LOCK_PATH, ensure_sonari_dir,
    INSTALL_RECORD_PATH,
)
from sonari.platform import transport
from sonari.daemon.state import SessionState
from sonari.daemon.context import Ctx
from sonari.daemon.registry import handler, dispatch
from sonari.daemon.server import Server
from sonari.daemon.limits import RATE_MIN, RATE_MAX, MINQUEUE_MIN, MINQUEUE_MAX
from sonari.daemon.features import control  # noqa: F401 — registers @handler decorators
from sonari.daemon.features.control import (
    on_set_rate, on_set_voice, on_set_verbosity,
    on_set_minqueue, on_cycle_verbosity, on_status, on_ping,
)
from sonari.daemon.features import decisions  # noqa: F401 — registers @handler decorators
from sonari.daemon.features.decisions import (
    on_choice, on_plan, on_permission, on_reread_options,
)
from sonari.daemon.features import lifecycle  # noqa: F401 — registers @handler decorators
from sonari.daemon.features.lifecycle import on_set_foreground, on_session_end
from sonari.daemon.features import navigation  # noqa: F401 — registers @handler decorators
from sonari.daemon.features.navigation import on_nav
from sonari.daemon.features import playback  # noqa: F401 — registers @handler decorators
from sonari.daemon.features.playback import (
    on_stop, on_skip, on_pause, on_mute, on_pin_toggle, on_jump_decision,
)
from sonari.daemon.features import focus  # noqa: F401 — registers @handler decorators
from sonari.daemon.features.focus import on_jump_waiting
from sonari.daemon.features import prose  # noqa: F401 — registers @handler decorators
from sonari.daemon.features.prose import on_prose, on_tool, on_earcon, on_flush
from sonari.daemon.features import hotkeys  # noqa: F401 — registers @handler decorators
from sonari.daemon.features.hotkeys import on_reload_keymap


class SpeechDaemon:
    def __init__(self, speaker, sessions, config, raise_service=None) -> None:
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

    # --- Ledger shims (Step 7): storage lives on SessionState. The hot path
    # (speak loop + kernel ops) goes through self._state._X directly; these
    # properties bridge the self._X name for cold-path callers (tests, the
    # concurrency guards, feature modules on the connection thread). 3 are
    # read/write (rebindable scalars); 4 are read-only (mutated in place). ---
    @property
    def _streams(self):
        return self._state._streams

    @property
    def _pending_heard(self):
        return self._state._pending_heard

    @property
    def _paused(self):
        return self._state._paused

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
    def _next_id(self):
        return self._state._next_id

    @_next_id.setter
    def _next_id(self, value):
        self._state._next_id = value

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
            self._state._streams[session] = s
        return s

    def _enqueue(self, session: str, kind: str, text: str, is_decision: bool,
                 entry=None, mute_exempt: bool = False,
                 pause_exempt: bool = False, at_front: bool = False,
                 names_session: bool = False) -> None:
        item = SpeechItem(
            id=self._alloc_id(),
            session=session,
            kind=kind,
            text=text,
            is_decision=is_decision,
            mute_exempt=mute_exempt,
            pause_exempt=pause_exempt,
            names_session=names_session,
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
            self._enqueue(session, "prose", text, False, entry=entry)
        # Background-backlog cue: ONCE per turn, when a NON-foreground, non-muted
        # session's prose reaches its (now non-empty) queue. Debounced via the
        # per-stream flag, re-armed only by reset_for_new_prompt (a new prompt =
        # a new turn) — never per sentence. Decisions carry their own alert earcon,
        # so this is prose-only.
        if (not st.waiting_signaled and not st.muted
                and session != self.sessions.foreground()
                and len(st.queue) > 0):
            self.speaker.earcon("waiting")
            st.waiting_signaled = True

    def _maybe_guide_setup(self, session: str, plugin_version: str) -> None:
        """Speak ONE setup-guidance cue for this session, only when degraded.

        Throttle: at most once per session (recorded whether or not a cue fires).
        Silent when healthy. The check is a few file stats + a version compare
        (no launchctl) and never raises.
        """
        if self._stream(session).guided:
            return
        try:
            state, cue = self._setup_health(plugin_version or "")
        except Exception:  # noqa: BLE001 - guidance must never break a session
            return
        self._stream(session).guided = True
        if state != "ok" and cue:
            self._enqueue(session, "prose", cue, False)

    def _drop_pending(self, items) -> None:
        for it in items:
            self._state._pending_heard.pop(it.id, None)

    def _waiting_target(self, exclude):
        """The background session jump-to-waiting should switch to, or None.

        Considers only streams with a non-empty, non-muted queue (live backlog —
        Stage 3 keys off the queue, not history). A stream holding an unplayed
        decision (choice|plan|permission) ranks ahead of prose-only ones; ties break
        by session insertion order. Excludes *exclude* (the current foreground)."""
        blocked, prose = [], []
        for sess, st in self._state._streams.items():          # insertion-ordered
            if sess == exclude or st.muted or len(st.queue) == 0:
                continue
            (blocked if st.queue.has_decision() else prose).append(sess)
        ordered = blocked + prose
        return ordered[0] if ordered else None

    def _attributed_text(self, item) -> str:
        """item.text, prefixed with the session's folder name when the voice switches
        to a session different from the one last spoken — so the user knows who's
        talking. Never prefixes the very first utterance (last == None), a self-naming
        cue (names_session), or a control cue (mute_exempt). Updates _last_spoken_session.
        Called under self._lock from the speak loop."""
        text = item.text
        if item.names_session:
            # Self-naming cue (e.g. "Jumping to backend." / "Pinned backend."):
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
        with self._lock:
            self._state._current_item = None
            entry = self._state._pending_heard.pop(item.id, None)
            if entry is not None and completed:
                entry.heard = True

    @staticmethod
    def _read_install_record():
        """Return the install.json dict, or None if unreadable/absent. Never raises."""
        import json
        try:
            with open(str(INSTALL_RECORD_PATH), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001 - health check must never raise
            return None

    @staticmethod
    def _launcher_present() -> bool:
        """Delegating shim — logic lives in the platform supervisor backend."""
        from sonari.platform import get_platform
        return get_platform().supervisor.is_installed()

    def _setup_health(self, plugin_version: str):
        """Return (state, cue) where state is one of:
        "ok"            -> fully installed, no version drift   -> cue None
        "not_installed" -> no install.json or launcher (never ran `sonari install`)
        "version_drift" -> installed but plugin_version differs from this session's

        Cheap: a few file stats + a string compare. No launchctl. Never raises.
        The hotkeyd binary is deliberately NOT part of this check so a deliberate
        speech-only user (no swiftc) is never nagged.
        """
        rec = self._read_install_record()
        installed = (rec is not None and self._launcher_present())
        if not installed:
            return ("not_installed",
                    "Sonari is reading aloud. To enable hotkeys and autostart, "
                    "run, slash sonari install.")
        recorded = (rec.get("plugin_version") or "")
        # Only flag drift when BOTH sides are known and differ.
        if plugin_version and recorded and plugin_version != recorded:
            return ("version_drift",
                    "Sonari was updated. Run, slash sonari install, to apply.")
        return ("ok", None)

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

    def _nav(self, session: str, to: str) -> None:
        """Move the per-session message cursor within the ANCHORED response and play
        from there to its end. The anchored response is `nav_turn` (None == the live
        turn); a response jump (`_nav_response`) sets it, a new prompt clears it. If the
        anchored turn was evicted by the rolling cap, fall back to the live turn.

        The cursor indexes the anchored turn's messages, oldest..newest; absent == the
        latest. 'next'/'prev' step one message and CLAMP at the ends (no wrap; at the
        newest, 'next' just re-reads it); 'first'/'last' jump to the start/end. Every
        move cuts current speech, clears the queue, and reads the target message AND
        every later one (seek-and-play). Newly streamed prose enqueues after these."""
        st = self._stream(session)
        if st.nav_turn is not None and st.nav_turn not in self.history.turn_ids(session):
            st.nav_turn = None              # anchored turn evicted -> follow live again
            st.nav_cursor = None
        if st.nav_turn is None:
            ids = self.history.message_ids(session)
        else:
            ids = self.history.message_ids_in_turn(session, st.nav_turn)
        if not ids:
            self._enqueue(session, "prose", "Nothing to navigate yet.", False)
            return
        n = len(ids)
        cur_id = st.nav_cursor
        cur = ids.index(cur_id) if cur_id in ids else n - 1
        if to == "next":
            new = min(cur + 1, n - 1)
        elif to == "prev":
            new = max(cur - 1, 0)
        elif to == "first":
            new = 0
        elif to == "last":
            new = n - 1
        else:
            return
        if new >= n - 1:
            self._stream(session).nav_cursor = None
        else:
            self._stream(session).nav_cursor = ids[new]
        self.speaker.cancel()
        self._drop_pending(self._stream(session).queue.clear())
        # Seek-and-play: enqueue the target item AND every later one.
        for mid in ids[new:]:
            for e in self.history.entries_for_message(session, mid):
                self._enqueue(session, e.kind, e.text, False, entry=e)

    def _nav_response(self, session: str, direction: str) -> None:
        """Response-to-response navigation (Stage 5). Move the turn anchor a whole
        response, read the target response from its start (seek-and-play), and lead with
        a relative orientation cue. Clamps at the oldest/latest. Read-only — replays
        stored text, never re-triggers the agent."""
        st = self._stream(session)
        turns = self.history.turn_ids(session)
        if len(turns) < 2:
            # 0 or 1 navigable responses -> nothing to move between.
            cue = "Nothing to navigate yet." if not turns else "No other response."
            self._enqueue(session, "prose", cue, False, mute_exempt=True)
            return
        # Current anchored index (None anchor == live == the latest turn).
        cur_turn = st.nav_turn
        cur_idx = turns.index(cur_turn) if cur_turn in turns else len(turns) - 1
        if direction == "prev_response":
            new_idx = max(cur_idx - 1, 0)
        else:
            new_idx = min(cur_idx + 1, len(turns) - 1)
        target_turn = turns[new_idx]
        at_newest = (new_idx == len(turns) - 1)
        # Follow live (anchor None) ONLY when the target is the ACTUAL live turn. When the
        # live turn is empty (FLUSH->first-prose window) it is excluded from turn_ids, so
        # the newest navigable turn is NOT the live turn — pin the anchor to it instead of
        # None (which would point at the empty live turn and dead-end within-nav).
        follow_live = at_newest and target_turn == self.history.current_turn(session)
        st.nav_turn = None if follow_live else target_turn
        # Relative orientation cue; boundary cues take precedence (Nima's decision).
        # "Back to the latest." fires at the newest navigable response, live or not.
        if at_newest:
            cue = "Back to the latest."
        elif new_idx == 0:
            cue = "Oldest response."
        else:
            back = (len(turns) - 1) - new_idx
            cue = "{0} response{1} back.".format(back, "" if back == 1 else "s")
        mids = self.history.message_ids_in_turn(session, target_turn)
        # Anchor the cursor at the START of the target response; None == follow live.
        st.nav_cursor = None if follow_live else (mids[0] if mids else None)
        self.speaker.cancel()
        self._drop_pending(st.queue.clear())
        self._enqueue(session, "prose", cue, False, mute_exempt=True)
        for mid in mids:
            for e in self.history.entries_for_message(session, mid):
                self._enqueue(session, e.kind, e.text, False, entry=e)

    def _resume(self) -> None:
        """Clear pause and wake the speak loop. The interrupted utterance was
        already re-queued at the front by the speak loop when its speak() returned
        not-completed during the pause, so resume picks back up where it stopped."""
        self._state._paused.clear()
        self._state._wake.set()

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
            self.speaker.earcon("error")
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

        The voice plays the FOREGROUND session's stream: every pop reads the
        foreground stream's own queue. Background streams accumulate untouched
        until they become foreground."""
        if self._state._paused.is_set():
            # Play/pause: the loop is held, but a pause-exempt cue ("Paused.") must
            # still be voiced. Scan the foreground stream's queue for one; otherwise
            # hold. Pop+claim under the lock, mirroring the normal branch.
            with self._lock:
                fg = self.sessions.foreground()
                st = self._state._streams.get(fg)
                item = st.queue.pop_pause_exempt() if st is not None else None
                self._state._current_item = item
                cancel_epoch = self.speaker.cancel_epoch()
            if item is None:
                self._state._wake.wait(self._poll_interval)
                self._state._wake.clear()
                return
            try:
                completed = self.speaker.speak(item.text, cancel_epoch=cancel_epoch)
            except Exception:  # noqa: BLE001 - one bad cue must not wedge the pause
                self._signal_speak_failure()
                completed = False
            self.note_spoken(item, completed)
            return
        # Pop and CLAIM the foreground stream's next item atomically under the lock.
        # foreground() is read here too, so a switch arriving on another connection
        # (also under the lock) is observed consistently. PAUSE/MUTE/FLUSH run under
        # this lock, so they can't slip into the gap between pop and claim.
        with self._lock:
            fg = self.sessions.foreground()
            st = self._state._streams.get(fg)
            item = st.queue.pop_next() if st is not None else None
            self._state._current_item = item
            # Capture the speaker's cancel baseline atomically with the claim, so a
            # cancel() arriving during speak() is detected (M2 — the pop->speak gap).
            cancel_epoch = self.speaker.cancel_epoch()
            ist = self._state._streams.get(item.session) if item is not None else None
            muted = (item is not None
                     and ist is not None and ist.muted
                     and not item.mute_exempt)
            text = None
            # Snapshot before _attributed_text so we can roll back if pause interrupts.
            prev = self._state._last_spoken_session
            if muted:
                # Muted session: drop without speaking; release the claim.
                self._state._current_item = None
                self._state._pending_heard.pop(item.id, None)
            elif item is not None:
                # Compute the attributed text under the lock so _last_spoken_session
                # is updated atomically with the pop — a concurrent JUMP_WAITING or
                # SET_FOREGROUND can't race the attribution read.
                text = self._attributed_text(item)
        if item is None:
            # Foreground stream empty (or no foreground): wait until woken.
            self._state._wake.wait(self._poll_interval)
            self._state._wake.clear()
            return
        if muted:
            return
        try:
            completed = self.speaker.speak(text, cancel_epoch=cancel_epoch)
        except Exception:  # noqa: BLE001 - one bad utterance must not abort the item
            self._signal_speak_failure()
            completed = False
        requeued = False
        with self._lock:
            # Re-check pause INSIDE the lock (L2). A FLUSH also runs under this lock
            # and clears pause + the stream's queue; checking pause outside let a
            # FLUSH land between check and enqueue_front, resurrecting a flushed item.
            if not completed and self._state._paused.is_set():
                # A pause interrupted this utterance: re-queue it at the front of ITS
                # OWN stream so resume picks back up here, and KEEP its _pending_heard
                # entry (don't note_spoken) so the eventual replay records it as heard.
                # Roll back the _last_spoken_session commit from _attributed_text so
                # the re-popped item on resume sees the pre-switch state and re-adds
                # the folder prefix correctly (pause-attribution-drop regression).
                self._state._current_item = None
                self._stream(item.session).queue.enqueue_front(item)
                self._state._last_spoken_session = prev
                requeued = True
        if not requeued:
            self.note_spoken(item, completed)

    def _handle_message_guarded(self, msg):
        """Dispatch one socket message under the lock, contained so a malformed or
        buggy message logs a traceback instead of silently killing the connection
        thread (mirrors the _dispatch_hotkey guard). Returns the reply or None."""
        try:
            with self._state.transaction():
                return self.handle_message(msg)
        except Exception:  # noqa: BLE001 - one bad message must not drop the connection
            import sys
            import traceback
            traceback.print_exc(file=sys.stderr)
            return None

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
