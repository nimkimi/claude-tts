# Per-Session Control Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace sonari's global play/pause + per-session mute + pin lattice with one truthful model — per-session **stop/start** (⌃⌘S) and **stop-all** (⌃⌘M) — the foundation the rest of the cockpit-grammar redesign sits on.

**Architecture:** The speak loop already plays only the *foreground* session's stream. Today a single global `_paused` Event gates the whole loop. This plan moves that gate onto each `SessionStream` as a per-session `stopped` flag: the loop holds when the *foreground* stream is stopped, and resume replays from the exact interrupted item (reusing the existing `enqueue_front` re-queue). ⌃⌘S toggles the foreground session's flag; ⌃⌘M broadcasts it to every stream. Pin and per-session mute are deleted; the now-dead global-pause scaffolding is retired last.

**Tech Stack:** Python 3.9+ (`from __future__ import annotations`), pytest. macOS daemon; behavior is unit-tested behind `tests/daemon_helpers.py` fakes — no audio, no hotkey hardware.

## Global Constraints

- **This is sub-project A of 4** (per-session control core → navigation grammar → answer-via-hook → sound language). Stay in scope: ⌃⌘S/⌃⌘M behavior + removing pin/mute/global-pause. Do NOT add nav, cycle, ⌃⌘W, rate, sound, or the hook channel here.
- **TDD, every task.** Write the failing test, watch it fail for the right reason, minimal code to green, refactor, commit. No production code without a failing test first.
- **Branch + PR only — NO direct push to `main`.** Work continues on branch `design/sonari-cockpit-grammar` (or a child of it). No `claude.ai/code/session` footer in any commit or PR.
- **NEVER run `sonari install` against the live `~/.sonari`.** All verification is `pytest`. The on-hardware feel is a separate human-acceptance gate, not part of this plan.
- **Bindings are by physical key position** (macOS keyCodes); ⌃⌘S = `s`, ⌃⌘M = `m` already bind to the old `pause`/`mute` actions, so this plan is a pure daemon-side behavior swap with a keymap *rename* — no new keytable entries.
- **Keep `SpeechItem.mute_exempt`.** Despite the name, after mute is removed it still serves its second purpose in `_attributed_text` (host.py): "this is a control cue — never folder-prefix it, don't claim it as last-spoken." All the new cues (`"Stopped."`, `"Resumed."`, `"All stopped."`) set `mute_exempt=True` for exactly that reason. Only the per-session `muted` *state* and its handler go away.
- **Cue wording (locked):** stop → `"Stopped."`; resume → `"Resumed."`; stop-all → `"All stopped."`.
- **MsgType inventory bookkeeping.** This plan's net protocol change is **+2 −3 = −1**: add `STOP_SESSION`, `STOP_ALL`; remove `PAUSE`, `MUTE`, `PIN_TOGGLE`. Three places track the inventory and must stay in sync as you go: (a) the `assert_complete([...])` list in `daemon/__init__.py` — keep it 1:1 with registered handlers at every step (swap in Tasks 2-3, remove `PIN_TOGGLE` in Task 4 → 27 entries); (b) the `# ... all 28 known keys ...` comment above it → **27** after Task 4; (c) `tests/test_protocol.py` and `tests/test_daemon_registry.py`, which enumerate/count the MsgType members (look for `ALL_28`/`test_all_28`/a "28" literal) → update to the renamed members and the new count of **27**. The full-suite-green gate will catch a missed one; fix it in the task that caused it.
- **Run the full suite green before declaring any task done** (`.venv/bin/python -m pytest -q`). Baseline on this branch: 784 passed, 1 skipped.

---

## File Structure

Production (all under `src/sonari/`):
- `session_stream.py` — add the per-session `stopped` flag (Task 1).
- `daemon/host.py` — speak loop: per-session stop gate + resume-from-spot (Task 2); remove the mute drop-branch (Task 3); remove `_paused` property + `_resume()` (Task 5).
- `daemon/features/playback.py` — replace `on_pause` with `on_stop_session` (Task 2); replace `on_mute` with `on_stop_all` (Task 3); delete `on_pin_toggle` (Task 4).
- `protocol.py` — retire `PAUSE`/`MUTE`/`PIN_TOGGLE`, add `STOP_SESSION`/`STOP_ALL` (Tasks 2–4).
- `daemon/__init__.py` — keep the `assert_complete([...])` registry guard in sync (Tasks 2–4).
- `keymap.py` — rename actions `pause`→`stop_session`, `mute`→`stop_all`; drop `pin_toggle` (Tasks 2–4).
- `sessions.py` — delete `_pinned`, `pin_toggle()`, `pinned()`; simplify `foreground()`/`focus()`/`unregister()` (Task 4).
- `daemon/host.py` `_flush_prose_buffer` waiting-cue guard + `daemon/features/focus.py` `_waiting_target` filter — swap `st.muted`→`st.stopped` (Task 3).
- `daemon/state.py` — remove the `_paused` Event (Task 5).
- `daemon/features/prose.py` — remove the FLUSH `_paused.clear()` (Task 5).

