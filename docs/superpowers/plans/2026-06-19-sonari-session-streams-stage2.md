# Sonari Session-Streams Stage 2 — Per-Stream Queues + Foreground-Driven Speak Loop

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every session its own speech queue and make the speak loop play only the foreground session's stream, so background sessions accumulate (never silently dropped) instead of being "captured."

**Architecture:** Today there is ONE shared `SpeechQueue` on the daemon, gated at *enqueue* time by a `_voice_owner` arbitration lattice (`_may_speak` / `_claim_for_decision`): a background session's prose is recorded to history but NOT enqueued (`st.captured = True`) — that is the drop. Stage 2 inverts this: each `SessionStream` owns a `SpeechQueue`; `_enqueue` always enqueues into the session's own stream; the speak loop pops only `foreground().queue`. Gating moves from enqueue-time to playback-time. Once selection is foreground-driven, the entire arbitration lattice is dead by construction and is deleted (terminal task).

**Tech Stack:** Python 3.9 (stdlib-only core), pytest. One speak thread, one `self._lock`, per-connection handlers — concurrency model unchanged.

## Global Constraints

- **Python 3.9 floor; stdlib-only core.** No new dependencies.
- **Suite green at every step.** Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`. Baseline after Stage 1: **698 passed, 2 skipped** (the 2 skips + ignored module need the `[kokoro]`/numpy extra, absent in `.venv` — pre-existing, not introduced here).
- **All `handle_message` runs hold `self._lock`** (via `_handle_conn` and `_dispatch_hotkey`). Inside `handle_message`, mutate-sites may lazy-create with `self._stream(session)`; read-only sites use `self._streams.get(session)` and stay None/absent-safe. The speak loop reads `self.sessions.foreground()` and the stream queues **under `self._lock`**.
- **`reset_for_new_prompt()` keeps the sticky flags** (`muted` / `warned_immediate` / `guided`) and **does NOT clear the queue** — the FLUSH handler clears the queue explicitly so it can drop the dropped items' heard-markers (`_drop_pending`).
- **Behavior-preserving for controls in Stage 2.** STOP still clears ALL streams (global), FLUSH/MUTE/NAV/JUMP/CATCH_UP stay scoped to the session they target today. Rescoping STOP/PAUSE to the foreground stream (symptom 2a) is **Stage 3**, not here.
- **Deliberate behavior changes in Stage 2** (rewrite the encoding tests, do not delete them; document the why):
  1. **Background accumulates instead of being captured** (symptoms 1 + 3a dissolve).
  2. **Voice follows foreground, losing mid-reply continuity:** a foreground switch mid-reply no longer holds the voice to end-of-reply (`_owner_mid_reply` retired). The prior reply's remaining prose waits in its own stream queue and is heard on switch-back — not lost.
- **Deferred, state it explicitly (do not "fix" here):**
  - **Cut-on-switch (spec §4.2):** Stage 2 lets the current sentence *finish* on a foreground switch; cutting it is **Stage 3**. A new prompt in session B does not cancel A's currently-playing sentence (FLUSH only cancels `cur.session == session`).
  - **`catch_up` reconciliation (spec §5/§6):** Stage 2 routes catch_up replay into the foreground stream so it is still heard; the deeper cross-session/dup rework is Stage 5/6.
- **Lattice removal is the terminal task** (Task 3), separated from the behavioral flip (Task 2) so each is a clean reviewer gate: Task 2 *stops calling* the lattice, Task 3 *deletes* it.

---

## File Structure

- `src/sonari/session_stream.py` — **modify.** `SessionStream` gains `self.queue: SpeechQueue`. (Task 1; `captured`/`open_msg` fields removed in Task 3.)
- `src/sonari/daemon.py` — **modify.** `_enqueue`, the speak loop, every control handler, `_nav`, `note_spoken`, STATUS redirect to per-stream queues; enqueue gates dropped; `_voice_owner` references removed (Task 2); lattice methods + shared-queue param deleted (Task 3).
- `src/sonari/queue.py` — **modify (Task 3).** `flush_session` retires (each per-stream queue holds one session, so it collapses to `clear()`).
- `tests/daemon_helpers.py` — **modify.** `make_daemon` returns the foreground stream's queue as the `queue` slot; add `stream_queue(daemon, session)` (Task 2); drop the shared-queue constructor arg (Task 3).
- `tests/test_session_stream.py` — **modify.** Lock the new `queue` field + reset contract (Task 1).
- `tests/test_daemon_streams.py` — **modify.** Add per-stream-queue + foreground-loop + accumulate regressions (Task 2).
- Old-policy tests to **rewrite (not delete)** in Task 2: `tests/test_daemon_phase21.py` (the capture/voice-owner suite), and any failing assertions in `tests/test_daemon_decisions.py`, `tests/test_daemon_prose.py`, `tests/test_daemon_control.py`, `tests/test_daemon_pause_mute.py`, `tests/test_daemon_nav.py`. Drop `_voice_owner` references in Task 3 cleanup.

---

## Task 1: `SessionStream` owns a `SpeechQueue`

Pure addition. Each session gets its own queue object; nothing in the daemon uses it yet. Behavior-preserving; suite stays green.

**Files:**
- Modify: `src/sonari/session_stream.py`
- Test: `tests/test_session_stream.py`

**Interfaces:**
- Produces: `SessionStream.queue` — a `sonari.queue.SpeechQueue` instance, created in `__init__`, **not** reset by `reset_for_new_prompt()`. Consumed by `daemon._enqueue`, the speak loop, and every control handler in Task 2.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_session_stream.py`:

