# Cockpit Navigation & Session Grammar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land sub-project **B** of sonari's cockpit-grammar redesign — the **navigation & session grammar**: cycle sessions (⌃⌘Tab / ⌃⌘⇧Tab), where-am-I spoken status (⌃⌘W) with barge-in + interjection-resume, two-layer transcript nav rebound to ⌃⌘↑/↓, jump-to-decision on ⌃⌘D, and rate on ⌃⌘+/−. Sub-project A (per-session stop ⌃⌘S / stop-all ⌃⌘M; pin & mute removed) already shipped.

**Architecture:** Three small new daemon mechanisms plus one atomic keymap rewrite. (1) A `SessionManager.session_ids()` roster accessor so cycle handlers don't poke the private `_sessions` dict. (2) `on_cycle_session` (one `CYCLE_SESSION` MsgType carrying a `direction` field) steps the voice through that roster with wrap, mirroring `on_jump_waiting`'s focus+cancel+folder-cue pattern but as a *soft* switch (no terminal-raise). (3) `on_where_am_i` (a new `WHERE_AM_I` MsgType, **not** the CLI `STATUS` dict path) speaks a terse plain-text status and implements §7 interjection-resume: capture the in-flight item, barge-in (`cancel()`), enqueue the status cue at the front, then re-queue the interrupted item at the front *behind* it on a fresh item id that carries the original `pending_heard` entry — so the speak loop's post-cancel `note_spoken` (which pops the OLD id with `completed=False`) cannot mark it unheard or lose it. The within/between-response nav handlers and the jump-to-decision handler are unchanged except a 1-line ⌃⌘D targeting consistency fix; everything else is a keymap/keytable rebind to §4.

**Tech Stack:** Python 3.9+ (`from __future__ import annotations`), pytest. macOS daemon; behavior is unit-tested behind `tests/daemon_helpers.py` fakes (`make_daemon` / `FakeSpeaker`) — no audio, no hotkey hardware. The macOS hotkey/keystroke layer and on-hardware feel are a separate human-acceptance gate.

## Global Constraints

- **This is sub-project B of 4** (A shipped; **C = answer-via-hook**, **D = sound language** are LATER — out of scope here: no ⌃⌘⏎/⎋, no spearcon synthesis/pitch; **⌃⌘W speaks PLAIN terse status**).
- **TDD, every task.** Write the failing test, watch it fail for the right reason, minimal code to green, refactor, commit. **Full suite green before each task is done** (`.venv/bin/python -m pytest -q`). **Baseline on this branch: 758 passed, 1 skipped.**
- **Branch + PR only — NO direct push to `main`.** Work continues on branch `feat/cockpit-nav-grammar`. **NO `claude.ai/code/session` footer** in any commit or PR.
- **NEVER run `sonari install` against the live `~/.sonari`.** All verification is `pytest`; on-hardware feel is a separate human gate.
- **Bindings are by physical key position** (macOS keyCodes); the modifier is **⌃⌘ (ctrl+cmd)** for every B chord. **⌃⌘D is KEPT** despite shadowing macOS Look Up (owner decision — see spec §15).
- **Lock discipline.** Daemon handlers run **UNDER the daemon lock** (via `_state.transaction()` on the socket/hotkey paths); `_enqueue` does **NOT** take the lock; **NEVER re-acquire `self._lock` inside a handler** (deadlock). Wire `type` values are the `MsgType` **string** values.
- **MsgType inventory 27 → 29** (`CYCLE_SESSION`, `WHERE_AM_I`). Keep four places in sync as you go: the `assert_complete([...])` list in `daemon/__init__.py`, its `# ... all N known keys ...` comment, `tests/test_daemon_registry.py` (`ALL_27`→`ALL_29`, plus the test/fn names), and the two hardcoded dicts in `tests/test_protocol.py`. **Task 2 does 27 → 28** (`CYCLE_SESSION`); **Task 3 does 28 → 29** (`WHERE_AM_I`). The full-suite-green gate catches a missed one.
- **The #65 "voice follows the speaker" guarantee + all sub-project A behavior must stay green.**
- **Norwegian +/− note:** ship the **ANSI `equal`/`minus` positions** (keyCodes 24 / 27) as the default for rate. The Norwegian physical +/− position is **verified on-hardware at the human-acceptance gate** — do NOT block on it and do NOT add Norwegian-specific defaults in this plan.

---

## File Structure

Production (all under `src/sonari/`):
- `sessions.py` — add the public `session_ids()` roster accessor (Task 1).
- `protocol.py` — add `MsgType.CYCLE_SESSION` (Task 2) and `MsgType.WHERE_AM_I` (Task 3).
- `daemon/features/focus.py` — add `on_cycle_session` (Task 2).
- `daemon/features/control.py` — add `on_where_am_i` (Task 3).
- `daemon/features/playback.py` — `on_jump_decision` targeting fix (Task 4).
- `daemon/__init__.py` — keep the `assert_complete([...])` guard + count comment in sync (Tasks 2, 3).
- `keymap.py` — `ACTION_MESSAGES` + `_DEFAULT_KEYS` rewrite (Task 5).
- `platform/macos/keytables.py` — `KEY_CODES` additions (Task 5).
- `platform/macos/hotkeys.py` — `_KEY_DISPLAY_BY_NAME` + `extra_default_bindings()` (Task 5).

Tests (under `tests/`):
- `test_sessions.py` — `session_ids()` unit tests (Task 1).
- `test_daemon_cycle.py` — **new**: cycle-session behavior (Task 2).
- `test_daemon_where_am_i.py` — **new**: where-am-I behavior incl. interjection-resume (Task 3).
- `test_daemon_decisions.py` — add the focused-session routing test (Task 4).
- `test_daemon_registry.py` — `ALL_27`→`ALL_28`→`ALL_29` + fn renames (Tasks 2, 3).
- `test_protocol.py` — add the two new constants to both MsgType dicts (Tasks 2, 3).
- `test_keymap.py` — rewrite the default-set tests + new B-binding tests (Task 5).
- `test_cli_hotkeyd.py` — repoint the `keymap … clear` test off the now-unbound `nav_first` (Task 5).