Tests (under `tests/`):
- `test_session_stream.py` — assert `stopped` default + stickiness (Task 1).
- `test_daemon_stop.py` — **new**: per-session stop + stop-all behavior (Tasks 2–3).
- `test_daemon_pause_mute.py` — **delete** (Task 2): its pause cases are replaced by `test_daemon_stop.py`; its mute cases are obsolete (mute removed in Task 3); it references `_paused` (removed in Task 5).
- `test_daemon_pin.py` — **delete** (Task 4).
- `test_keymap.py` — rename/retire pause/mute/pin assertions (Tasks 2–4).
- `test_sessions.py` — retire pin assertions (Task 4).
- Any remaining `_paused` references in `test_daemon_state.py`, `test_daemon_streams.py`, `test_concurrency_guards.py`, `test_blackbox_net.py` — clean up (Task 5).

---

## Task 1: Per-session `stopped` flag on `SessionStream`

**Files:**
- Modify: `src/sonari/session_stream.py:7-37`
- Test: `tests/test_session_stream.py`

**Interfaces:**
- Produces: `SessionStream.stopped: bool` (default `False`; **sticky** — `reset_for_new_prompt()` must NOT clear it). Tasks 2–5 read/write it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_session_stream.py`. In `test_defaults_are_empty_and_unflagged` add the `stopped` default assertion, and add a new stickiness test:

```python
def test_defaults_are_empty_and_unflagged():
    s = SessionStream()
    assert isinstance(s.assembler, ProseAssembler)
    assert s.prose_buffer == []
    assert s.options is None
    assert s.nav_cursor is None
    assert s.muted is False
    assert s.stopped is False
    assert s.warned_immediate is False
    assert s.guided is False


def test_reset_for_new_prompt_keeps_stopped_sticky():
    # Per-session stop survives a new prompt: a session you stopped stays silent
    # until you ⌃⌘S it again (spec §6.1) — a background re-invocation must not
    # resurrect it.
    s = SessionStream()
    s.stopped = True
    s.reset_for_new_prompt()
    assert s.stopped is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_session_stream.py -q`
Expected: FAIL — `AttributeError: 'SessionStream' object has no attribute 'stopped'`.

- [ ] **Step 3: Add the field**

In `src/sonari/session_stream.py`, in `__init__` (next to `self.muted = False`):

```python
        self.muted = False                  # sticky per-session mute
        self.stopped = False                # per-session stop (⌃⌘S); sticky across prompts
```

`reset_for_new_prompt()` already clears only the playback fields and keeps the sticky flags, so `stopped` is sticky with no change there. Confirm it does not appear in the reset body.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_session_stream.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/session_stream.py tests/test_session_stream.py
git commit -m "feat(sonari): add per-session stopped flag to SessionStream"
```

---

## Task 2: Per-session stop/start end-to-end (⌃⌘S)

Rewrites the speak loop to gate on the foreground stream's `stopped` flag (replacing the global `_paused` gate) and replaces the `pause` action/handler with `stop_session`. After this task, ⌃⌘S = per-session stop/start, dogfoodable. (The global `_paused` Event still exists but the loop no longer reads it; `on_pause` is gone. It is retired in Task 5. Per-session mute is untouched here and removed in Task 3.)

**Files:**
- Modify: `src/sonari/daemon/host.py:351-435` (`_speak_loop_once`)
- Modify: `src/sonari/daemon/features/playback.py:31-58` (replace `on_pause`)
- Modify: `src/sonari/protocol.py:23` (replace `PAUSE` with `STOP_SESSION`)
- Modify: `src/sonari/daemon/__init__.py:25` (`MsgType.PAUSE` → `MsgType.STOP_SESSION`)
- Modify: `src/sonari/keymap.py:36,49-52` (`pause` action → `stop_session`)
- Create: `tests/test_daemon_stop.py`
- Delete: `tests/test_daemon_pause_mute.py`
- Modify: `tests/test_keymap.py` (pause→stop_session assertions)

**Interfaces:**
- Consumes: `SessionStream.stopped` (Task 1); `SpeechQueue.pop_pause_exempt()` / `enqueue_front()`; `host._stream()`, `host._enqueue(..., mute_exempt, pause_exempt, at_front)`, `host._current_item`, `host._state._wake`.
- Produces: `MsgType.STOP_SESSION = "stop_session"`; handler `on_stop_session(ctx, msg)`; keymap action `stop_session` → `{"type": "stop_session"}`, default key `s` + platform mods. The speak loop's stopped-gate (read by Task 3's mute removal and Task 4).

- [ ] **Step 1: Write the failing behavior tests**

Create `tests/test_daemon_stop.py`:

```python
"""Per-session stop/start (⌃⌘S) and stop-all (⌃⌘M) — the per-session control core."""
from tests.daemon_helpers import make_daemon


def test_stop_toggles_the_foreground_stopped_flag():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    assert daemon._stream("fg").stopped is False
    daemon.handle_message({"type": "stop_session", "session": "fg"})
    assert daemon._stream("fg").stopped is True
    daemon.handle_message({"type": "stop_session", "session": "fg"})
    assert daemon._stream("fg").stopped is False


def test_stop_holds_loop_voices_cue_and_resume_replays_from_spot():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._enqueue("fg", "prose", "hello", False)
    daemon.handle_message({"type": "stop_session", "session": "fg"})
    daemon._speak_loop_once()                  # stopped: only the pause-exempt cue voices
    assert speaker.spoken == ["Stopped."]
    daemon._speak_loop_once()                  # nothing else exempt -> held
    assert speaker.spoken == ["Stopped."]
    assert "hello" not in speaker.spoken and len(queue) == 1   # backlog retained
    daemon.handle_message({"type": "stop_session", "session": "fg"})   # resume
    daemon._speak_loop_once()
    assert speaker.spoken == ["Stopped.", "Resumed."]          # confirmation first
    daemon._speak_loop_once()
    assert speaker.spoken == ["Stopped.", "Resumed.", "hello"] # then continues from spot


def test_stop_during_speech_requeues_interrupted_item():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._enqueue("fg", "prose", "interrupted sentence", False)

    def interrupted(text, cancel_epoch=None):
        speaker.spoken.append(text)
        daemon._stream("fg").stopped = True    # stop arrived mid-utterance
        return False                           # ... and cancelled it

    speaker.speak = interrupted
    daemon._speak_loop_once()
    assert speaker.spoken == ["interrupted sentence"]
    assert daemon._current_item is None
    assert len(queue) == 1 and queue.pop_next().text == "interrupted sentence"


def test_stop_preserves_heard_marker_for_the_replay():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    entry = daemon.history.record("fg", "prose", "hello")
    daemon._enqueue("fg", "prose", "hello", False, entry=entry)

    def interrupted(text, cancel_epoch=None):
        speaker.spoken.append(text)
        daemon._stream("fg").stopped = True
        return False

    speaker.speak = interrupted
    daemon._speak_loop_once()
    assert entry.heard is False
    assert entry in daemon._pending_heard.values()   # preserved for the replay
    daemon._stream("fg").stopped = False
    speaker.speak = lambda t, cancel_epoch=None: (speaker.spoken.append(t) or True)
    daemon._speak_loop_once()
    assert entry.heard is True


def test_stopped_session_does_not_auto_read_on_landing():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    daemon._enqueue("B", "prose", "b content", False)
    daemon._stream("B").stopped = True
    sessions.set_foreground("B")               # "land on" B
    daemon._speak_loop_once()
    assert speaker.spoken == []                # stopped -> held, no auto-read


def test_stop_is_sticky_across_a_new_prompt():
    daemon, queue, speaker, *_ = make_daemon(foreground="fg")
    daemon._stream("fg").stopped = True
    daemon.handle_message({"type": "flush", "session": "fg"})   # a new prompt
    assert daemon._stream("fg").stopped is True                 # NOT auto-resumed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_daemon_stop.py -q`
Expected: FAIL — the `stop_session` message routes to the no-op `_ignore` handler (registry default), so `stopped` never flips and no cue is spoken. (`KeyError`/assertion failures, not import errors.)

- [ ] **Step 3: Add the `STOP_SESSION` message type**

In `src/sonari/protocol.py`, replace the `PAUSE` line:

```python
    STOP_SESSION = "stop_session"   # ⌃⌘S: toggle a per-session stop (resume-from-spot, sticky)
```

- [ ] **Step 4: Rewrite the speak loop to gate on the foreground stream's `stopped` flag**

In `src/sonari/daemon/host.py`, replace the entire `_speak_loop_once` method (currently lines 351-435) with:

```python
    def _speak_loop_once(self) -> None:
        """One iteration of the speak loop. May raise; _speak_loop contains it.

        The voice plays the FOREGROUND session's stream: every pop reads the
        foreground stream's own queue. Background streams accumulate untouched
        until they become foreground. When the foreground stream is per-session
        STOPPED (⌃⌘S / ⌃⌘M), the loop is held — only a pause-exempt cue
        ("Stopped." / "All stopped.") is voiced — until it is started again."""
        fg0 = self.sessions.foreground()
        st0 = self._state._streams.get(fg0)
        if st0 is not None and st0.stopped:
            # Held: scan the foreground stream for a pause-exempt cue; otherwise
            # wait. Pop+claim under the lock, mirroring the normal branch.
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
            except Exception:  # noqa: BLE001 - one bad cue must not wedge the hold
                self._signal_speak_failure()
                completed = False
            self.note_spoken(item, completed)
            return
        # Pop and CLAIM the foreground stream's next item atomically under the lock.
        # foreground() is read here too, so a switch arriving on another connection
        # (also under the lock) is observed consistently. STOP/FLUSH run under this
        # lock, so they can't slip into the gap between pop and claim.
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
            # Snapshot before _attributed_text so we can roll back if a stop interrupts.
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
        if not requeued:
            self.note_spoken(item, completed)
```