```python
def test_new_stream_has_its_own_empty_speech_queue():
    from sonari.queue import SpeechQueue
    from sonari.session_stream import SessionStream
    st = SessionStream()
    assert isinstance(st.queue, SpeechQueue)
    assert len(st.queue) == 0


def test_reset_for_new_prompt_keeps_the_queue_object_and_items():
    # The FLUSH handler clears the queue explicitly (so it can drop heard-markers);
    # reset_for_new_prompt must NOT clear it, or those markers would leak.
    from sonari.queue import SpeechItem
    from sonari.session_stream import SessionStream
    st = SessionStream()
    q = st.queue
    st.queue.enqueue(SpeechItem(id=1, session="s", kind="prose",
                                text="x", is_decision=False))
    st.reset_for_new_prompt()
    assert st.queue is q
    assert len(st.queue) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_session_stream.py -q`
Expected: FAIL — `AttributeError: 'SessionStream' object has no attribute 'queue'`.

- [ ] **Step 3: Add the queue field**

In `src/sonari/session_stream.py`, add the import and the field. Final file:

```python
from __future__ import annotations

from sonari.assembler import ProseAssembler
from sonari.queue import SpeechQueue


class SessionStream:
    """All per-session speech state for one Claude Code session, in one place.

    Stage 2 of the per-session-streams redesign: each session owns its own speech
    queue, and the speak loop plays only the foreground session's stream.
    """

    def __init__(self) -> None:
        self.queue = SpeechQueue()          # this session's own pending-speech queue
        self.assembler = ProseAssembler()
        self.prose_buffer: list = []        # [(text, HistoryEntry)] awaiting minqueue flush
        self.options: "str | None" = None   # last decision text, for reread
        self.nav_cursor = None              # anchored message id (None == latest)
        self.captured = False               # message started while the voice was unavailable
        self.open_msg = False               # an assistant message is currently streaming
        self.muted = False                  # sticky per-session mute
        self.warned_immediate = False       # warned once about immediate selection
        self.guided = False                 # received the setup-guidance cue once

    def reset_for_new_prompt(self) -> None:
        """A new user prompt (FLUSH): reset playback state with a fresh assembler,
        but KEEP the sticky flags (muted / warned_immediate / guided). Does NOT
        clear self.queue — the FLUSH handler clears it so it can drop the dropped
        items' heard-markers."""
        self.assembler = ProseAssembler()
        self.prose_buffer = []
        self.options = None
        self.nav_cursor = None
        self.captured = False
        self.open_msg = False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_session_stream.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: 700 passed, 2 skipped (698 + 2 new).

- [ ] **Step 6: Commit**

```bash
git add src/sonari/session_stream.py tests/test_session_stream.py
git commit -m "feat: SessionStream owns its own SpeechQueue (Stage 2 Task 1)"
```

---

## Task 2: The flip — per-stream enqueue + foreground-driven speak loop

The behavioral heart of the redesign. `_enqueue` targets the session's own stream; the speak loop pops only the foreground stream; the enqueue-time gates are dropped so background output accumulates; every control site is redirected to per-stream queues, behavior-preservingly; and the `_voice_owner` *references* (not yet the method definitions) are removed from the sites this task already edits.

**Files:**
- Modify: `src/sonari/daemon.py`
- Modify: `tests/daemon_helpers.py`
- Modify/add: `tests/test_daemon_streams.py`
- Rewrite (old policy → new policy): `tests/test_daemon_phase21.py` and any failing assertions in `tests/test_daemon_decisions.py`, `tests/test_daemon_prose.py`, `tests/test_daemon_control.py`, `tests/test_daemon_pause_mute.py`, `tests/test_daemon_nav.py`.

**Interfaces:**
- Consumes: `SessionStream.queue` (Task 1); `self.sessions.foreground()` → `str | None` (last-prompt or pinned session); `SpeechQueue` methods `enqueue` / `enqueue_front` / `pop_next` / `pop_pause_exempt` / `jump_to_decision` / `clear` / `__len__`.
- Produces: the new speak-loop contract (drains `self._streams.get(foreground()).queue`); the harness helper `stream_queue(daemon, session)`; `make_daemon` returning the foreground stream's queue as its `queue` slot.

> **Implementer note — do the source edits first (Steps 1–9), run the full suite, then triage failures (Steps 10–12).** The harness change in Step 1 makes single-session tests pass unchanged (they enqueue into `queue`, which is now the foreground stream's queue the loop drains). The failures that remain encode the OLD capture/voice-owner policy and are rewritten in Step 11.

- [ ] **Step 1: Update the test harness (low-churn switch)**

In `tests/daemon_helpers.py`, change `make_daemon` to expose the foreground stream's queue as the returned `queue`, and add `stream_queue`. The constructor still takes the shared queue in Stage 2 (removed in Task 3):

```python
def make_daemon(verbosity: str = "everything", foreground: "str | None" = "fg"):
    """Build a SpeechDaemon. The returned `queue` is the FOREGROUND session's own
    stream queue (where its items now land and where the loop drains), so most
    single-session tests need no change. Use stream_queue() for other sessions."""
    shared = SpeechQueue()            # still required by the constructor in Stage 2
    speaker = FakeSpeaker()
    sessions = SessionManager()
    if foreground is not None:
        sessions.set_foreground(foreground)
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = verbosity
    daemon = SpeechDaemon(shared, speaker, sessions, config)
    queue = daemon._stream(foreground).queue if foreground is not None else shared
    return daemon, queue, speaker, sessions, config