---

## Task 1: `SessionManager.session_ids()` roster accessor

The cycle handlers (Task 2) need an encapsulated, insertion-ordered roster instead of reaching into the private `_sessions` dict.

**Files:**
- Modify: `src/sonari/sessions.py` (add a method after `foreground()`, ~line 57)
- Test: `tests/test_sessions.py`

**Interfaces:**
- Produces: `SessionManager.session_ids() -> list[str]` — `list(self._sessions.keys())`, insertion order. Consumed by `on_cycle_session` (Task 2).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sessions.py`:

```python
# --- roster (cycle-session) ----------------------------------------------

def test_session_ids_empty_initially():
    assert SessionManager().session_ids() == []


def test_session_ids_returns_insertion_order():
    sm = SessionManager()
    sm.register("a")
    sm.register("b")
    sm.set_foreground("c")          # set_foreground also records the session
    assert sm.session_ids() == ["a", "b", "c"]


def test_session_ids_excludes_unregistered():
    sm = SessionManager()
    sm.register("a")
    sm.register("b")
    sm.unregister("a")
    assert sm.session_ids() == ["b"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sessions.py -q`
Expected: FAIL — `AttributeError: 'SessionManager' object has no attribute 'session_ids'`.

- [ ] **Step 3: Add the accessor**

In `src/sonari/sessions.py`, immediately after the `foreground()` method:

```python
    def session_ids(self) -> "list[str]":
        """All registered session ids in insertion order — the cycle roster (⌃⌘Tab).
        Encapsulates the private _sessions dict so handlers don't poke it directly."""
        return list(self._sessions.keys())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sessions.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite + commit**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (1 skipped).

```bash
git add src/sonari/sessions.py tests/test_sessions.py
git commit -m "feat(sonari): add SessionManager.session_ids() roster accessor"
```

---

## Task 2: Cycle sessions (⌃⌘Tab / ⌃⌘⇧Tab), daemon side

Adds **one** `MsgType.CYCLE_SESSION` (carrying `direction` = `"next"|"prev"`) and `on_cycle_session` in `focus.py`. Steps the voice through `session_ids()` with wrap; `<2` sessions → an `error` earcon (never a silent no-op). NOT keymap-bound yet (Task 5) — tested by sending the message directly.

**Files:**
- Modify: `src/sonari/protocol.py` (add `CYCLE_SESSION` after `OS_FOCUS`, ~line 36)
- Modify: `src/sonari/daemon/features/focus.py` (add `on_cycle_session` after `on_jump_waiting`)
- Modify: `src/sonari/daemon/__init__.py` (add `MsgType.CYCLE_SESSION,` to `assert_complete`; comment 27 → 28)
- Modify: `tests/test_daemon_registry.py` (`ALL_27`→`ALL_28`; fn rename; `+CYCLE_SESSION`)
- Modify: `tests/test_protocol.py` (add `CYCLE_SESSION` to both dicts)
- Create: `tests/test_daemon_cycle.py`

**Interfaces:**
- Consumes: `sessions.session_ids()` (Task 1), `sessions.foreground()`, `sessions.focus()`, `sessions.folder()`, `speaker.cancel()`, `speaker.earcon()`, `host._enqueue(..., mute_exempt, at_front, names_session)`.
- Produces: `MsgType.CYCLE_SESSION = "cycle_session"`; handler `on_cycle_session(ctx, msg)`. (Keymap actions `cycle_session_next`/`cycle_session_prev` arrive in Task 5.)

- [ ] **Step 1: Write the failing behavior tests**

Create `tests/test_daemon_cycle.py`:

```python
"""Cycle sessions (⌃⌘Tab / ⌃⌘⇧Tab) — roster navigation in insertion order."""
from tests.daemon_helpers import make_daemon


def test_cycle_next_moves_voice_to_the_next_session_and_cues_it():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    sessions.set_foreground("A")
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    assert sessions.foreground() == "B"
    assert speaker.cancels == 1                  # barge-in: the switch is immediate
    daemon._speak_loop_once()
    assert speaker.spoken == ["bravo."]          # self-naming folder cue at the front


def test_cycle_next_wraps_from_last_to_first():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A")
    sessions.register("B")
    sessions.set_foreground("B")                 # last in the roster
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    assert sessions.foreground() == "A"          # wraps to the first


def test_cycle_prev_wraps_from_first_to_last():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A")
    sessions.register("B")
    sessions.register("C")
    sessions.set_foreground("A")                 # first in the roster
    daemon.handle_message({"type": "cycle_session", "direction": "prev"})
    assert sessions.foreground() == "C"          # wraps to the last


def test_cycle_with_fewer_than_two_sessions_errors_and_does_not_switch():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    # only A is registered (via make_daemon's set_foreground)
    daemon.handle_message({"type": "cycle_session", "direction": "next"})
    assert speaker.earcons == ["error"]          # confirm fired; never a silent no-op
    assert sessions.foreground() == "A"          # unchanged
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_daemon_cycle.py -q`
Expected: FAIL — `cycle_session` routes to the registry no-op, so `foreground()` never moves and no cue/earcon is produced.

- [ ] **Step 3: Add the `CYCLE_SESSION` message type**

In `src/sonari/protocol.py`, add after the `OS_FOCUS` line:

```python
    CYCLE_SESSION = "cycle_session"   # ⌃⌘Tab/⌃⌘⇧Tab: cycle the voice through the roster (msg["direction"])
```

- [ ] **Step 4: Add the `on_cycle_session` handler**

In `src/sonari/daemon/features/focus.py`, after `on_jump_waiting`:

```python
@handler(MsgType.CYCLE_SESSION)
def on_cycle_session(ctx, msg):
    # ⌃⌘Tab / ⌃⌘⇧Tab: cycle the VOICE through the session roster in insertion order,
    # wrapping at the ends. A SOFT switch (no terminal-raise, unlike jump-to-waiting):
    # focus the target, cut the current utterance, lead with a self-naming folder cue.
    sessions = ctx.host.sessions
    ids = sessions.session_ids()
    if len(ids) < 2:
        ctx.host.speaker.earcon("error")          # <2 sessions: confirm fired, no silent no-op
        return None
    fg = sessions.foreground()
    cur = ids.index(fg) if fg in ids else 0
    step = 1 if msg.get("direction", "next") == "next" else -1
    target = ids[(cur + step) % len(ids)]
    sessions.focus(target)
    ctx.host.speaker.cancel()
    folder = sessions.folder(target)
    cue = folder + "." if folder else "Another session."
    ctx.host._enqueue(target, "prose", cue, False,
                      mute_exempt=True, at_front=True, names_session=True)
    return None
```

- [ ] **Step 5: Keep the registry guard in sync (27 → 28)**

In `src/sonari/daemon/__init__.py`:
- Change the comment `... all 27 known keys explicitly.` → `... all 28 known keys explicitly.`
- Add `MsgType.CYCLE_SESSION,` to the `assert_complete([...])` list (after `MsgType.OS_FOCUS,`).

- [ ] **Step 6: Update the protocol + registry tests**

In `tests/test_protocol.py`, add `"CYCLE_SESSION": "cycle_session",` to **both** hardcoded dicts — in `test_msgtype_has_every_constant_with_exact_values` (the `expected` dict, ~line 79, after `"OS_FOCUS"`) and in `test_msgtype_defines_no_extra_string_constants` (the `expected` dict, ~line 119, after `"OS_FOCUS"`). (The second dict is an exhaustive `actual == expected` check and **must** include it.)

In `tests/test_daemon_registry.py`:
- Rename the constant `ALL_27` → `ALL_28` and add `_MsgType.CYCLE_SESSION,` to the list (after `_MsgType.OS_FOCUS,`).
- Rename `test_all_27_msgtypes_registered` → `test_all_28_msgtypes_registered`; update its references from `ALL_27` to `ALL_28`.
- In `test_negative_assert_complete_names_missing_type`, change `reg.assert_complete(ALL_27)` → `reg.assert_complete(ALL_28)`.

- [ ] **Step 7: Run the new + affected tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_daemon_cycle.py tests/test_protocol.py tests/test_daemon_registry.py -q`
Expected: PASS.
Run: `.venv/bin/python -m pytest -q`
Expected: PASS (1 skipped). (`tests/test_hotkeyd_contract.py::test_all_action_messages_are_known_msgtypes` is unaffected — `CYCLE_SESSION` isn't in `ACTION_MESSAGES` until Task 5.)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(sonari): cycle-session handler (Ctrl+Cmd+Tab roster nav)"
```

---

## Task 3: Where-am-I (⌃⌘W) spoken status with barge-in + interjection-resume

**THE KEYSTONE.** A new `MsgType.WHERE_AM_I` (do NOT reuse the CLI `STATUS` dict handler) + `on_where_am_i` in `control.py`. Speaks a terse plain status, barges in, then resumes the interrupted item from its start without losing its heard-marker.

**Files:**
- Modify: `src/sonari/protocol.py` (add `WHERE_AM_I` after `CYCLE_SESSION`)
- Modify: `src/sonari/daemon/features/control.py` (add `on_where_am_i` after `on_status`)
- Modify: `src/sonari/daemon/__init__.py` (add `MsgType.WHERE_AM_I,`; comment 28 → 29)
- Modify: `tests/test_daemon_registry.py` (`ALL_28`→`ALL_29`; fn rename; `+WHERE_AM_I`)
- Modify: `tests/test_protocol.py` (add `WHERE_AM_I` to both dicts)
- Create: `tests/test_daemon_where_am_i.py`

**Interfaces:**
- Consumes: `host._current_item`, `host._pending_heard`, `host._streams`, `host._enqueue`, `sessions.foreground()`, `sessions.folder()`, `speaker.cancel()`, `speaker.earcon()`; the post-A speak loop (`host._speak_loop_once` / `note_spoken`).
- Produces: `MsgType.WHERE_AM_I = "where_am_i"`; handler `on_where_am_i(ctx, msg)`.

**Design — interjection-resume (read before coding).** When the foreground session is NOT stopped, the post-A speak loop's post-cancel block (`host.py`, `if not completed and self._stream(item.session).stopped:`) does **not** fire, so it calls `note_spoken(item, completed=False)` — which sets `_current_item=None` and **pops** the item's `pending_heard` entry (leaving `heard=False`). A non-stopping interjection must therefore re-queue the interrupted item **itself**, on a **fresh item id** that re-registers the SAME `pending_heard` entry: `note_spoken` then pops only the OLD id, and the entry survives on the new id. Both the handler (under the transaction lock) and `note_spoken` (under `self._lock`) serialize on the same lock, so there is no interleave. Enqueue order: re-queue the interrupted item at_front **first**, then the status cue at_front — so the queue is `[status, resumed-item]` and the status plays first.

- [ ] **Step 1: Write the failing behavior tests**

Create `tests/test_daemon_where_am_i.py`:

```python
"""Where am I (⌃⌘W) — terse spoken status with barge-in + interjection-resume (§7)."""
from tests.daemon_helpers import make_daemon


def test_where_am_i_speaks_terse_status():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/Users/me/work")
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()
    assert speaker.spoken == ["work. Playing. 0 waiting."]
    assert speaker.cancels == 1                   # barge-in fires (always-confirm)


def test_where_am_i_unknown_folder_says_unknown_session():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "where_am_i", "session": "fg"})   # no cwd -> folder None
    daemon._speak_loop_once()
    assert speaker.spoken == ["Unknown session. Playing. 0 waiting."]


def test_where_am_i_reports_stopped_state():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon._stream("fg").stopped = True
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()                     # pause_exempt cue voices even when stopped
    assert speaker.spoken == ["work. Stopped. 0 waiting."]


def test_where_am_i_counts_waiting_background_sessions():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon._enqueue("bg1", "prose", "x", False)              # waiting
    daemon._enqueue("bg2", "prose", "y", False)              # waiting
    daemon._stream("bg3").stopped = True                     # stopped -> NOT counted
    daemon._enqueue("bg3", "prose", "z", False)
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()
    assert speaker.spoken == ["work. Playing. 2 waiting."]


def test_where_am_i_no_foreground_errors():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message({"type": "where_am_i"})
    assert speaker.earcons == ["error"]


def test_where_am_i_with_nothing_in_flight_still_barges_in():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    assert daemon._current_item is None
    daemon.handle_message({"type": "where_am_i", "session": "fg"})
    assert speaker.cancels == 1                   # §7 barge-in even with nothing playing
    daemon._speak_loop_once()
    assert speaker.spoken == ["work. Playing. 0 waiting."]


def test_where_am_i_resumes_interrupted_item_after_the_status_cue():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon._enqueue("fg", "prose", "interrupted sentence", False)
    fired = {"done": False}

    def interrupting(text, cancel_epoch=None):
        speaker.spoken.append(text)
        if not fired["done"]:
            fired["done"] = True
            daemon.handle_message({"type": "where_am_i", "session": "fg"})  # ⌃⌘W mid-utterance
            return False                          # ... and cancelled it
        return True

    speaker.speak = interrupting
    daemon._speak_loop_once()                     # speaks the item; ⌃⌘W barges in, cancels
    assert speaker.spoken == ["interrupted sentence"]
    assert daemon._current_item is None
    daemon._speak_loop_once()                     # the status cue plays FIRST
    assert speaker.spoken[-1] == "work. Playing. 0 waiting."
    daemon._speak_loop_once()                     # then reading resumes from the item's start
    assert speaker.spoken[-1] == "interrupted sentence"


def test_where_am_i_preserves_heard_marker_of_the_resumed_item():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    entry = daemon.history.record("fg", "prose", "hello")
    daemon._enqueue("fg", "prose", "hello", False, entry=entry)

    def interrupting(text, cancel_epoch=None):
        speaker.spoken.append(text)
        daemon.handle_message({"type": "where_am_i", "session": "fg"})
        speaker.speak = lambda t, cancel_epoch=None: (speaker.spoken.append(t) or True)
        return False

    speaker.speak = interrupting
    daemon._speak_loop_once()                     # hello interrupted by ⌃⌘W
    assert entry.heard is False
    assert entry in daemon._pending_heard.values()   # carried onto the re-queued item
    daemon._speak_loop_once()                     # status cue
    daemon._speak_loop_once()                     # hello resumes -> completes -> marked heard
    assert entry.heard is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_daemon_where_am_i.py -q`
Expected: FAIL — `where_am_i` is unhandled, so nothing is spoken / no earcon fires.

- [ ] **Step 3: Add the `WHERE_AM_I` message type**

In `src/sonari/protocol.py`, add after the `CYCLE_SESSION` line:

```python
    WHERE_AM_I = "where_am_i"   # ⌃⌘W: terse SPOKEN status (barge-in + interjection-resume)
```

- [ ] **Step 4: Add the `on_where_am_i` handler**

In `src/sonari/daemon/features/control.py`, add after `on_status` (and before `on_ping`):

```python
@handler(MsgType.WHERE_AM_I)
def on_where_am_i(ctx, msg):
    # ⌃⌘W "where am I": a terse SPOKEN status (distinct from the CLI STATUS dict),
    # barge-in + interjection-resume per §7. Plain text for sub-project B (spearcon /
    # pitch polish is sub-project D): "{folder}. {Playing|Stopped}. {N} waiting."
    host = ctx.host
    fg = host.sessions.foreground()
    if fg is None:
        host.speaker.earcon("error")              # always-confirm-fired: never a silent no-op
        return None
    # Capture the in-flight item BEFORE cancel so we can resume it afterwards.
    cur = host._current_item
    folder = host.sessions.folder(fg) or "Unknown session"
    st = host._streams.get(fg)
    state = "Stopped" if (st is not None and st.stopped) else "Playing"
    # Waiting = background sessions with live, non-stopped backlog (mirrors _waiting_target).
    waiting = sum(1 for sess, s in host._streams.items()
                  if sess != fg and not s.stopped and len(s.queue) > 0)
    text = "{0}. {1}. {2} waiting.".format(folder, state, waiting)
    host.speaker.cancel()                          # barge-in: cut the current utterance
    # Resume-after-interjection: re-queue the interrupted item at the front (BEHIND the
    # status cue), carrying its pending-heard entry on a FRESH item id so the speak
    # loop's note_spoken (which pops the OLD id with completed=False) can't lose it.
    if cur is not None:
        entry = host._pending_heard.get(cur.id)
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, mute_exempt=cur.mute_exempt,
                      pause_exempt=cur.pause_exempt, names_session=cur.names_session,
                      at_front=True)
    # Status cue at the very front (plays FIRST). pause_exempt so ⌃⌘W speaks even when the
    # foreground session is stopped; mute_exempt so it is never folder-prefixed.
    host._enqueue(fg, "prose", text, False,
                  mute_exempt=True, pause_exempt=True, at_front=True)
    return None
```

- [ ] **Step 5: Keep the registry guard in sync (28 → 29)**

In `src/sonari/daemon/__init__.py`:
- Change the comment `... all 28 known keys explicitly.` → `... all 29 known keys explicitly.`
- Add `MsgType.WHERE_AM_I,` to the `assert_complete([...])` list (after `MsgType.CYCLE_SESSION,`).

- [ ] **Step 6: Update the protocol + registry tests**

In `tests/test_protocol.py`, add `"WHERE_AM_I": "where_am_i",` to **both** `expected` dicts (after the `CYCLE_SESSION` entry added in Task 2).

In `tests/test_daemon_registry.py`:
- Rename `ALL_28` → `ALL_29` and add `_MsgType.WHERE_AM_I,` to the list.
- Rename `test_all_28_msgtypes_registered` → `test_all_29_msgtypes_registered`; update references `ALL_28` → `ALL_29`.
- In `test_negative_assert_complete_names_missing_type`, change `reg.assert_complete(ALL_28)` → `reg.assert_complete(ALL_29)`.

- [ ] **Step 7: Run the new + affected tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_daemon_where_am_i.py tests/test_protocol.py tests/test_daemon_registry.py -q`
Expected: PASS — including the two resume cases (`..._resumes_interrupted_item...`, `..._preserves_heard_marker...`).
Run: `.venv/bin/python -m pytest -q`
Expected: PASS (1 skipped).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(sonari): where-am-i (Ctrl+Cmd+W) spoken status with barge-in + resume"
```

---

## Task 4: ⌃⌘D targeting consistency

`on_jump_decision` targets `foreground()` today; every other nav hotkey routes through `focused_session() or foreground()` (see `on_nav`). Make ⌃⌘D act on the OS-focused session too — a 1-line behavior fix.

**Files:**
- Modify: `src/sonari/daemon/features/playback.py` (`on_jump_decision`, ~lines 94-97)
- Test: `tests/test_daemon_decisions.py`

**Interfaces:**
- Produces: `on_jump_decision` now resolves its target via `sessions.focused_session() or sessions.foreground()` (mirrors `on_nav`). No protocol/keymap change.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_daemon_decisions.py`:

```python
def test_jump_decision_targets_the_focused_session_not_foreground():
    # ⌃⌘D acts on the OS-focused session (like on_nav), not the voice's foreground —
    # so a decision-jump fired while looking at another terminal jumps THAT session.
    from sonari.sessions import Identity
    from tests.daemon_helpers import stream_queue
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B")
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttys9"))
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys9")
    assert sessions.focused_session() == "B"          # B is OS-focused; A owns the voice
    daemon._enqueue("B", "prose", "skip me", False)
    daemon._enqueue("B", "choice", "decide now", True)
    daemon.handle_message({"type": "jump_decision"})
    assert stream_queue(daemon, "B").pop_next().text == "decide now"   # B jumped, not A
    assert speaker.cancels == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_daemon_decisions.py::test_jump_decision_targets_the_focused_session_not_foreground -q`
Expected: FAIL — current `on_jump_decision` jumps `foreground()` ("A", empty queue), so B's leading prose is never discarded; `pop_next().text` is `"skip me"`, not `"decide now"`.

- [ ] **Step 3: Make the target match `on_nav`**

In `src/sonari/daemon/features/playback.py`, in `on_jump_decision`, replace:

```python
    fg = ctx.host.sessions.foreground()
    st = ctx.host._streams.get(fg)
    if st is not None:
        ctx.host._drop_pending(st.queue.jump_to_decision())
```

with:

```python
    sessions = ctx.host.sessions
    target = sessions.focused_session() or sessions.foreground()
    st = ctx.host._streams.get(target)
    if st is not None:
        ctx.host._drop_pending(st.queue.jump_to_decision())
```

(The current-item heard-marking above and `speaker.cancel()` below are unchanged.)

- [ ] **Step 4: Run the new test + the full suite**

Run: `.venv/bin/python -m pytest tests/test_daemon_decisions.py tests/test_daemon_control.py -q`
Expected: PASS — the existing `jump_decision` tests set no OS focus, so `focused_session()` is `None` and they fall back to `foreground()` (unchanged behavior).
Run: `.venv/bin/python -m pytest -q`
Expected: PASS (1 skipped).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "fix(sonari): jump-to-decision targets the focused session (matches nav)"
```

---

## Task 5: The keymap rewrite (ATOMIC)

One task so no intermediate state has a duplicate `(keyCode, modifiers)` pair. Adds keytable codes, wires the four new actions, rebinds response-nav to ⌃⌘↑/↓, frees ⌃⌘↑/↓ by unbinding `nav_first`/`nav_last`, and binds rate to ⌃⌘+/−. After this task the full B chord set resolves with no collision.

**Files:**
- Modify: `src/sonari/platform/macos/keytables.py` (`KEY_CODES`)
- Modify: `src/sonari/platform/macos/hotkeys.py` (`_KEY_DISPLAY_BY_NAME`, `extra_default_bindings()`)
- Modify: `src/sonari/keymap.py` (`ACTION_MESSAGES`, `_DEFAULT_KEYS`)
- Modify: `tests/test_keymap.py` (rewrite default-set tests; add B-binding tests)
- Modify: `tests/test_cli_hotkeyd.py` (repoint the `keymap … clear` test off `nav_first`)

**Interfaces:**
- Produces (default macOS keymap, all under ⌃⌘ unless noted): `nav_prev`=left, `nav_next`=right, `stop_session`=s, `stop_all`=m, `jump_waiting`=j, `jump_decision`=d, `where_am_i`=w, `faster`=equal, `slower`=minus; and via `extra_default_bindings()`: `nav_prev_response`=⌃⌘↑, `nav_next_response`=⌃⌘↓, `cycle_session_next`=⌃⌘Tab, `cycle_session_prev`=⌃⌘⇧Tab. `nav_first`/`nav_last` remain valid actions but ship UNBOUND.

- [ ] **Step 1: Write the failing keymap tests**

In `tests/test_keymap.py`:

(a) **Rewrite** `test_default_keymap_macos_uses_ctrl_cmd` to the B default set:

```python
def test_default_keymap_macos_uses_ctrl_cmd(mac):
    d = keymap.default_keymap()
    assert set(d.keys()) == {
        "nav_prev", "nav_next",
        "stop_session", "stop_all", "jump_waiting",
        "jump_decision", "where_am_i", "faster", "slower",
        "nav_prev_response", "nav_next_response",
        "cycle_session_next", "cycle_session_prev",
    }
    assert d["nav_next"]["key"] == "right" and d["nav_next"]["mods"] == ["ctrl", "cmd"]
    assert d["stop_session"]["key"] == "s" and d["stop_all"]["key"] == "m"
    assert d["jump_decision"]["key"] == "d" and d["where_am_i"]["key"] == "w"
    assert d["faster"]["key"] == "equal" and d["slower"]["key"] == "minus"
    # Sub-project B: nav_first/nav_last lose their default keys so ⌃⌘↑/↓ can own response-nav.
    assert "nav_first" not in d and "nav_last" not in d
    assert d["nav_prev_response"] == {"key": "up", "mods": ["ctrl", "cmd"]}
    assert d["nav_next_response"] == {"key": "down", "mods": ["ctrl", "cmd"]}
    assert d["cycle_session_next"] == {"key": "tab", "mods": ["ctrl", "cmd"]}
    assert d["cycle_session_prev"] == {"key": "tab", "mods": ["ctrl", "cmd", "shift"]}
```

(b) **Rewrite** `test_default_keymap_binds_only_nav_stop_keys` (faster/slower are now BOUND; nav_first/nav_last now UNBOUND):

```python
def test_default_keymap_binds_only_nav_stop_keys():
    km = keymap.default_keymap()
    assert {"nav_prev", "nav_next",
            "stop_session", "stop_all", "jump_waiting"} <= set(km.keys())
    assert set(km.keys()) <= set(keymap.ACTION_MESSAGES.keys())
    # nav_first/nav_last remain valid actions but ship UNBOUND after sub-project B.
    assert "nav_first" in keymap.ACTION_MESSAGES and "nav_first" not in km
    assert "nav_last" in keymap.ACTION_MESSAGES and "nav_last" not in km
```

(c) **Rewrite** `test_default_keymap_binds_nav_stop_keys` to drop nav_first/nav_last from the loop:

```python
def test_default_keymap_binds_nav_stop_keys():
    km = keymap.default_keymap()
    for action in ("nav_next", "nav_prev", "stop_session", "stop_all"):
        assert action in km, f"{action} has no default binding"
        assert km[action]["key"], f"{action} default binding has no key"
```

(d) **Repoint** `test_unbind_action_default_writes_unbound_override` off the now-unbound `nav_first` to a still-defaulted action:

```python
def test_unbind_action_default_writes_unbound_override(monkeypatch, tmp_path):
    km, _ = _patch_keymap_paths(monkeypatch, tmp_path)
    keymap.unbind_action("nav_prev")             # nav_prev HAS a default binding
    user = json.loads(km.read_text(encoding="utf-8"))
    assert user["nav_prev"]["key"] is None       # explicit unbound override
    resolved = keymap.resolve_keymap(keymap.load_keymap())
    assert "nav_prev" not in {e["action"] for e in resolved}
```

(e) **Extend** `test_macos_keytables_via_backend` to assert the new codes:

```python
def test_macos_keytables_via_backend(mac):
    kc, mm = keymap._keytables()
    for k in ("s", "r", "d", "l", "v", "o", ".", "]", "[", "w", "tab", "equal", "minus"):
        assert k in kc
    assert kc["s"] == 1 and kc["."] == 47 and kc["]"] == 30 and kc["["] == 33
    assert kc["w"] == 13 and kc["tab"] == 48 and kc["equal"] == 24 and kc["minus"] == 27
    assert mm["cmd"] == 256 and mm["shift"] == 512 and mm["ctrl"] == 4096
```

(f) **Add** the four new B tests (anywhere in the file):

```python
def test_response_nav_default_is_ctrl_cmd_arrows_no_shift(mac):
    d = keymap.default_keymap()
    assert d["nav_prev_response"] == {"key": "up", "mods": ["ctrl", "cmd"]}
    assert d["nav_next_response"] == {"key": "down", "mods": ["ctrl", "cmd"]}


def test_cycle_session_default_bindings_on_macos(mac):
    d = keymap.default_keymap()
    assert d["cycle_session_next"] == {"key": "tab", "mods": ["ctrl", "cmd"]}
    assert d["cycle_session_prev"] == {"key": "tab", "mods": ["ctrl", "cmd", "shift"]}


def test_b_action_messages_present():
    assert keymap.ACTION_MESSAGES["jump_decision"] == {"type": "jump_decision"}
    assert keymap.ACTION_MESSAGES["where_am_i"] == {"type": "where_am_i"}
    assert keymap.ACTION_MESSAGES["cycle_session_next"] == {
        "type": "cycle_session", "direction": "next"}
    assert keymap.ACTION_MESSAGES["cycle_session_prev"] == {
        "type": "cycle_session", "direction": "prev"}


def test_full_default_keymap_resolves_without_duplicate_hotkeys(mac):
    resolved = keymap.resolve_keymap(keymap.default_keymap())
    pairs = [(e["keyCode"], e["modifiers"]) for e in resolved]
    assert len(pairs) == len(set(pairs)), "duplicate (keyCode, modifiers) in default keymap"
    actions = {e["action"] for e in resolved}
    assert {"jump_decision", "where_am_i", "faster", "slower",
            "cycle_session_next", "cycle_session_prev",
            "nav_prev_response", "nav_next_response"} <= actions
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_keymap.py -q`
Expected: FAIL — new keys (`w`/`tab`/`equal`/`minus`) and new actions aren't in the tables/keymap yet; the rewritten default-set assertions don't match the current map.

- [ ] **Step 3: Add the new Carbon key codes**

In `src/sonari/platform/macos/keytables.py`, extend `KEY_CODES`:

```python
KEY_CODES = {
    "s": 1, "r": 15, "d": 2, "l": 37, "v": 9, "o": 31,   # 's' = stop_session
    "f": 3, "p": 35, "m": 46, "j": 38,  # 'p' free, 'm' = stop_all, 'j' = jump_waiting (kVK_ANSI_J)
    "w": 13,                            # 'w' = where_am_i (kVK_ANSI_W)
    "period": 47, ".": 47,
    "rightbracket": 30, "]": 30,
    "leftbracket": 33, "[": 33,
    "equal": 24, "+": 24,               # rate faster (kVK_ANSI_Equal; '+' alias, same physical key)
    "minus": 27, "-": 27,               # rate slower (kVK_ANSI_Minus; '-' alias)
    "tab": 48,                          # cycle sessions (kVK_Tab)
    # Arrow keys (Carbon virtual key codes), with aliases.
    "left": 123, "leftarrow": 123,
    "right": 124, "rightarrow": 124,
    "down": 125, "downarrow": 125,
    "up": 126, "uparrow": 126,
}
```

- [ ] **Step 4: Add display labels**

In `src/sonari/platform/macos/hotkeys.py`, extend `_KEY_DISPLAY_BY_NAME`:

```python
_KEY_DISPLAY_BY_NAME = {
    "s": "S", "r": "R", "d": "D", "l": "L", "v": "V", "o": "O",
    "f": "F", "p": "P", "m": "M", "j": "J", "w": "W",
    "period": ".", ".": ".",
    "rightbracket": "]", "]": "]",
    "leftbracket": "[", "[": "[",
    "equal": "=", "minus": "-", "tab": "Tab",
    "left": "Left", "right": "Right", "up": "Up", "down": "Down",
}
```

- [ ] **Step 5: Rebind response-nav + add cycle in `extra_default_bindings()`**

In `src/sonari/platform/macos/hotkeys.py`, replace `extra_default_bindings()` with:

```python
    def extra_default_bindings(self) -> dict:
        # Sub-project B nav grammar (Ctrl+Cmd base chord):
        #  - between-response nav = ⌃⌘↑ / ⌃⌘↓ (frees the old ⌃⌘⇧←/→ chord),
        #  - cycle sessions      = ⌃⌘Tab (next) / ⌃⌘⇧Tab (prev).
        base = list(self.default_mods())
        return {
            "nav_prev_response": {"key": "up", "mods": list(base)},
            "nav_next_response": {"key": "down", "mods": list(base)},
            "cycle_session_next": {"key": "tab", "mods": list(base)},
            "cycle_session_prev": {"key": "tab", "mods": base + ["shift"]},
        }
```

- [ ] **Step 6: Wire the actions + default keys**

In `src/sonari/keymap.py`, add the four new entries to `ACTION_MESSAGES` (after `jump_waiting`, before `faster`):

```python
    "jump_waiting": {"type": "jump_waiting"},  # switch voice to a waiting background session
    "jump_decision": {"type": "jump_decision"},   # ⌃⌘D: jump to the question/decision
    "cycle_session_next": {"type": "cycle_session", "direction": "next"},  # ⌃⌘Tab
    "cycle_session_prev": {"type": "cycle_session", "direction": "prev"},  # ⌃⌘⇧Tab
    "where_am_i": {"type": "where_am_i"},          # ⌃⌘W: terse spoken status
    "faster": {"type": "set_rate", "delta": 25},
    "slower": {"type": "set_rate", "delta": -25},
```

Then replace `_DEFAULT_KEYS` with (remove `nav_first`/`nav_last`; add `jump_decision`/`where_am_i`/`faster`/`slower`):

```python
_DEFAULT_KEYS = {
    "nav_prev": "left", "nav_next": "right",
    "stop_session": "s", "stop_all": "m", "jump_waiting": "j",
    "jump_decision": "d", "where_am_i": "w",
    "faster": "equal", "slower": "minus",
}
```

- [ ] **Step 7: Repoint the CLI keymap-clear test**

In `tests/test_cli_hotkeyd.py`, `test_keymap_clear_unbinds_and_requests_live_reload` clears `nav_first`, which no longer has a default binding (so `unbind_action` would just drop it, writing no `{"key": null}` override). Repoint it to a still-defaulted action:

```python
        rc = cli.main(["keymap", "nav_prev", "clear"])
    assert rc == 0
    user = json.loads((tmp_path / "keymap.json").read_text(encoding="utf-8"))
    assert user["nav_prev"]["key"] is None                  # unbound override written
```

(Leave `test_keymap_subcommand_prints_the_default_bindings` unchanged: the CLI lister iterates `ACTION_MESSAGES` [`cli/control.py:116`], so `nav_first`/`nav_last` still print as `(unbound)` and `faster`/`slower` still print — its assertions hold.)

- [ ] **Step 8: Run keymap + contract + cli tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_keymap.py tests/test_cli_hotkeyd.py tests/test_hotkeyd_contract.py -q`
Expected: PASS — including `test_hotkeyd_contract.py::test_all_action_messages_are_known_msgtypes` (every new `ACTION_MESSAGES` type now resolves to a real `MsgType`: `JUMP_DECISION` exists, `CYCLE_SESSION` from Task 2, `WHERE_AM_I` from Task 3) and `test_no_two_default_actions_share_a_key`.
Run: `.venv/bin/python -m pytest -q`
Expected: PASS (1 skipped). Investigate any failure — likely a lingering `nav_first`/`nav_last` default-binding assumption in another test.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(sonari): cockpit nav keymap (cycle/where-am-i/jump/rate; ⌃⌘↑↓ response-nav)"
```

---

## Self-Review

**Spec coverage (vs §15, the authoritative B section; §4 keymap; §6.3 nav; §6.5 ⌃⌘W; §7 barge-in/resume):**
- Within-response nav ⌃⌘←/→ — already correct (`nav_prev`/`nav_next` = left/right under ⌃⌘); ← re-reads via existing `prev`. No handler/keymap change. ✓ (verified: `keymap._DEFAULT_KEYS` keeps `nav_prev`=left / `nav_next`=right)
- Between-response nav rebind ⌃⌘⇧←/→ → **⌃⌘↑/↓** — Task 5 `extra_default_bindings()`; handler `_nav_response` unchanged. ✓
- ⌃⌘↑/↓ collision freed atomically by removing `nav_first`/`nav_last` from `_DEFAULT_KEYS` in the SAME task as the rebind (Task 5) — no intermediate duplicate `(keyCode, modifiers)`. ✓
- ⌃⌘D jump-to-decision — keymap binding added (Task 5) + 1-line `focused_session() or foreground()` consistency fix (Task 4). ✓
- ⌃⌘Tab / ⌃⌘⇧Tab cycle — one `CYCLE_SESSION` + `direction` (Task 2), `session_ids()` roster (Task 1), soft-switch handler with wrap and `<2`→error earcon. ✓
- ⌃⌘W where-am-I — new `WHERE_AM_I` + handler (Task 3), interjection-resume preserving the heard-marker, plain text `"{folder}. {Playing|Stopped}. {N} waiting."`. ✓
- ⌃⌘+/− rate — bind existing `faster`/`slower` (Task 5); `on_set_rate` already does NOT cancel → the §7 no-cut exception holds, no handler change. ✓
- Protocol inventory 27 → 29; `assert_complete` + comment + `test_daemon_registry` + `test_protocol` synced (Task 2: +CYCLE_SESSION; Task 3: +WHERE_AM_I). ✓
- **Out of scope (correctly deferred):** answer-via-hook ⌃⌘⏎/⎋ (C); spearcon synthesis + pitch (D). ⌃⌘W speaks plain status only. ✓

**Placeholder scan:** No "TBD"/"add error handling"/"write tests for the above". Every production hunk is shown in full against verbatim current source; every test edit is named by test + exact replacement. The one "investigate any failure" line (Task 5 Step 8) is bounded by the specific likely cause (a lingering `nav_first`/`nav_last` default assumption), not an open-ended placeholder.

**Type/name consistency across tasks:** `CYCLE_SESSION = "cycle_session"` / handler `on_cycle_session` / actions `cycle_session_next`+`cycle_session_prev` (both emit `{"type": "cycle_session", "direction": …}`) — one MsgType, two actions, consistent in protocol/handler/keymap/registry/test. `WHERE_AM_I = "where_am_i"` / `on_where_am_i` / action `where_am_i` — consistent. `session_ids()` named identically in Tasks 1-2. `jump_decision` action carries `{"type": "jump_decision"}` matching the existing `MsgType.JUMP_DECISION` + `on_jump_decision`. Default keys `faster`=`equal`(24) / `slower`=`minus`(27); keytable + display labels added for `w`/`tab`/`equal`/`minus`. The CLI `STATUS` dict path is untouched (⌃⌘W is a separate MsgType).

**Resolved ambiguities (surfaced):**
- *One MsgType vs two for cycle:* §15 + the decomposition mandate **one** `CYCLE_SESSION` with a `direction` field (→ 29). The recon's keymap subagent suggested two types (→ 30) — that is the rejected alternative; followed §15.
- *Cycle cue wording:* used the plain `"{folder}."` / `"Another session."` cue (matching `on_nav`'s soft cross-session cue and §2's "minimal spoken chrome"), not `on_jump_waiting`'s `"Jumping to …"`. "Mirror on_jump_waiting's cue pattern" (§15) = the flags + `cancel()`, not the literal string; `"Jumping to …"` stays reserved for ⌃⌘J.
- *⌃⌘W status-cue exemptions:* the cue is `mute_exempt=True, pause_exempt=True` so it is never folder-prefixed and still speaks when the foreground session is stopped; the resumed item carries `pause_exempt=cur.pause_exempt` so an interrupted control cue keeps its exemption on resume.

**Risk notes for the implementer:**
- Task 3 is the keystone: the interjection-resume race is solved by re-queuing on a **fresh item id** carrying the original `pending_heard` entry (so `note_spoken` pops only the OLD id) — both paths serialize on the daemon lock. The two resume tests (`..._resumes_interrupted_item...`, `..._preserves_heard_marker...`) are the canaries; do not weaken them.
- Task 5 must stay atomic: removing `nav_first`/`nav_last` from `_DEFAULT_KEYS` and rebinding response-nav to ⌃⌘↑/↓ in the same commit avoids a transient duplicate `(keyCode, modifiers)` that would fail `test_no_two_default_actions_share_a_key`.
- The Norwegian +/− physical position is verified at the on-hardware gate, not in pytest — ship ANSI `equal`/`minus`.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-06-27-sonari-cockpit-nav-grammar.md`. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent implements each task with two-stage review between tasks. Best fit: the tasks are sequenced with explicit interfaces, each ends with the full suite green, and Task 3 (the interjection-resume keystone) benefits from a fresh reviewer.
2. **Inline Execution** — implement the tasks in this session with review checkpoints.