(This preserves all three documented invariants — M2 cancel-epoch capture, L2 re-queue-inside-lock, and the `prev` attribution rollback — and removes the only outside-lock dependency on the global `_paused`. The `muted` branch is unchanged here; it is removed in Task 3.)

- [ ] **Step 5: Replace `on_pause` with `on_stop_session`**

In `src/sonari/daemon/features/playback.py`, replace the `on_pause` handler (lines 31-58) with:

```python
@handler(MsgType.STOP_SESSION)
def on_stop_session(ctx, msg):
    # Per-session stop/start (⌃⌘S). Toggles the FOREGROUND session — the track you
    # are currently flying; switch with ⌃⌘Tab / ⌃⌘J first to stop another. Stopping
    # holds this session's stream and re-reads from the interrupted item on resume;
    # the state is sticky across new prompts (a stopped session stays silent until
    # ⌃⌘S'd again).
    fg = ctx.host.sessions.foreground()
    if fg is None:
        ctx.host.speaker.earcon("error")
        return None
    st = ctx.host._stream(fg)
    if st.stopped:
        # Resuming: "Resumed." FIRST (at the front, ahead of the interrupted item the
        # speak loop re-queued there on stop), then clear the flag. _enqueue wakes
        # the loop. mute_exempt so the control cue is never folder-prefixed.
        st.stopped = False
        ctx.host._enqueue(fg, "prose", "Resumed.", False,
                          mute_exempt=True, at_front=True)
    else:
        st.stopped = True
        # Cancel only if THIS session is the one in flight, so stopping never cuts
        # another session's utterance (the loop only plays the foreground, so a live
        # claim is the foreground's — the session check is belt-and-suspenders).
        cur = ctx.host._current_item
        if cur is not None and cur.session == fg:
            ctx.host.speaker.cancel()
        # "Stopped." is pause_exempt (the held branch voices it past the re-queued
        # item) and mute_exempt (a control cue, never folder-prefixed).
        ctx.host._enqueue(fg, "prose", "Stopped.", False,
                          mute_exempt=True, pause_exempt=True)
    return None
```

- [ ] **Step 6: Keep the registry guard in sync**

In `src/sonari/daemon/__init__.py`, in the `assert_complete([...])` list, change `MsgType.PAUSE,` to `MsgType.STOP_SESSION,`.

- [ ] **Step 7: Rename the keymap action `pause` → `stop_session`**

In `src/sonari/keymap.py`:
- In `ACTION_MESSAGES`, replace the `"pause"` entry:
  ```python
      "stop_session": {"type": "stop_session"},   # ⌃⌘S: per-session stop/start
  ```
- In `_DEFAULT_KEYS`, change `"pause": "s"` to `"stop_session": "s"` (leave `"mute": "m", "pin_toggle": "p", "jump_waiting": "j"` for now):
  ```python
      "stop_session": "s", "mute": "m", "pin_toggle": "p", "jump_waiting": "j",
  ```

- [ ] **Step 8: Update the keymap tests for the rename**

In `tests/test_keymap.py`:
- `test_default_keymap_macos_uses_ctrl_cmd`: in the expected key set replace `"pause"` with `"stop_session"`, and change `assert d["pause"]["key"] == "s"` to `assert d["stop_session"]["key"] == "s"`.
- `test_resolve_macos_carbon_codes`: change the input/expected from `pause` to `stop_session` and the message to `{"type": "stop_session"}`:
  ```python
  def test_resolve_macos_carbon_codes(mac):
      resolved = keymap.resolve_keymap({"stop_session": {"key": "p", "mods": ["ctrl", "cmd"]}})
      assert resolved == [{
          "action": "stop_session", "keyCode": 35, "modifiers": 4352,  # 4096 | 256
          "message": '{"type": "stop_session"}'}]
  ```
- `test_default_keymap_binds_only_nav_pause_mute` and `test_default_keymap_binds_nav_pause_mute`: in their action lists replace `"pause"` with `"stop_session"`.
- `test_resolve_unknown_key_raises`, `test_resolve_unknown_mod_raises`, `test_resolve_skips_unbound_entries`, `test_load_keymap_merges_user_override`, `test_load_keymap_drops_unknown_actions`: these use `"pause"` as a *valid action carrier*. Replace each `"pause"` with `"stop_session"` (in `test_load_keymap_drops_unknown_actions`, keep `"stop"` as the unknown-action that gets dropped, and use `"stop_session"` as the kept one).

- [ ] **Step 9: Delete the obsolete pause/mute test file**

```bash
git rm tests/test_daemon_pause_mute.py
```

(Its pause cases are replaced by `test_daemon_stop.py`; its mute cases are removed with mute in Task 3; it references `_paused`, removed in Task 5.)