def stream_queue(daemon, session: str):
    """The per-session speech queue, for assertions on a non-foreground session."""
    return daemon._stream(session).queue
```

- [ ] **Step 2: Redirect `_enqueue` to the session's own stream queue**

In `src/sonari/daemon.py`, `_enqueue` (currently enqueues onto `self.queue`). Replace the tail (the `if entry … self._wake.set()` block) with:

```python
        st = self._stream(session)
        if entry is not None:
            self._pending_heard[item.id] = entry
        if at_front:
            st.queue.enqueue_front(item)
        else:
            st.queue.enqueue(item)
        self._wake.set()
```

- [ ] **Step 3: Drop the enqueue gate in the PROSE handler (background accumulates)**

Replace the `MsgType.PROSE` branch body. Remove the `_may_speak` gate and the `captured = True` drop branch; every non-quiet session now buffers its own prose:

```python
        if t == MsgType.PROSE:
            final = msg.get("final", False)
            if not final:
                # A message is now streaming for this session.
                self._stream(session).open_msg = True
            a = self._stream(session).assembler
            chunks = a.feed(msg.get("delta", ""), msg.get("index", 0), final)
            if chunks:
                from sonari.assembler import PARAGRAPH_BREAK
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
                self._stream(session).open_msg = False
                self._stream(session).options = None
            return None
```

(`open_msg` is still set/cleared here; it becomes write-only after this task and is removed in Task 3.)

- [ ] **Step 4: Drop the decision gates (CHOICE / PLAN / PERMISSION always enqueue)**

In each of the three decision branches, remove the `if self._claim_for_decision(session):` wrapper and unconditionally flush+enqueue. CHOICE:

```python
        if t == MsgType.CHOICE:
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
            self._flush_prose_buffer(session)   # prose before the question
            self._enqueue(session, "choice", text, True, entry=entry)
            return None
```

PLAN:

```python
        if t == MsgType.PLAN:
            text = self._plan_text(msg)
            cue = self._selection_cue(session, verbosity)
            if cue:
                text = "{0} {1}".format(text, cue)
            self._stream(session).options = text
            entry = self.history.record(session, "plan", text)
            self.history.end_message(session)
            self._flush_prose_buffer(session)   # prose before the plan
            self._enqueue(session, "plan", text, True, entry=entry)
            return None
