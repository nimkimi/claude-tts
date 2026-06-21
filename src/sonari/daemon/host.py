from __future__ import annotations

import os
import secrets
import socket
import threading

from sonari.protocol import MsgType, encode, decode
from sonari.queue import SpeechItem
from sonari.config import save_config
from sonari.session_stream import SessionStream
from sonari.paths import (
    LOCK_PATH, ensure_sonari_dir,
    INSTALL_RECORD_PATH,
)
from sonari.platform import transport
from sonari.daemon.state import SessionState
from sonari.daemon.context import Ctx
from sonari.daemon.registry import handler, dispatch


RATE_MIN = 100
RATE_MAX = 400

# Min-queue batching: how many prose items must accumulate before they are read.
# 1 == read each item as it arrives (the default, unchanged behaviour).
MINQUEUE_MIN = 1
MINQUEUE_MAX = 10

# Cap on concurrent connection-handler threads. Legitimate clients are short-lived
# (one request each), so this bound is generous; it just stops a misbehaving or
# hostile peer from leaking unbounded threads by opening many connections.
_MAX_CONN_THREADS = 32


class SpeechDaemon:
    def __init__(self, speaker, sessions, config, raise_service=None) -> None:
        self.speaker = speaker
        self.sessions = sessions
        self.config = config
        self._streams: "dict[str, SessionStream]" = {}
        self._next_id = 0
        self._running = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._state = SessionState(self._lock)
        self._ctx = Ctx(self)
        self._server = None
        self._token = None
        self._poll_interval = 0.1
        from sonari.history import SessionHistory
        self.history = SessionHistory(cap=int(config.get("history_cap", 200)))
        self._backlog_cap = int(config.get("backlog_cap", 200))
        self._pending_heard: dict = {}            # SpeechItem.id -> HistoryEntry
        self._paused = threading.Event()          # play/pause: set == speech halted
        self._current_item = None                 # item being spoken right now
        self._conn_sem = threading.BoundedSemaphore(_MAX_CONN_THREADS)
        self._reload_lock = threading.Lock()      # serializes off-lock hotkey reloads
        self._last_spoken_session = None          # for folder attribution on switch
        self.raise_service = raise_service        # lazily built on first jump

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
        self._next_id += 1
        return self._next_id

    def _stream(self, session: str) -> SessionStream:
        s = self._streams.get(session)
        if s is None:
            s = SessionStream(queue_cap=self._backlog_cap)
            self._streams[session] = s
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
            self._pending_heard[item.id] = entry
        if at_front:
            st.queue.enqueue_front(item)
        else:
            evicted = st.queue.enqueue(item)
            if evicted is not None:
                self._drop_pending([evicted])
        self._wake.set()

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
            self._pending_heard.pop(it.id, None)

    def _waiting_target(self, exclude):
        """The background session jump-to-waiting should switch to, or None.

        Considers only streams with a non-empty, non-muted queue (live backlog —
        Stage 3 keys off the queue, not history). A stream holding an unplayed
        decision (choice|plan|permission) ranks ahead of prose-only ones; ties break
        by session insertion order. Excludes *exclude* (the current foreground)."""
        blocked, prose = [], []
        for sess, st in self._streams.items():          # insertion-ordered
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
            self._last_spoken_session = item.session
        elif not item.mute_exempt:
            if (self._last_spoken_session is not None
                    and item.session != self._last_spoken_session):
                folder = self.sessions.folder(item.session)
                if folder:
                    text = "{0}. {1}".format(folder, item.text)
            self._last_spoken_session = item.session
        return text

    def note_spoken(self, item, completed: bool) -> None:
        """Speak-loop bookkeeping: confirm (or decline) the heard-marker for a
        finished utterance, and release the current-item claim."""
        with self._lock:
            self._current_item = None
            entry = self._pending_heard.pop(item.id, None)
            if entry is not None and completed:
                entry.heard = True

    @staticmethod
    def _choice_text(msg) -> str:
        parts = []
        for q in msg.get("questions", []) or []:
            qtext = q.get("question", "") if isinstance(q, dict) else str(q)
            multi = bool(isinstance(q, dict) and q.get("multiSelect"))
            opts = q.get("options", []) if isinstance(q, dict) else []
            segs = []
            for i, o in enumerate(opts, 1):
                if isinstance(o, dict):
                    label = o.get("label", "")
                    desc = (o.get("description") or "").strip()
                else:
                    label, desc = str(o), ""
                if not label:
                    continue   # keep numbering aligned with the TUI's digits
                seg = "Option {0}: {1}.".format(i, label)
                if desc:
                    seg += " {0}{1}".format(
                        desc, "" if desc.endswith((".", "!", "?")) else ".")
                segs.append(seg)
            head = qtext
            if multi:
                head = "{0}{1}".format(
                    (qtext + " ") if qtext else "",
                    "This is a multi-select; you can pick more than one.")
            if head and segs:
                parts.append("{0} {1}".format(head, " ".join(segs)))
            elif segs:
                parts.append(" ".join(segs))
            elif head:
                parts.append(head)
        return " ".join(parts) if parts else "A question needs your answer."

    @staticmethod
    def _plan_text(msg) -> str:
        text = (msg.get("text") or "").strip()
        if text:
            return "Plan ready. {0}".format(text)
        return "A plan is ready for your review."

    @staticmethod
    def _permission_text(msg) -> str:
        # The 'permission' earcon already signals approval is needed; speak the
        # pending action, else the human-readable message, else a generic cue.
        action = (msg.get("action") or "").strip()
        if action:
            return action
        message = (msg.get("message") or "").strip()
        return message if message else "Permission needed."

    def _selection_cue(self, session: str, verbosity: str) -> str:
        if verbosity != "everything":
            return ""
        cue = "Press the option's number to choose, or Escape to cancel."
        st = self._stream(session)
        if not st.warned_immediate:
            st.warned_immediate = True
            cue += " Selecting is immediate."
        return cue

    @staticmethod
    def _choice_notes(msg) -> str:
        notes = []
        questions = msg.get("questions", []) or []
        if any(isinstance(q, dict) and q.get("multiSelect") for q in questions):
            notes.append(
                "Select multiple: press each number, or Space on the "
                "highlighted item, then Enter to confirm."
            )
        if any(
            isinstance(q, dict) and len(q.get("options", []) or []) > 9
            for q in questions
        ):
            notes.append("More than nine options; use arrow keys for ten and up.")
        return " ".join(notes)

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

    # ------------------------------------------------------------------ #
    # Prose family handlers (Task 3.2)                                     #
    # ------------------------------------------------------------------ #

    def _on_prose(self, msg):
        session = msg.get("session", "")
        verbosity = self.config.get("verbosity", "everything")
        final = msg.get("final", False)
        a = self._stream(session).assembler
        chunks = a.feed(msg.get("delta", ""), msg.get("index", 0), final)
        if chunks:
            from sonari.assembler import PARAGRAPH_BREAK
            # The flip: every non-quiet session buffers its OWN prose into its
            # OWN stream (the speak loop plays only the foreground stream). The
            # old _may_speak gate + captured-drop are gone — background output
            # accumulates instead of being lost.
            speak = verbosity != "quiet"
            for chunk in chunks:
                if chunk is PARAGRAPH_BREAK:
                    # A blank-line boundary: start a new message group so the
                    # nav cursor treats each paragraph as its own 'item'.
                    self.history.end_message(session)
                    continue
                entry = self.history.record(session, "prose", chunk)
                if speak:
                    self._buffer_prose(session, chunk, entry)
        if final:
            # `final` marks the end of ONE assistant text block, not the whole
            # turn — the buffer is flushed at the real turn boundary (turn_done)
            # and when the threshold is hit, so it is NOT flushed here.
            self.history.end_message(session)
            self._stream(session).options = None
        return None

    def _on_tool(self, msg):
        session = msg.get("session", "")
        verbosity = self.config.get("verbosity", "everything")
        if verbosity == "everything":
            tool = msg.get("tool", "")
            summary = (msg.get("summary") or "").strip()
            text = summary if summary else "Running {0}.".format(tool)
            # Keep textual order: read prose that preceded this tool call first.
            self._flush_prose_buffer(session)
            self._enqueue(session, "tool_announce", text, False)
        return None

    def _on_earcon(self, msg):
        session = msg.get("session", "")
        # Instant: the Windows earcon backend plays on a separate audio path
        # that mixes with the speech, so it no longer cuts the reading.
        kind = msg.get("kind", "")
        self.speaker.earcon(kind)
        if kind == "turn_done":
            # End-of-turn boundary: flush any sub-threshold buffered prose so
            # it is not silently dropped when the assistant produces fewer items
            # than the minqueue threshold.
            self._flush_prose_buffer(session)
        return None

    def _on_flush(self, msg):
        session = msg.get("session", "")
        st = self._stream(session)
        self._drop_pending(st.queue.clear())
        cur = self._current_item
        # Cut the current utterance on a new prompt: same-session (the new prompt
        # supersedes the old reply) OR a cross-session switch where this prompt's
        # session is now the foreground (pin-aware) — so the voice moves to it
        # immediately instead of finishing the old session's sentence (§4.2
        # cut-on-switch). SESSION_START sends no FLUSH, so a bare new session
        # never cuts.
        if cur is not None and (cur.session == session
                                or self.sessions.foreground() == session):
            self.speaker.cancel()
        st.reset_for_new_prompt()
        # Stage 4: a new prompt opens a NEW TURN and KEEPS the prior turn's
        # transcript (persistent, navigable in Stage 5). reset_for_new_prompt()
        # already cleared live playback (queue, assembler, nav_cursor -> snap to
        # live edge); history is no longer wiped here. SESSION_END still clears it.
        self.history.start_turn(session)
        # A new prompt is a user action -> auto-resume from pause.
        self._paused.clear()
        self._wake.set()
        return None

    # ------------------------------------------------------------------ #
    # Decisions + navigation family handlers (Task 3.3)                   #
    # ------------------------------------------------------------------ #

    def _on_choice(self, msg):
        session = msg.get("session", "")
        verbosity = self.config.get("verbosity", "everything")
        text = self._choice_text(msg)
        extras = [e for e in (
            self._choice_notes(msg),
            self._selection_cue(session, verbosity),
        ) if e]
        if extras:
            text = "{0} {1}".format(text, " ".join(extras))
        self._stream(session).options = text
        entry = self.history.record(session, "choice", text)
        self.history.end_message(session)
        # The flip: gating moved to playback. Every session enqueues its own
        # decision into its own stream; the foreground-driven loop voices it.
        self._flush_prose_buffer(session)   # prose before the question
        self._enqueue(session, "choice", text, True, entry=entry)
        return None

    def _on_plan(self, msg):
        session = msg.get("session", "")
        verbosity = self.config.get("verbosity", "everything")
        text = self._plan_text(msg)
        cue = self._selection_cue(session, verbosity)
        if cue:
            text = "{0} {1}".format(text, cue)
        self._stream(session).options = text
        entry = self.history.record(session, "plan", text)
        self.history.end_message(session)
        # The flip: enqueue unconditionally into this session's own stream.
        self._flush_prose_buffer(session)   # prose before the plan
        self._enqueue(session, "plan", text, True, entry=entry)
        return None

    def _on_permission(self, msg):
        session = msg.get("session", "")
        verbosity = self.config.get("verbosity", "everything")
        text = self._permission_text(msg)
        cue = self._selection_cue(session, verbosity)
        if cue:
            text = "{0} {1}".format(text, cue)
        self._stream(session).options = text
        entry = self.history.record(session, "permission", text)
        self.history.end_message(session)
        # The flip: enqueue unconditionally into this session's own stream.
        self._flush_prose_buffer(session)   # prose before the permission ask
        self._enqueue(session, "permission", text, True, entry=entry)
        return None

    def _on_reread_options(self, msg):
        fg = self.sessions.foreground()
        if fg is None:
            return None
        st = self._streams.get(fg)
        text = st.options if st is not None else None
        if text:
            self._enqueue(fg, "choice", text, False)
        else:
            self._enqueue(fg, "prose", "No options right now.", False)
        return None

    def _on_nav(self, msg):
        fg = self.sessions.foreground()
        if fg is None:
            return None
        to = msg.get("to", "prev")
        if to in ("prev_response", "next_response"):
            self._nav_response(fg, to)
        else:
            self._nav(fg, to)
        return None

    # ------------------------------------------------------------------ #
    # Playback + focus family handlers (Task 3.4)                         #
    # ------------------------------------------------------------------ #

    def _on_stop(self, msg):
        # Stop acts on the FOREGROUND stream only — clearing every stream would
        # wipe a background session's backlog the user hasn't heard yet (the 2a
        # global-STOP clobber). Background streams accumulate untouched.
        fg = self.sessions.foreground()
        st = self._streams.get(fg)
        if st is not None:
            self._drop_pending(st.queue.clear())
        self.speaker.cancel()
        return None

    def _on_skip(self, msg):
        cur = self._current_item
        if cur is not None:
            entry = self._pending_heard.get(cur.id)
            if entry is not None:
                entry.heard = True
        self.speaker.cancel()
        return None

    def _on_pause(self, msg):
        # Temporary play/pause. Pause stops the current utterance and holds the
        # loop; resume re-speaks the interrupted item so it picks back up. Also
        # auto-cleared by a new prompt (see the FLUSH handler).
        fg = self.sessions.foreground()
        if self._paused.is_set():
            # Resuming: voice "Resumed." FIRST (at the front, ahead of the
            # interrupted utterance that was re-queued there on pause), then
            # continue. mute_exempt so the control cue is always heard.
            if fg is not None:
                self._enqueue(fg, "prose", "Resumed.", False,
                              mute_exempt=True, at_front=True)
            self._resume()
        else:
            self._paused.set()
            # cancel() bumps the speaker's epoch, so even an utterance still
            # mid-synthesis aborts. The speak loop re-queues the interrupted
            # item (it sees completed=False while paused), so we don't capture
            # it here — which also avoids replaying an already-finished item.
            self.speaker.cancel()
            # The hold silences the queue, so "Paused." is pause_exempt (the
            # paused branch of the speak loop voices it) and mute_exempt (always
            # heard). pop_pause_exempt finds it past the re-queued interrupted item.
            if fg is not None:
                self._enqueue(fg, "prose", "Paused.", False,
                              mute_exempt=True, pause_exempt=True)
        return None

    def _on_mute(self, msg):
        # Toggle a sticky per-session mute. Earcons still fire (alerts), and the
        # "muted"/"unmuted" confirmation is spoken (the mute-on case is exempt).
        fg = self.sessions.foreground()
        if fg is None:
            return None
        st = self._stream(fg)
        if st.muted:
            st.muted = False
            self._enqueue(fg, "prose", "Session unmuted.", False)
        else:
            st.muted = True
            self._drop_pending(st.queue.clear())
            cur = self._current_item
            if cur is not None and cur.session == fg:
                self.speaker.cancel()
            self._enqueue(fg, "prose", "Session muted.", False, mute_exempt=True)
        return None

    def _on_pin_toggle(self, msg):
        # Pin the voice to the current (last-prompt) session, or unpin it.
        # The pin overrides "foreground", so a later SET_FOREGROUND from another
        # session can't steal the voice. Confirmation is mute_exempt so the user
        # always hears it; the no-session case has nothing to speak through, so
        # it is an error earcon only.
        action, folder = self.sessions.pin_toggle()
        if action == "none":
            self.speaker.earcon("error")
            return None
        fg = self.sessions.foreground()
        if action == "pinned":
            text = "Pinned {0}.".format(folder) if folder else "Pinned."
        else:
            text = "Auto."
        self._enqueue(fg, "prose", text, False, mute_exempt=True,
                      names_session=(action == "pinned"))
        return None

    def _on_jump_waiting(self, msg):
        fg = self.sessions.foreground()
        target = self._waiting_target(exclude=fg)
        if target is None:
            # Nothing waiting: say so (mute_exempt so it's always heard). With no
            # foreground to speak through, fall back to an error earcon.
            if fg is not None:
                self._enqueue(fg, "prose", "No session waiting.", False,
                              mute_exempt=True)
            else:
                self.speaker.earcon("error")
            return None
        # Explicit move: clear any pin, switch the VOICE (not OS focus) to the
        # target, cut the current utterance so the switch is immediate, and lead
        # with a spoken folder label. The foreground-driven loop then drains the
        # target's accumulated backlog.
        self.sessions.focus(target)
        self.speaker.cancel()
        folder = self.sessions.folder(target)
        identity = self.sessions.identity(target)
        will_raise = self._raise().will_attempt(identity)
        # Bump the jump generation on EVERY jump, not only raising ones. A jump to
        # a non-followable target must still advance the generation so a prior
        # in-flight raise sees itself superseded (its _is_current(genOld) check
        # returns False -> no-ops). If this lived inside `if will_raise:`, a
        # non-raising jump B would leave the generation pinned at A's value, and a
        # slow raise(A) would yank focus back to A while the voice is on B (spec
        # §4.5 lines 191-201).
        gen = self._raise().bump_generation()
        base = ("Jumping to {0}.".format(folder) if folder
                else "Jumping to another session.")
        if not will_raise:
            base += " Bring it forward to type."
        self._enqueue(target, "prose", base, False,
                      mute_exempt=True, at_front=True, names_session=True)
        if will_raise:
            self._raise().raise_async(
                identity, gen,
                on_failure=lambda s=target, f=folder: self._raise_failed(s, f))
        return None

    def _on_jump_decision(self, msg):
        # Mark the cancelled current item heard and clear heard-markers of the
        # skipped prose items so they don't linger in unheard() (M6).
        cur = self._current_item
        if cur is not None:
            entry = self._pending_heard.get(cur.id)
            if entry is not None:
                entry.heard = True
        fg = self.sessions.foreground()
        st = self._streams.get(fg)
        if st is not None:
            self._drop_pending(st.queue.jump_to_decision())
        self.speaker.cancel()
        return None

    # ------------------------------------------------------------------ #
    # Lifecycle family handlers (Task 3.5)                                #
    # ------------------------------------------------------------------ #

    def _on_set_foreground(self, msg):
        t = msg.get("type")
        session = msg.get("session", "")
        self.sessions.set_foreground(session, cwd=msg.get("cwd"))
        if t == MsgType.SESSION_START:
            self.sessions.register(session, cwd=msg.get("cwd"))
            from sonari.sessions import Identity
            self.sessions.set_identity(session, Identity(
                term_program=msg.get("term_program", ""),
                tty=msg.get("tty", ""),
                iterm_session_id=msg.get("iterm_session_id", ""),
            ))
            self._maybe_guide_setup(session, msg.get("plugin_version", ""))
        return None

    def _on_session_end(self, msg):
        session = msg.get("session", "")
        self.sessions.unregister(session)
        st = self._streams.get(session)
        if st is not None:
            self._drop_pending(st.queue.clear())
        self.history.reset(session)
        self._streams.pop(session, None)
        return None

    # ------------------------------------------------------------------ #
    # Hotkeys family handlers (Task 3.5)                                  #
    # ------------------------------------------------------------------ #

    def _on_reload_keymap(self, msg):
        # keymap.json changed (e.g. an unbind): re-register hotkeys so it takes
        # effect without a daemon restart. Run it OFF the daemon lock: this
        # handler is invoked while holding self._lock, but _reload_hotkeys joins
        # the Windows hotkey pump thread, which itself needs self._lock to
        # dispatch a fire. Joining under the lock could stall the daemon up to
        # the join timeout and, on timeout, leave an orphaned thread that
        # re-creates the H2 dark-hotkey race. A short-lived thread does the
        # reload lock-free (and _reload_lock serializes concurrent reloads).
        threading.Thread(target=self._reload_hotkeys,
                         name="sonari-keymap-reload", daemon=True).start()
        return None

    # ------------------------------------------------------------------ #
    # Control family handlers (Task 3.5)                                  #
    # ------------------------------------------------------------------ #

    def _on_set_rate(self, msg):
        is_delta = "delta" in msg
        if is_delta:
            try:
                cur = int(self.config.get("rate", 200))
                rate = max(RATE_MIN, min(RATE_MAX, cur + int(msg.get("delta", 0))))
            except (ValueError, TypeError):
                return None
        else:
            # Validate/clamp the absolute rate just like the delta branch — an
            # unvalidated value here is persisted to disk and breaks synthesis.
            try:
                rate = max(RATE_MIN, min(RATE_MAX, int(msg.get("rate"))))
            except (TypeError, ValueError):
                return None
        self.config["rate"] = rate
        self.speaker.set_rate(rate)
        save_config(self.config)
        if is_delta:
            fg = self.sessions.foreground()
            if fg is not None:
                self._enqueue(fg, "prose", "Rate {0}.".format(rate), False)
        return None

    def _on_set_voice(self, msg):
        voice = msg.get("voice")
        self.config["voice"] = voice
        self.speaker.set_voice(voice)
        save_config(self.config)
        return None

    def _on_set_verbosity(self, msg):
        self.config["verbosity"] = msg.get("verbosity")
        save_config(self.config)
        return None

    def _on_set_minqueue(self, msg):
        # Validate/clamp before persisting — a bad value reaches disk and would
        # wedge prose buffering on every turn (mirrors the SET_RATE guard).
        try:
            n = max(MINQUEUE_MIN, min(MINQUEUE_MAX, int(msg.get("minqueue"))))
        except (TypeError, ValueError):
            return None
        self.config["minqueue"] = n
        save_config(self.config)
        return None

    def _on_cycle_verbosity(self, msg):
        order = ["everything", "medium", "quiet"]
        cur = self.config.get("verbosity", "everything")
        if cur in order:
            nxt = order[(order.index(cur) + 1) % len(order)]
        else:
            nxt = order[0]
        self.config["verbosity"] = nxt
        save_config(self.config)
        fg = self.sessions.foreground()
        if fg is not None:
            self._enqueue(fg, "prose", "Verbosity {0}.".format(nxt), False)
        return None

    def _on_status(self, msg):
        return {
            "verbosity": self.config.get("verbosity"),
            "rate": self.config.get("rate"),
            "voice": self.config.get("voice"),
            "foreground": self.sessions.foreground(),
            "queue_len": sum(len(st.queue) for st in self._streams.values()),
            "minqueue": self.config.get("minqueue"),
        }

    def _on_ping(self, msg):
        return {"ok": True}

    def stop(self) -> None:
        self._running.clear()
        self._wake.set()
        self._stop_hotkeys()
        srv = self._server
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass

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
        self._paused.clear()
        self._wake.set()

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
                self._wake.wait(0.1)

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
            traceback.print_exc(file=sys.stderr)
        except Exception:  # noqa: BLE001 - logging failure must not wedge the loop
            pass

    def _speak_loop_once(self) -> None:
        """One iteration of the speak loop. May raise; _speak_loop contains it.

        The voice plays the FOREGROUND session's stream: every pop reads the
        foreground stream's own queue. Background streams accumulate untouched
        until they become foreground."""
        if self._paused.is_set():
            # Play/pause: the loop is held, but a pause-exempt cue ("Paused.") must
            # still be voiced. Scan the foreground stream's queue for one; otherwise
            # hold. Pop+claim under the lock, mirroring the normal branch.
            with self._lock:
                fg = self.sessions.foreground()
                st = self._streams.get(fg)
                item = st.queue.pop_pause_exempt() if st is not None else None
                self._current_item = item
                cancel_epoch = self.speaker.cancel_epoch()
            if item is None:
                self._wake.wait(self._poll_interval)
                self._wake.clear()
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
            st = self._streams.get(fg)
            item = st.queue.pop_next() if st is not None else None
            self._current_item = item
            # Capture the speaker's cancel baseline atomically with the claim, so a
            # cancel() arriving during speak() is detected (M2 — the pop->speak gap).
            cancel_epoch = self.speaker.cancel_epoch()
            ist = self._streams.get(item.session) if item is not None else None
            muted = (item is not None
                     and ist is not None and ist.muted
                     and not item.mute_exempt)
            text = None
            # Snapshot before _attributed_text so we can roll back if pause interrupts.
            prev = self._last_spoken_session
            if muted:
                # Muted session: drop without speaking; release the claim.
                self._current_item = None
                self._pending_heard.pop(item.id, None)
            elif item is not None:
                # Compute the attributed text under the lock so _last_spoken_session
                # is updated atomically with the pop — a concurrent JUMP_WAITING or
                # SET_FOREGROUND can't race the attribution read.
                text = self._attributed_text(item)
        if item is None:
            # Foreground stream empty (or no foreground): wait until woken.
            self._wake.wait(self._poll_interval)
            self._wake.clear()
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
            if not completed and self._paused.is_set():
                # A pause interrupted this utterance: re-queue it at the front of ITS
                # OWN stream so resume picks back up here, and KEEP its _pending_heard
                # entry (don't note_spoken) so the eventual replay records it as heard.
                # Roll back the _last_spoken_session commit from _attributed_text so
                # the re-popped item on resume sees the pre-switch state and re-adds
                # the folder prefix correctly (pause-attribution-drop regression).
                self._current_item = None
                self._stream(item.session).queue.enqueue_front(item)
                self._last_spoken_session = prev
                requeued = True
        if not requeued:
            self.note_spoken(item, completed)

    def _handle_conn(self, conn) -> None:
        try:
            buf = b""
            with conn:
                conn.settimeout(5.0)
                # --- token handshake: the first newline-terminated line must
                # equal the daemon's session token, or the peer is dropped. ---
                while b"\n" not in buf:
                    try:
                        data = conn.recv(4096)
                    except (OSError, socket.timeout):
                        return
                    if not data:
                        return
                    buf += data
                token_line, buf = buf.split(b"\n", 1)
                if token_line.decode("utf-8", "replace") != self._token:
                    return  # reject unauthenticated peer
                while self._running.is_set():
                    # Process any complete messages already buffered (e.g. a
                    # message that arrived in the same packet as the token).
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            msg = decode(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        reply = self._handle_message_guarded(msg)
                        if reply is not None:
                            try:
                                conn.sendall(encode(reply))
                            except OSError:
                                return
                    try:
                        data = conn.recv(4096)
                    except (OSError, socket.timeout):
                        return
                    if not data:
                        return
                    buf += data
        except OSError:
            return

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

    def _handle_conn_guarded(self, conn) -> None:
        """Run _handle_conn, contain any crash (log it, don't die silently), and
        always release the concurrency permit so capacity recovers."""
        try:
            self._handle_conn(conn)
        except Exception:  # noqa: BLE001 - a handler crash must be logged, not silent
            import sys
            import traceback
            traceback.print_exc(file=sys.stderr)
        finally:
            self._conn_sem.release()

    def _spawn_conn_handler(self, conn) -> bool:
        """Spawn a handler thread for *conn* if under the concurrency cap; else
        drop (close) the connection. Returns True iff a handler was spawned."""
        if not self._conn_sem.acquire(blocking=False):
            try:
                conn.close()
            except OSError:
                pass
            return False
        try:
            th = threading.Thread(target=self._handle_conn_guarded, args=(conn,), daemon=True)
            th.start()
        except Exception:  # noqa: BLE001 - thread creation can fail (resource limits)
            # The handler that would release the permit never ran: release it here
            # and drop the connection, else this slot leaks forever (M8).
            self._conn_sem.release()
            try:
                conn.close()
            except OSError:
                pass
            return False
        return True

    def _accept_loop(self) -> None:
        srv = self._server
        while self._running.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            self._spawn_conn_handler(conn)

    def run(self) -> None:
        ensure_sonari_dir()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((transport.HOST, 0))
        srv.listen(16)
        port = srv.getsockname()[1]
        self._token = secrets.token_hex(32)
        transport.write_lockfile(
            LOCK_PATH, transport.HOST, port, self._token, os.getpid())
        self._server = srv
        self._running.set()

        speak_thread = threading.Thread(target=self._speak_loop, daemon=True)
        accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        speak_thread.start()
        accept_thread.start()
        self._start_hotkeys()

        try:
            while self._running.is_set():
                accept_thread.join(timeout=0.25)
                if not accept_thread.is_alive():
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            try:
                srv.close()
            except OSError:
                pass
            try:
                os.unlink(LOCK_PATH)
            except FileNotFoundError:
                pass


# ------------------------------------------------------------------ #
# Registry thunks — prose family (Task 3.2)                           #
# Grouped for the Step-5 lift into features/prose.py                  #
# ------------------------------------------------------------------ #

@handler(MsgType.PROSE)
def _h_prose(ctx, msg):
    return ctx.host._on_prose(msg)


@handler(MsgType.TOOL)
def _h_tool(ctx, msg):
    return ctx.host._on_tool(msg)


@handler(MsgType.EARCON)
def _h_earcon(ctx, msg):
    return ctx.host._on_earcon(msg)


@handler(MsgType.FLUSH)
def _h_flush(ctx, msg):
    return ctx.host._on_flush(msg)


# ------------------------------------------------------------------ #
# Registry thunks — decisions family (Task 3.3)                       #
# Grouped for the Step-5 lift into features/decisions.py              #
# ------------------------------------------------------------------ #

@handler(MsgType.CHOICE)
def _h_choice(ctx, msg):
    return ctx.host._on_choice(msg)


@handler(MsgType.PLAN)
def _h_plan(ctx, msg):
    return ctx.host._on_plan(msg)


@handler(MsgType.PERMISSION)
def _h_permission(ctx, msg):
    return ctx.host._on_permission(msg)


@handler(MsgType.REREAD_OPTIONS)
def _h_reread_options(ctx, msg):
    return ctx.host._on_reread_options(msg)


# ------------------------------------------------------------------ #
# Registry thunks — navigation family (Task 3.3)                      #
# Grouped for the Step-5 lift into features/navigation.py             #
# ------------------------------------------------------------------ #

@handler(MsgType.NAV)
def _h_nav(ctx, msg):
    return ctx.host._on_nav(msg)


# ------------------------------------------------------------------ #
# Registry thunks — playback family (Task 3.4)                        #
# Grouped for the Step-5 lift into features/playback.py               #
# ------------------------------------------------------------------ #

@handler(MsgType.STOP)
def _h_stop(ctx, msg):
    return ctx.host._on_stop(msg)


@handler(MsgType.SKIP)
def _h_skip(ctx, msg):
    return ctx.host._on_skip(msg)


@handler(MsgType.PAUSE)
def _h_pause(ctx, msg):
    return ctx.host._on_pause(msg)


@handler(MsgType.MUTE)
def _h_mute(ctx, msg):
    return ctx.host._on_mute(msg)


@handler(MsgType.PIN_TOGGLE)
def _h_pin_toggle(ctx, msg):
    return ctx.host._on_pin_toggle(msg)


@handler(MsgType.JUMP_DECISION)
def _h_jump_decision(ctx, msg):
    return ctx.host._on_jump_decision(msg)


# ------------------------------------------------------------------ #
# Registry thunks — focus family (Task 3.4)                           #
# Grouped for the Step-5 lift into features/focus.py                  #
# ------------------------------------------------------------------ #

@handler(MsgType.JUMP_WAITING)
def _h_jump_waiting(ctx, msg):
    return ctx.host._on_jump_waiting(msg)


# ------------------------------------------------------------------ #
# Registry thunks — lifecycle family (Task 3.5)                       #
# Grouped for the Step-5 lift into features/lifecycle.py              #
# ------------------------------------------------------------------ #

@handler(MsgType.SET_FOREGROUND)
@handler(MsgType.SESSION_START)
def _h_set_foreground(ctx, msg):
    return ctx.host._on_set_foreground(msg)


@handler(MsgType.SESSION_END)
def _h_session_end(ctx, msg):
    return ctx.host._on_session_end(msg)


# ------------------------------------------------------------------ #
# Registry thunks — hotkeys family (Task 3.5)                         #
# Grouped for the Step-5 lift into features/hotkeys.py                #
# ------------------------------------------------------------------ #

@handler(MsgType.RELOAD_KEYMAP)
def _h_reload_keymap(ctx, msg):
    return ctx.host._on_reload_keymap(msg)


# ------------------------------------------------------------------ #
# Registry thunks — control family (Task 3.5)                         #
# Grouped for the Step-5 lift into features/control.py                #
# ------------------------------------------------------------------ #

@handler(MsgType.SET_RATE)
def _h_set_rate(ctx, msg):
    return ctx.host._on_set_rate(msg)


@handler(MsgType.SET_VOICE)
def _h_set_voice(ctx, msg):
    return ctx.host._on_set_voice(msg)


@handler(MsgType.SET_VERBOSITY)
def _h_set_verbosity(ctx, msg):
    return ctx.host._on_set_verbosity(msg)


@handler(MsgType.SET_MINQUEUE)
def _h_set_minqueue(ctx, msg):
    return ctx.host._on_set_minqueue(msg)


@handler(MsgType.CYCLE_VERBOSITY)
def _h_cycle_verbosity(ctx, msg):
    return ctx.host._on_cycle_verbosity(msg)


@handler(MsgType.STATUS)
def _h_status(ctx, msg):
    return ctx.host._on_status(msg)


@handler(MsgType.PING)
def _h_ping(ctx, msg):
    return ctx.host._on_ping(msg)