- [ ] **Step 10: Run the new tests + the full suite**

Run: `.venv/bin/python -m pytest tests/test_daemon_stop.py tests/test_keymap.py -q`
Expected: PASS.
Run: `.venv/bin/python -m pytest -q`
Expected: PASS (1 skipped). Investigate any failure — likely a lingering `pause`/`_paused` reference in another test (e.g. `test_daemon_registry.py`, `test_protocol.py`, `test_blackbox_net.py`). Fix references to the renamed action; do NOT touch `_paused` itself yet (Task 5).

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat(sonari): per-session stop/start (Ctrl+Cmd+S) replacing global pause"
```

---

## Task 3: Stop-all (⌃⌘M) + remove per-session mute

Adds the ⌃⌘M broadcast and deletes per-session mute (state, handler, the speak-loop drop-branch, and the two `st.muted` filters). After this task, ⌃⌘M = stop-all, dogfoodable; mute is gone.

**Files:**
- Modify: `src/sonari/protocol.py:24` (replace `MUTE` with `STOP_ALL`)
- Modify: `src/sonari/daemon/features/playback.py:61-79` (replace `on_mute` with `on_stop_all`)
- Modify: `src/sonari/daemon/host.py` (`_speak_loop_once`: delete the `muted` branch; `_flush_prose_buffer`: `not st.muted` → `not st.stopped`)
- Modify: `src/sonari/daemon/features/focus.py:16` (`_waiting_target`: `st.muted` → `st.stopped`)
- Modify: `src/sonari/session_stream.py` (delete `self.muted`)
- Modify: `src/sonari/daemon/__init__.py` (`MsgType.MUTE` → `MsgType.STOP_ALL`)
- Modify: `src/sonari/keymap.py` (`mute` action → `stop_all`)
- Modify: `tests/test_daemon_stop.py` (add stop-all tests), `tests/test_session_stream.py` (drop `muted` assertions), `tests/test_keymap.py` (mute→stop_all)

**Interfaces:**
- Consumes: `SessionStream.stopped`; `host._streams`, `host._current_item`, `host._enqueue`.
- Produces: `MsgType.STOP_ALL = "stop_all"`; handler `on_stop_all(ctx, msg)`; keymap action `stop_all` → `{"type": "stop_all"}`, default key `m`.

- [ ] **Step 1: Write the failing stop-all tests**

Append to `tests/test_daemon_stop.py`:

```python
def test_stop_all_stops_every_session_and_confirms():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    daemon._enqueue("A", "prose", "a", False)
    daemon._enqueue("B", "prose", "b", False)
    daemon.handle_message({"type": "stop_all", "session": "A"})
    assert daemon._stream("A").stopped is True and daemon._stream("B").stopped is True
    daemon._speak_loop_once()
    assert speaker.spoken == ["All stopped."]


def test_stop_all_is_one_way_each_session_returns_via_its_own_stop_key():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    daemon._enqueue("B", "prose", "b", False)
    daemon.handle_message({"type": "stop_all", "session": "A"})
    sessions.set_foreground("B")
    daemon._speak_loop_once()
    assert "b" not in speaker.spoken          # landing on B does NOT auto-read it
    daemon.handle_message({"type": "stop_session", "session": "B"})   # ⌃⌘S brings B back
    daemon._speak_loop_once()                 # "Resumed."
    daemon._speak_loop_once()                 # then B's content
    assert "b" in speaker.spoken
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_daemon_stop.py -q`
Expected: FAIL — `stop_all` is unhandled; `stopped` flags don't flip.

- [ ] **Step 3: Add the `STOP_ALL` message type**

In `src/sonari/protocol.py`, replace the `MUTE` line:

```python
    STOP_ALL = "stop_all"   # ⌃⌘M: stop EVERY session at once (one-way; return per-session via ⌃⌘S)
```

- [ ] **Step 4: Replace `on_mute` with `on_stop_all`**

In `src/sonari/daemon/features/playback.py`, replace the `on_mute` handler (lines 61-79) with:

```python
@handler(MsgType.STOP_ALL)
def on_stop_all(ctx, msg):
    # Stop EVERY session at once (the master quiet key, ⌃⌘M). One-way: bring each
    # session back individually with ⌃⌘S. Cancels any in-flight utterance; the
    # speak loop re-queues it at the front of its own (now stopped) stream.
    for st in ctx.host._streams.values():
        st.stopped = True
    if ctx.host._current_item is not None:
        ctx.host.speaker.cancel()
    fg = ctx.host.sessions.foreground()
    if fg is not None:
        # Ensure the foreground stream is stopped even if it had no stream yet, then
        # voice the confirmation (pause_exempt -> the held branch speaks it).
        ctx.host._stream(fg).stopped = True
        ctx.host._enqueue(fg, "prose", "All stopped.", False,
                          mute_exempt=True, pause_exempt=True)
    return None