```

PERMISSION:

```python
        if t == MsgType.PERMISSION:
            text = self._permission_text(msg)
            cue = self._selection_cue(session, verbosity)
            if cue:
                text = "{0} {1}".format(text, cue)
            self._stream(session).options = text
            entry = self.history.record(session, "permission", text)
            self.history.end_message(session)
            self._flush_prose_buffer(session)   # prose before the permission ask
            self._enqueue(session, "permission", text, True, entry=entry)
            return None
```

- [ ] **Step 5: Drop the TOOL gate**

```python
        if t == MsgType.TOOL:
            if verbosity == "everything":
                tool = msg.get("tool", "")
                summary = (msg.get("summary") or "").strip()
                text = summary if summary else "Running {0}.".format(tool)
                # Keep textual order: read prose that preceded this tool call first.
                self._flush_prose_buffer(session)
                self._enqueue(session, "tool_announce", text, False)
            return None
```

- [ ] **Step 6: Rewrite the speak loop to drain the foreground stream**

Replace `_speak_loop_once` in full. Foreground is read under `self._lock`; pops/requeues are scoped to the right stream; the `_voice_owner` idle-release block is gone:

```python
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
            if muted:
                # Muted session: drop without speaking; release the claim.
                self._current_item = None
                self._pending_heard.pop(item.id, None)
        if item is None:
            # Foreground stream empty (or no foreground): wait until woken.
            self._wake.wait(self._poll_interval)
            self._wake.clear()
            return
        if muted:
            return
        try:
            completed = self.speaker.speak(item.text, cancel_epoch=cancel_epoch)
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
                self._current_item = None
                self._stream(item.session).queue.enqueue_front(item)
                requeued = True
        if not requeued:
            self.note_spoken(item, completed)
```

- [ ] **Step 7: Trim `note_spoken` (drop the voice-owner release)**

```python
    def note_spoken(self, item, completed: bool) -> None:
        """Speak-loop bookkeeping: confirm (or decline) the heard-marker for a
        finished utterance, and release the current-item claim."""
        with self._lock:
            self._current_item = None
            entry = self._pending_heard.pop(item.id, None)
            if entry is not None and completed:
                entry.heard = True
```

- [ ] **Step 8: Redirect the control handlers to per-stream queues (behavior-preserving)**

FLUSH — clear this session's own queue; drop the `_voice_owner` lines:

```python
        if t == MsgType.FLUSH:
            st = self._stream(session)
            self._drop_pending(st.queue.clear())
            cur = self._current_item
            if cur is not None and cur.session == session:
                self.speaker.cancel()
            st.reset_for_new_prompt()
            self.history.reset(session)
            # A new prompt is a user action -> auto-resume from pause.
            self._paused.clear()
            self._wake.set()
            return None
```

SESSION_END — drop this session's queued items, then pop the whole stream:

```python
        if t == MsgType.SESSION_END:
            self.sessions.unregister(session)
            st = self._streams.get(session)
            if st is not None:
                self._drop_pending(st.queue.clear())
            self.history.reset(session)
            self._streams.pop(session, None)
            return None
```

STOP — global stop clears EVERY stream's queue (behavior-preserving; rescoping to foreground is Stage 3):

```python
        if t == MsgType.STOP:
            for st in self._streams.values():
                self._drop_pending(st.queue.clear())
            self.speaker.cancel()
            return None
```

MUTE — the `st = self._stream(fg)` binding already exists; clear `st.queue`:

```python
            else:
                st.muted = True
                self._drop_pending(st.queue.clear())
                cur = self._current_item
                if cur is not None and cur.session == fg:
                    self.speaker.cancel()
                self._enqueue(fg, "prose", "Session muted.", False, mute_exempt=True)
            return None
```

JUMP_DECISION — operate on the foreground stream (what is playing):

```python
        if t == MsgType.JUMP_DECISION:
            # Mark the cancelled current item heard and drop the heard-markers of the
            # skipped prose, so a later CATCH_UP doesn't replay them out of order (M6).
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
```

CATCH_UP — route replay into the FOREGROUND stream so the loop plays it (cross-session reconcile is Stage 5/6):

```python
        if t == MsgType.CATCH_UP:
            fg = self.sessions.foreground()
            if fg is None:
                return None
            entries = self.history.unheard(fg)
            preamble = None
            if not entries:
                other = self.history.other_session_with_unheard(fg)
                if other is not None:
                    entries = self.history.unheard(other)
                    preamble = "Catching up on another session."
            if not entries:
                self._enqueue(fg, "prose", "You're all caught up.", False)
                return None
            # The voice plays the foreground stream, so replay into it: cut the
            # foreground's current utterance, clear its queue (so the replay isn't
            # duplicated by pending live items), then re-enqueue each unheard entry
            # there. Heard-marking rides on `entry=e`, independent of the stream the
            # text is read under.
            cur = self._current_item
            if cur is not None and cur.session == fg:
                self.speaker.cancel()
            self._drop_pending(self._stream(fg).queue.clear())
            if preamble:
                self._enqueue(fg, "prose", preamble, False)
            for e in entries:
                self._enqueue(fg, e.kind, e.text,
                              e.kind in ("choice", "plan", "permission"),
                              entry=e)
            return None