```

- [ ] **Step 5: Remove the mute drop-branch from the speak loop**

In `src/sonari/daemon/host.py` `_speak_loop_once`, inside the normal-branch `with self._lock:` block, delete the mute computation and drop-branch. Change:

```python
            ist = self._state._streams.get(item.session) if item is not None else None
            muted = (item is not None
                     and ist is not None and ist.muted
                     and not item.mute_exempt)
            text = None
            # Snapshot before _attributed_text so we can roll back if a stop interrupts.
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
```

to:

```python
            text = None
            # Snapshot before _attributed_text so we can roll back if a stop interrupts.
            prev = self._state._last_spoken_session
            if item is not None:
                # Compute the attributed text under the lock so _last_spoken_session
                # is updated atomically with the pop — a concurrent JUMP_WAITING or
                # SET_FOREGROUND can't race the attribution read.
                text = self._attributed_text(item)
```

Then delete the now-dead guard further down:

```python
        if muted:
            return
```

- [ ] **Step 6: Swap the two `st.muted` filters to `st.stopped`**

A deliberately-silenced session should not raise a background "waiting" ping, and should not be offered as a jump target — the same intent the old mute filter had. Swap:

- `src/sonari/daemon/host.py` `_flush_prose_buffer` (the waiting-cue guard, ~line 201): change `and not st.muted` to `and not st.stopped`.
- `src/sonari/daemon/features/focus.py:16` (`_waiting_target`): change `st.muted` to `st.stopped`.

- [ ] **Step 7: Delete the `muted` field + its test assertions**

- In `src/sonari/session_stream.py`, delete `self.muted = False`.
- In `tests/test_session_stream.py`, remove `assert s.muted is False` from `test_defaults_are_empty_and_unflagged`, and in `test_reset_for_new_prompt_clears_playback_keeps_sticky` remove the two `s.muted` lines (`s.muted = True` and `assert s.muted is True`).

- [ ] **Step 8: Keep the registry guard + keymap in sync**

- `src/sonari/daemon/__init__.py`: change `MsgType.MUTE,` to `MsgType.STOP_ALL,`.
- `src/sonari/keymap.py`: in `ACTION_MESSAGES` replace the `"mute"` entry with `"stop_all": {"type": "stop_all"},   # ⌃⌘M: stop every session`; in `_DEFAULT_KEYS` change `"mute": "m"` to `"stop_all": "m"`.

- [ ] **Step 9: Update the keymap tests for the mute→stop_all rename**

In `tests/test_keymap.py`:
- `test_default_keymap_macos_uses_ctrl_cmd`: replace `"mute"` with `"stop_all"` in the key set; change `d["mute"]["key"] == "m"` to `d["stop_all"]["key"] == "m"`.
- `test_default_keymap_binds_only_nav_pause_mute` and `test_default_keymap_binds_nav_pause_mute`: replace `"mute"` with `"stop_all"`.
- `test_resolve_skips_unbound_entries`: replace the `"mute"` carrier with `"stop_all"`.

- [ ] **Step 10: Run the tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_daemon_stop.py tests/test_session_stream.py tests/test_keymap.py -q`
Expected: PASS.
Run: `.venv/bin/python -m pytest -q`
Expected: PASS (1 skipped). Fix any lingering `mute`/`.muted` references surfaced by the run.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat(sonari): stop-all (Ctrl+Cmd+M); remove per-session mute"
```

---

## Task 4: Remove pin

Deletes the pin concept entirely — `MsgType.PIN_TOGGLE`, `on_pin_toggle`, and the `SessionManager` pin machinery — and simplifies `foreground()` to the raw last-prompt session. The #65 "voice follows the speaker" guarantee must stay green.

**Files:**
- Modify: `src/sonari/protocol.py:25` (delete `PIN_TOGGLE`)
- Modify: `src/sonari/daemon/features/playback.py:82-100` (delete `on_pin_toggle`)
- Modify: `src/sonari/sessions.py` (delete `_pinned`, `pin_toggle()`, `pinned()`; simplify `foreground()`, `focus()`, `unregister()`)
- Modify: `src/sonari/daemon/__init__.py` (drop `MsgType.PIN_TOGGLE`)
- Modify: `src/sonari/keymap.py` (drop `pin_toggle` action + default key)
- Delete: `tests/test_daemon_pin.py`
- Modify: `tests/test_sessions.py`, `tests/test_keymap.py` (drop pin assertions)

**Interfaces:**
- Produces: `SessionManager.foreground()` now returns `self._foreground` (no pin); `focus()` and `unregister()` no longer reference `_pinned`. `MsgType.PIN_TOGGLE`, `pin_toggle()`, `pinned()` no longer exist.

- [ ] **Step 1: Update the sessions tests (red) — drop pin, keep foreground/focus behavior**