```

STATUS — `queue_len` becomes the total across streams (same meaning as the old global length):

```python
                "queue_len": sum(len(st.queue) for st in self._streams.values()),
```

- [ ] **Step 9: Redirect `_nav` and drop its voice-claim**

In `_nav`, delete the voice-claim block (the comment + `if self._voice_owner == session or not self._owner_open(): … captured = False`) — nav already targets the foreground session, whose stream the loop drains — and redirect its flush. The body from `ids = …` becomes:

```python
        ids = self.history.message_ids(session)
        if not ids:
            self._enqueue(session, "prose", "Nothing to navigate yet.", False)
            return
        n = len(ids)
        # Anchor on a STABLE message id, not a position: new paragraphs streaming in
        # append ids without shifting where the cursor points. Unset/stale -> latest.
        cur_id = self._stream(session).nav_cursor
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
```

- [ ] **Step 10: Run the full suite and capture the failures**

Run: `python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: source compiles and runs; single-session tests (e.g. all of `tests/test_daemon_loop.py`) PASS unchanged via the harness switch. FAILURES are concentrated in tests that encode the OLD capture/voice-owner policy (chiefly `tests/test_daemon_phase21.py`) and any test asserting on a *background* session's queue via the unpacked `queue`. Record the failing test ids — they are the Step 11 worklist.

- [ ] **Step 11: Apply the per-test dispositions below**

This list was built by reading every old-policy test. **Two dispositions, not one.** Spec §9 says "update, don't delete" — but that holds only for behaviors that still exist in a new form. Tests of behaviors that **cease to exist** (voice ownership, capture, H1 voice-busy hold, M4 claim-from-stale-owner, L3 nav voice-claim) have **no analog**; forcing a "rewrite" produces a tautology or a wrong assertion. Those are **DELETE with a one-line rationale comment**. Each rewrite adds `stream_queue` to the file's `from tests.daemon_helpers import …` line where used, and removes any `daemon._voice_owner = …` / `daemon._stream(...).captured = …` / `.open_msg = …` setup lines (those attributes are deleted in Task 3).