In `tests/test_sessions.py`, delete every test that exercises `pin_toggle`/`pinned`/`_pinned` (the pin-specific cases). Keep/ën adjust the `foreground()` and `focus()` tests so they assert the no-pin behavior:
- A `foreground()` test should assert it returns the last `set_foreground(...)` session.
- A `focus()` test should assert it moves the voice to the target (no pin to clear).

Run: `.venv/bin/python -m pytest tests/test_sessions.py -q`
Expected: FAIL only where a kept test still calls a pin method — that confirms what remains to remove. (If all pin tests are deleted and the kept ones already pass against current code, this step is a no-op red; proceed.)

- [ ] **Step 2: Delete `on_pin_toggle` + the pin message type**

- In `src/sonari/daemon/features/playback.py`, delete the entire `on_pin_toggle` handler (lines 82-100).
- In `src/sonari/protocol.py`, delete the `PIN_TOGGLE = "pin_toggle"` line.
- In `src/sonari/daemon/__init__.py`, delete the `MsgType.PIN_TOGGLE,` line from `assert_complete([...])`.

- [ ] **Step 3: Simplify `SessionManager` — remove pin**

In `src/sonari/sessions.py`:
- In `__init__`, delete `self._pinned: "str | None" = None ...`.
- `foreground()` → return the raw foreground:
  ```python
      def foreground(self) -> "str | None":
          """The session that owns the voice: the last session to submit a prompt / start."""
          return self._foreground
  ```
- In `unregister()`, delete the two lines:
  ```python
          if self._pinned == session:             # pinned session ended -> auto
              self._pinned = None
  ```
- `focus()` → drop the pin clear:
  ```python
      def focus(self, session: str, cwd=None) -> None:
          """Explicitly move the voice to *session* (the jump-to-waiting hotkey):
          set it foreground."""
          self._record(session, cwd)
          self._foreground = session
  ```
- Delete the `pinned()` method (lines 81-82) and the `pin_toggle()` method (lines 147-161).

- [ ] **Step 4: Drop the `pin_toggle` keymap action + default binding**

In `src/sonari/keymap.py`:
- In `ACTION_MESSAGES`, delete the `"pin_toggle"` entry.
- In `_DEFAULT_KEYS`, drop `"pin_toggle": "p"` (frees the `p` key):
  ```python
      "stop_session": "s", "stop_all": "m", "jump_waiting": "j",
  ```

- [ ] **Step 5: Delete the pin test file + pin keymap tests**

```bash
git rm tests/test_daemon_pin.py
```
In `tests/test_keymap.py`, delete the four pin tests: `test_pin_toggle_action_message`, `test_pin_toggle_default_binding_is_p`, `test_pin_toggle_resolves_to_its_message`, `test_pin_toggle_is_clearable`. (`test_no_two_default_actions_share_a_key` stays — it still holds with `p` freed.)

- [ ] **Step 6: Verify pin is gone + #65 still holds + full suite**