**`tests/test_daemon_phase21.py`:**
- `test_foreground_session_acquires_free_voice` → **DELETE** (rationale: voice acquisition concept retired; foreground-enqueue is covered by `test_enqueue_lands_in_the_sessions_own_stream_queue`).
- `test_response_landing_on_busy_voice_stays_captured_to_its_end` → **DELETE** (rationale: capture + H1 voice-busy hold retired; background now accumulates in its own stream).
- `test_nonforeground_response_is_captured_not_spoken` → **REWRITE**: background `"b"` prose now accumulates — `assert [i.text for i in stream_queue(daemon, "b")._items] == ["Background."]` and `assert len(queue) == 0` (foreground `a` unaffected); keep the history assertion.
- `test_owner_keeps_voice_after_foreground_moves` → **REWRITE**: drop both `_voice_owner` asserts; after `sessions.set_foreground("b")`, `a`'s continued deltas accumulate — `assert len(stream_queue(daemon, "a")) == 2` (not lost, not auto-played).
- `test_voice_frees_but_never_autostarts_nonforeground_backlog` → **REWRITE**: drop the `_voice_owner` assert; `"b"` backlog sits in `stream_queue(daemon, "b")` and one `daemon._speak_loop_once()` with foreground `a` leaves `speaker.spoken == []` (loop never auto-plays background).
- `test_choice_for_nonowner_is_captured_and_options_stored` → **REWRITE**: background `"b"` choice now enqueues into `stream_queue(daemon, "b")` (not captured); keep `"Pick one?" in daemon._stream("b").options` and the history assertion; `assert len(queue) == 0` (only `a`'s, none here).
- `test_repeat_acts_on_foreground_session_history` → **KEEP**; update the stale `# B captured` comment to `# b accumulates in its own stream`.
- `test_catch_up_falls_back_to_other_session_backlog` → **KEEP** (passes: catch_up now routes the cross-session replay into the foreground stream = `queue`); update the stale `# captured silently` comment to `# accumulates in b's stream`.
- All other phase21 tests (recording, heard-marking, the rest of repeat, catch_up, reread, choice, permission) → **KEEP unchanged** (single-session, `queue` is the foreground stream).

**`tests/test_daemon_decisions.py`:**
- `test_decision_for_foreground_claims_voice_from_background_owner` → **REWRITE**: delete the `daemon._voice_owner = "B"` line and the `assert daemon._voice_owner == "A"` line. The CHOICE for foreground `"A"` lands in `queue` (= A's stream); keep `len(queue) == 1`, `item.kind == "choice"`, `item.session == "A"`. Rename to `test_decision_for_foreground_enqueues_to_its_stream`.
- `test_decision_for_current_owner_still_enqueues_even_if_backgrounded` → **REWRITE**: delete the `daemon._voice_owner = "A"` line. Foreground is `B`, so the PERMISSION for `"A"` lands in `stream_queue(daemon, "A")`, not `queue` — `assert len(stream_queue(daemon, "A")) == 1` and `assert stream_queue(daemon, "A").pop_next().session == "A"`. Rename to `test_decision_for_background_session_enqueues_to_its_own_stream`.

**`tests/test_daemon_prose.py`:**
- `test_owner_keeps_voice_across_interchunk_drain_when_other_session_flips_foreground` → **REWRITE**: delete the two `assert daemon._voice_owner == "A"` lines. The rest holds as-is (`queue` is A's stream): after `SET_FOREGROUND "B"`, A's second delta still enqueues into `queue` (A's stream) — `len(queue) == 1`, text `"Second sentence here."`. Update the docstring to "A's remaining deltas accumulate in A's own stream (not lost)."
- `test_open_message_released_at_turn_boundary` → **DELETE** (rationale: voice-ownership lifecycle retired; there is no owner to release at the turn boundary).

**`tests/test_daemon_nav.py`:**
- `test_nav_makes_foreground_session_the_voice_owner` → **REWRITE**: delete the `daemon._voice_owner = "bg"`, `daemon._stream("fg").captured = True`, `assert daemon._voice_owner == "fg"`, and `assert not daemon._stream("fg").captured` lines. Keep the surviving behavior: after `_nav(daemon, "prev")`, live prose for `fg` enqueues and is drained/spoken (`["Live after nav."]`). Rename to `test_nav_replays_then_live_prose_for_foreground_is_spoken`.
- `test_nav_does_not_steal_voice_from_a_streaming_session` → **DELETE** (rationale: no voice ownership to protect; "nav enqueues replay items for fg" is covered by the rewrite above).

**`tests/test_daemon_control.py`, `tests/test_daemon_pause_mute.py`:** no old-policy assertions (the `pause_mute` `speaker.spoken == []` at line 71 is the mute-drop, which is preserved). **No changes expected** — but if any assertion fails after the source edits, triage it the same way (mechanical `stream_queue` switch, or delete/rewrite by the rule above).

Re-run the suite after each file.

- [ ] **Step 12: Add the new positive regressions (lock the flip)**

Add to `tests/test_daemon_streams.py` (the file already imports `MsgType` from `sonari.protocol` and `make_daemon` from `tests.daemon_helpers` — extend that line to also import `stream_queue`). These promote the symptom-1 / 3a spikes:

```python
# extend the existing import: from tests.daemon_helpers import make_daemon, stream_queue


def _pump_one(daemon):
    """Run exactly one speak-loop iteration (no thread)."""
    daemon._speak_loop_once()


def test_enqueue_lands_in_the_sessions_own_stream_queue():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._enqueue("a", "prose", "for a", False)
    daemon._enqueue("b", "prose", "for b", False)
    assert [i.text for i in stream_queue(daemon, "a")._items] == ["for a"]
    assert [i.text for i in stream_queue(daemon, "b")._items] == ["for b"]


def test_speak_loop_plays_only_the_foreground_stream():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._enqueue("a", "prose", "alpha", False)
    daemon._enqueue("b", "prose", "beta", False)   # background — must wait
    _pump_one(daemon)
    assert speaker.spoken == ["alpha"]
    assert len(stream_queue(daemon, "b")) == 1      # beta untouched


def test_background_accumulates_then_is_heard_after_switching_foreground():
    # Symptom 1 + 3a regression: B's output while A is foreground is NOT lost.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._enqueue("b", "prose", "beta-1", False)
    daemon._enqueue("b", "prose", "beta-2", False)
    _pump_one(daemon)
    assert speaker.spoken == []                      # nothing foreground to say
    sessions.set_foreground("b")                     # user switches to B
    _pump_one(daemon)
    _pump_one(daemon)
    assert speaker.spoken == ["beta-1", "beta-2"]    # heard, in order


def test_muted_foreground_item_is_dropped_but_exempt_is_spoken():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon._stream("a").muted = True
    daemon._enqueue("a", "prose", "silenced", False)
    daemon._enqueue("a", "prose", "muted-cue", False, mute_exempt=True)
    _pump_one(daemon)   # drops "silenced"
    _pump_one(daemon)   # speaks the exempt cue
    assert speaker.spoken == ["muted-cue"]


def test_catch_up_routes_cross_session_backlog_into_the_foreground_stream():
    # Stage 2 pins catch_up's new behavior so Stages 3-6 can't silently regress it:
    # the unheard from ANOTHER session is replayed under the foreground voice and
    # heard, with its history entries marked heard.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    daemon.handle_message(_msg(MsgType.PROSE, "b", delta="B unheard. ",
                               index=0, final=True))   # background, accumulates
    daemon.handle_message(_msg(MsgType.CATCH_UP, "a"))
    assert [i.text for i in stream_queue(daemon, "a")._items] == [
        "Catching up on another session.", "B unheard."]
    while len(stream_queue(daemon, "a")):
        _pump_one(daemon)
    assert speaker.spoken[-1] == "B unheard."
    assert daemon.history.unheard("b") == []          # entry marked heard
```

(Reuse the file's existing `_msg` helper rather than redefining it; if `_pump_one` clashes with an existing drain helper in `test_daemon_streams.py`, use that one — keep behavior identical.)

> **Note (not a test target):** `SET_FOREGROUND` does not call `self._wake.set()`, so a *live* foreground switch is observed by the speak loop on its next poll — up to `self._poll_interval` (0.1s) latency. The Step-12 regressions drive `_speak_loop_once()` directly, so they prove *selection*, not wake latency. This latency is acceptable for Stage 2; the switch-&-read hotkey in Stage 3 (which should `_wake.set()`) makes switching instant. Do not add threading tests here.

- [ ] **Step 13: Run the full suite green**

Run: `python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: all pass (≈700 + new regressions, 2 skipped). No failures.

- [ ] **Step 14: Commit**

```bash
git add src/sonari/daemon.py tests/
git commit -m "feat: per-stream queues + foreground-driven speak loop (Stage 2 Task 2)

Background output accumulates in its own stream instead of being captured;
the voice follows the foreground session. Dissolves symptoms 1 and 3a.
Controls behavior-preserving (STOP global); _voice_owner lattice now unused."
```

---

## Task 3: Delete the retired arbitration lattice

Pure dead-code removal — the lattice is uncalled after Task 2. Behavior-preserving; suite stays green. (Spec §8 moves this here from Stage 3.)

**Files:**
- Modify: `src/sonari/daemon.py`, `src/sonari/session_stream.py`, `src/sonari/queue.py`, `tests/daemon_helpers.py`
- Modify: any test still naming `_voice_owner` / `_may_speak` / `_claim_for_decision` / `.captured` / `.open_msg` / `flush_session`.

**Interfaces:**
- Consumes: nothing new.
- Produces: `SpeechDaemon.__init__(self, speaker, sessions, config)` (the `queue` parameter is gone); `SessionStream` without `captured` / `open_msg`; `SpeechQueue` without `flush_session`.

- [ ] **Step 1: Confirm the lattice is uncalled (guard before deleting)**

Run:
```bash
grep -n "_voice_owner\|_may_speak\|_claim_for_decision\|_owner_open\|_owner_mid_reply\|\.captured\|\.open_msg\|flush_session" src/sonari/daemon.py src/sonari/queue.py src/sonari/session_stream.py
```
Expected: in `daemon.py`, hits ONLY inside the method definitions to be deleted (`_owner_open`, `_owner_mid_reply`, `_may_speak`, `_claim_for_decision`) and the `__init__` field — no live callers. If any *call site* remains outside those definitions, STOP: a Task-2 edit was missed; fix it before deleting.

- [ ] **Step 2: Delete the dead methods and field in `daemon.py`**

- Remove `self._voice_owner: "str | None" = None` from `__init__`.
- Remove the four method definitions in full: `_owner_open`, `_owner_mid_reply`, `_may_speak`, `_claim_for_decision`.
- Remove the now-write-only `open_msg` sets: in the PROSE handler (`if not final: self._stream(session).open_msg = True` and the `final` branch's `self._stream(session).open_msg = False`) and in the EARCON `turn_done` branch (`self._stream(session).open_msg = False`).

- [ ] **Step 3: Remove the shared-queue constructor parameter**

- `__init__` signature: `def __init__(self, speaker, sessions, config) -> None:` — delete the `queue` parameter and the `self.queue = queue` line.
- In `main()`: delete `queue = SpeechQueue()` (and the local `from sonari.queue import SpeechQueue` if it is now otherwise unused in `main`), and change construction to `daemon = SpeechDaemon(speaker, sessions, cfg)`.

- [ ] **Step 4: Drop `captured` and `open_msg` from `SessionStream`**

In `src/sonari/session_stream.py`, remove `self.captured = …` and `self.open_msg = …` from both `__init__` and `reset_for_new_prompt`. Update the class docstring if it references them.

- [ ] **Step 5: Retire `SpeechQueue.flush_session`**

In `src/sonari/queue.py`, delete the `flush_session` method (each per-stream queue holds a single session, so callers use `clear()`). Update the stale comment in `pop_next` that mentions `flush_session` to reference `clear()` instead.

- [ ] **Step 6: Update the harness constructor call**

In `tests/daemon_helpers.py`, `make_daemon` no longer builds/passes a shared queue:

```python
def make_daemon(verbosity: str = "everything", foreground: "str | None" = "fg"):
    speaker = FakeSpeaker()
    sessions = SessionManager()
    if foreground is not None:
        sessions.set_foreground(foreground)
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = verbosity
    daemon = SpeechDaemon(speaker, sessions, config)
    queue = daemon._stream(foreground).queue if foreground is not None else SpeechQueue()
    return daemon, queue, speaker, sessions, config
```

(Keep the `from sonari.queue import SpeechQueue` import — it is still used for the `foreground is None` fallback and by `test_queue.py` callers.)

- [ ] **Step 7: Clean any remaining lattice references in tests**

Run:
```bash
grep -rn "_voice_owner\|_may_speak\|_claim_for_decision\|\.captured\|\.open_msg\|flush_session" tests/
```
For each hit: a `flush_session` test in `tests/test_queue.py` asserting that method exists is now obsolete — remove it (document why: per-stream queues hold one session; `clear()` replaces it). Any residual `_voice_owner` assertion missed in Task 2 → delete it. Mechanical.

- [ ] **Step 8: Run the full suite green**

Run: `python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: all pass, 2 skipped. No references to the deleted symbols remain.

- [ ] **Step 9: Commit**

```bash
git add src/sonari/ tests/
git commit -m "refactor: delete the retired voice-arbitration lattice (Stage 2 Task 3)

_voice_owner / _may_speak / _claim_for_decision / _owner_open /
_owner_mid_reply and the captured/open_msg fields are dead by construction
once selection is foreground-driven. Drop the shared queue + flush_session."
```

---

## Self-Review (run before execution)

**1. Spec coverage (Stage 2 scope, spec §8.2 + §4.2/§4.3):**
- Per-stream queues → Task 1 (field) + Task 2 (enqueue/loop/controls). ✓
- Foreground-driven speak loop → Task 2 Step 6. ✓
- Background accumulates, not captured (symptom 1 + 3a) → Task 2 Steps 3–5 + regressions Step 12. ✓
- Retire the lattice (`_voice_owner` & friends, FLUSH-vs-SESSION_END divergence already fixed in Stage 1) → Task 2 (stop calling) + Task 3 (delete). ✓
- Deferrals stated: cut-on-switch → Stage 3; catch_up reconcile → Stage 5/6; control rescoping → Stage 3. ✓ (Global Constraints)

**2. Placeholder scan:** every code step contains complete code; the only "describe, don't show" item is the Step 11 *rewrite mapping*, which is intentional (it transforms existing tests and gives concrete before/after examples + the exact harness helper). No TBD/TODO. ✓

**3. Type/name consistency:** `SessionStream.queue` (Task 1) is consumed identically in Task 2 (`self._stream(session).queue`, `st.queue`); `stream_queue(daemon, session)` used consistently in Steps 11–12; `foreground()` returns `str | None` and every loop/handler read guards `None` via `self._streams.get(...)`; `make_daemon` returns the same 5-tuple throughout (the `queue` slot's *source* changes, not the shape). ✓

**4. Green-at-each-step:** Task 1 additive (700/2). Task 2 is one atomic flip (every `self.queue` site moves together — a partial move leaves controls operating on an empty shared queue), green after Step 13. Task 3 pure deletion, green after Step 8. ✓