Run: `.venv/bin/python -m pytest tests/test_sessions.py tests/test_keymap.py tests/test_e2e_pipeline.py -q`
Expected: PASS — in particular `tests/test_e2e_pipeline.py::test_background_reinvocation_does_not_hijack_foreground_voice` (the #65 guarantee) stays green after the `foreground()` simplification.
Run: `.venv/bin/python -m pytest -q`
Expected: PASS (1 skipped). Fix any remaining `pin`/`_pinned`/`pinned()` references the run surfaces (e.g. `test_daemon_loop.py`, `test_daemon_nav.py`, `test_daemon_focus_nav.py`, `test_daemon_dispatch.py`, `test_protocol.py`).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(sonari): remove pin — foreground is the raw last-prompt session"
```

---

## Task 5: Retire the dead global-pause scaffolding

`_paused` is now written by nothing and read by nothing (the loop gates on `stopped`; `on_pause` is gone). Remove it and its last vestiges so no orphan global-pause state remains.

**Files:**
- Modify: `src/sonari/daemon/state.py:24` (delete the `_paused` Event)
- Modify: `src/sonari/daemon/host.py` (delete the `_paused` property + `_resume()`)
- Modify: `src/sonari/daemon/features/prose.py:96` (delete the FLUSH `_paused.clear()`)
- Modify: any test still referencing `_paused`/`_resume`

**Interfaces:**
- Produces: no `_paused`, no `_resume()` anywhere. `host._wake` and the FLUSH `_wake.set()` stay.

- [ ] **Step 1: Find every remaining reference (defines the work + the red)**

Run: `grep -rn "_paused\|_resume\b" src/ tests/`
Expected remaining (production): `state.py` (the Event), `host.py` (`_paused` property + `_resume`), `prose.py` (FLUSH clear). Plus any tests that still poke `daemon._paused` / `daemon._resume()`. Each is removed below; the grep is the checklist.

- [ ] **Step 2: Remove the production scaffolding**

- `src/sonari/daemon/state.py`: delete `self._paused = threading.Event()`. Update the class docstring phrase "the pause/wake Events" → "the wake Event".
- `src/sonari/daemon/host.py`: delete the `_paused` property (the `@property def _paused` + its `return self._state._paused`, lines 64-66), and delete the entire `_resume(self)` method (lines 293-298).
- `src/sonari/daemon/features/prose.py`: in `on_flush`, delete the two-line tail and its comment:
  ```python
      # A new prompt is a user action -> auto-resume from pause.
      ctx.host._paused.clear()
      ctx.host._wake.set()
  ```
  Replace with just the wake (a new prompt must still wake the loop to read the new turn):
  ```python
      ctx.host._wake.set()
  ```

- [ ] **Step 3: Clean up test references**

For each test file the Step 1 grep flagged (candidates: `test_daemon_state.py`, `test_daemon_streams.py`, `test_concurrency_guards.py`, `test_blackbox_net.py`), remove or rewrite the `_paused`/`_resume` usage:
- A test asserting the global-pause Event existed → delete it (the concept is gone).
- A test that set `daemon._paused` to simulate a hold → rewrite it to set `daemon._stream(fg).stopped = True` instead (the per-session equivalent), or delete if it duplicates `test_daemon_stop.py`.

Open each flagged file and apply the matching edit; do not leave any `_paused`/`_resume` reference.

- [ ] **Step 4: Verify nothing references the removed scaffolding**

Run: `grep -rn "_paused\|_resume\b" src/ tests/`
Expected: no matches.

- [ ] **Step 5: Run the full suite + the behavioral matrix**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (1 skipped).
Confirm the per-session-control matrix is covered and green (these are the spec's §6.1/§6.2 contracts):
- stop holds + voices `"Stopped."` + resume replays from spot (`test_stop_holds_loop_voices_cue_and_resume_replays_from_spot`)
- stop mid-utterance re-queues the interrupted item (`test_stop_during_speech_requeues_interrupted_item`)
- stopped session does not auto-read on landing (`test_stopped_session_does_not_auto_read_on_landing`)
- stop is sticky across a new prompt (`test_stop_is_sticky_across_a_new_prompt`)
- stop-all stops every session + one-way return via ⌃⌘S (`test_stop_all_*`)
- #65 unchanged (`test_background_reinvocation_does_not_hijack_foreground_voice`)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(sonari): retire dead global-pause scaffolding"
```

---

## Self-Review

**Spec coverage (per §6.1, §6.2, §5, §12):**
- §6.1 per-session stop/start, resume-from-spot, sticky, no global pause to dissolve #69 → Tasks 1, 2, 5. ✓
- §6.2 stop-all, one-way, no auto-read on a stopped session → Task 3. ✓
- §5 mapping: pause→⌃⌘S (Task 2), mute/stop→⌃⌘M+⌃⌘S (Tasks 2-3), pin/per-session-mute dropped (Tasks 3-4). ✓
- §12 implementation notes: per-session stop replaces `_paused` Event (Tasks 2,5); stop-all broadcast (Task 3); drops `pin_toggle`+pin and the per-session `mute` (Tasks 3-4); repurpose ⌃⌘M; keymap rewrite of the stop keys (Tasks 2-4). ✓
- **Out of scope (correctly deferred to later sub-projects):** ⌃⌘Tab cycle, two-layer nav + ⌃⌘D, ⌃⌘W spoken status, rate, barge-in/resume for *speaking* hotkeys, sound language, the hook answer channel. Not in this plan.

**Placeholder scan:** No "TBD"/"add error handling"/"write tests for the above". Test edits to existing files are named by test + exact change; new code is shown in full. The one place that says "fix any remaining references the run surfaces" (Tasks 2/3/4/5) is bounded by an exact `grep` whose output is the checklist — not a vague placeholder.

**Type/name consistency:** `STOP_SESSION = "stop_session"` / `on_stop_session` / action `stop_session`; `STOP_ALL = "stop_all"` / `on_stop_all` / action `stop_all` — consistent across protocol, handler, registry guard, and keymap in every task. `SessionStream.stopped` named identically in Tasks 1-5. `mute_exempt` deliberately retained (documented in Global Constraints). The existing one-shot `MsgType.STOP = "stop"` (CLI clear-foreground-queue) is a different string + handler and is untouched.

**Risk notes for the implementer:**
- The `_speak_loop_once` rewrite (Task 2) is the keystone; it preserves the three documented invariants (M2/L2/`prev` rollback). Run `test_daemon_loop.py`, `test_daemon_speak_resilience.py`, `test_speaker_cancel_2b.py`, and `test_concurrency_guards.py` after it.
- Removing pin changes `foreground()` (Task 4): the #65 e2e test is the canary — keep it green.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-26-sonari-per-session-control-core.md`. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent implements each task, with two-stage review between tasks. Best fit here: the tasks are sequenced with explicit interfaces and each ends green, and the speak-loop rewrite + the cross-cutting removals benefit from a fresh reviewer per task.
2. **Inline Execution** — implement the tasks in this session with checkpoints for review.
