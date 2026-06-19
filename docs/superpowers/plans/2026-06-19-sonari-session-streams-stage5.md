# Sonari Session-Streams Stage 5 — Two-Level Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add response-to-response navigation (jump a whole answer at a time) on top of Stage 4's persistent transcript, so the user can move back through earlier responses and then step through the paragraphs within any of them — a true two-level navigation model, entirely in the arrow cluster.

**Architecture:** Two new `to` values (`prev_response` / `next_response`) on the existing `NAV` message — no new protocol type — bound to **`Ctrl+Cmd+Shift+←/→`** on macOS. A `SessionStream.nav_turn` anchor (None == the live turn) makes the existing within-response keys operate on whichever turn is anchored; a response jump moves the anchor, reads the target answer from its start, and speaks a relative orientation cue. New pure-history accessors (`turn_ids`, `message_ids_in_turn`) address turns and their messages.

**Tech Stack:** Python 3.9+ (stdlib only), pytest. Files: `src/sonari/history.py`, `src/sonari/keymap.py`, `src/sonari/protocol.py` (none — reuses NAV), `src/sonari/platform/base.py`, `src/sonari/platform/macos/hotkeys.py`, `src/sonari/session_stream.py`, `src/sonari/daemon.py`, and their tests.

## Global Constraints

Bind every task. From the spec (`docs/superpowers/specs/2026-06-19-sonari-session-streams-design.md` §5/§6/§8.3 item 5/§11) and the user's two brainstorm decisions.

- **Python 3.9 floor, stdlib-only core.** No new dependencies. `history.py` stays PURE (no I/O).
- **Suite green at every step.** Baseline before Task 1 (on `main` after Stage 4) = **716 passed, 2 skipped**. Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`. The 2 skips + the ignored module need the `[kokoro]`/numpy extra (absent in `.venv`); pre-existing.
- **Behavior-preserving until shipped.** The live daemon runs from `~/.sonari/app` (a copy); nothing reaches it until a future `sonari install`. No `sonari install` in this stage.
- **The two user-approved decisions (verbatim):**
  - **Bindings:** response-level nav = **`Ctrl+Cmd+Shift+←/→`** (`nav_prev_response` / `nav_next_response`). Arrow cluster + Shift; rebindable.
  - **Orientation:** on a response jump, a **relative** spoken cue precedes the replayed answer — **"N response(s) back."** — with boundary cues **"Oldest response."** (clamped at the oldest) and **"Back to the latest."** (returned to live). These cues are `mute_exempt` navigation feedback.
- **Within-response nav stays behavior-identical when not anchored** (`nav_turn is None`). Every existing nav test must stay green. The new keys are purely additive.
- **Keep Stage 3 features QUEUE-driven.** This stage touches navigation/history only; do not wire the waiting earcon / `jump_waiting` onto history.
- **`SESSION_END` still clears history; FLUSH still opens a turn** (Stage 4 — unchanged). FLUSH additionally snaps `nav_turn` back to live (via `reset_for_new_prompt`).
- **Do not reintroduce `catch_up` / `REPEAT`.** Read-only — navigation never re-triggers the agent.
- **Cross-platform binding:** macOS base chord is `["ctrl","cmd"]` (room for +Shift); **Windows base chord is `["ctrl","shift","alt"]` — Shift is already in it, so +Shift can't differentiate from within-nav and would COLLIDE.** Therefore response-nav ships **bound on macOS, UNBOUND on Windows** (revisit at Windows-box verification; an untested binding is worse than unbound). The actions still exist cross-platform so a user can bind them.
- **Do NOT push `main` or open a PR unless the user asks.** The git-push-guard hook blocks any command containing `git push` + `main`/`force` — keep those separate and user-initiated. Do not touch `docs/getting-started.md` or `.convergence-plan.md` (pre-existing untracked, not ours).

### Verified codebase facts (do not re-derive)

- `daemon.py` NAV handler (lines 475-480): `fg = self.sessions.foreground(); if fg is None: return None; self._nav(fg, msg.get("to", "prev"))`. **NAV operates on the FOREGROUND session, ignoring `msg["session"]`.** `_nav(self, session, to)` is at lines 738-777.
- `config.py`: `"minqueue": 1` (default) → single prose sentences flush straight to the queue, so a `final=True` single sentence is drainable from the queue in tests (matches existing `test_daemon_nav.py` patterns).
- `SessionStream.reset_for_new_prompt()` (`session_stream.py:25`) currently resets `assembler`, `prose_buffer`, `options`, `nav_cursor=None`, `waiting_signaled=False`. Stage 5 adds `nav_turn=None` here.
- `history.py` (post-Stage-4): `HistoryEntry` has `turn_id`; `_turn_id` map; `message_ids(session)` is turn-scoped to the current turn (filters `e.turn_id != self._turn_id.get(session,0)`, keeps the `seen`/`seq==0` truncated-head exclusion #8); `entries_for_message(session, msg_id)` is NOT turn-scoped (explicit-id lookup — Stage 5 relies on this for cross-turn replay); `start_turn` bumps `_turn_id`+`_msg_id`, resets `_group_seq`. msg_ids are globally monotonic, so each belongs to exactly one turn.
- `keymap.py`: `ACTION_MESSAGES` (action→message), `_DEFAULT_KEYS` (action→single key, uniform mods), `default_keymap()` (applies `get_platform().hotkey.default_mods()` to every `_DEFAULT_KEYS` action), `unbind_action(action)` (writes an explicit null override iff `action in _DEFAULT_KEYS`, else drops), `resolve_keymap`, `load_keymap`.
- `platform/base.py` `HotkeyBackend` has CONCRETE `key_codes()`/`mod_masks()`/`default_mods()` (overridden per-OS). macOS `default_mods()` = `["ctrl","cmd"]`; Windows = `["ctrl","shift","alt"]`. macOS keytable has `left=123,right=124,up=126,down=125`, `shift` mask = 512, `cmd`=256, `ctrl`=4096.
- Test helpers (verified):
  - `tests/test_history.py`: constructs `SessionHistory(...)` directly.
  - `tests/test_daemon_nav.py`: `_drain(queue)`, `_seed(daemon)`, `_nav(daemon, to)` (sends `{"type":"nav","to":to,"session":"fg"}`). Imports only `make_daemon`. Uses raw-dict messages.
  - `tests/test_keymap.py`: fixtures `mac` / `win` (force `platform.sys.platform` + reset `platform._CACHE`), `_patch_keymap_paths(monkeypatch, tmp_path)`. Imports `json`, `pytest`, `from sonari import keymap`, `import sonari.platform as platform`.
  - `tests/test_hotkeyd_contract.py`: `_msg(action_message, session="fg")`, `from sonari import keymap`, `from sonari.protocol import MsgType`, `make_daemon`.
  - `tests/daemon_helpers.py`: `make_daemon(verbosity="everything", foreground="fg")` → `(daemon, queue, speaker, sessions, config)`; `queue` is the foreground stream's own queue.

### Tests that encode the OLD binding set (must be UPDATED, spec §9)

- `tests/test_keymap.py::test_default_keymap_macos_uses_ctrl_cmd` (line 62) — exact-set assertion on `default_keymap()` keys; Stage 5 adds `nav_prev_response`/`nav_next_response` on macOS. Update the set + assert their Shift binding.
- `tests/test_keymap.py::test_default_keymap_binds_only_nav_pause_mute` (line 102) — exact-set assertion with NO platform fixture (host-dependent); since the default key SET is now platform-dependent (bound on mac, unbound on win), relax `==` to `<=` for the always-bound core (its real intent is "faster/slower unbound").
- `tests/test_keymap.py::test_default_keymap_windows_uses_ctrl_shift_alt` (line 71) — stays green; ADD `assert "nav_prev_response" not in d` to lock unbound-on-Windows.
- **Stay green (verified, do not touch):** `test_hotkeyd_contract.py::test_all_action_messages_are_known_msgtypes` (checks `message["type"]` only; new type is `"nav"`), `test_cli_hotkeyd.py::test_keymap_subcommand_prints_the_default_bindings` (presence checks, not exact set), `test_default_keymap_binds_nav_pause_mute` (subset check), `test_unbind_action_*` (nav_first stays defaulted, faster stays non-default).

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/sonari/history.py` | Pure transcript | Add `turn_ids()` + `message_ids_in_turn()`; refactor `message_ids()` to delegate |
| `src/sonari/keymap.py` | Hotkey logic | Add 2 response-nav actions to `ACTION_MESSAGES`; merge per-platform `extra_default_bindings()` into `default_keymap()`; make `unbind_action` platform-aware |
| `src/sonari/platform/base.py` | Backend ABCs | Add concrete `HotkeyBackend.extra_default_bindings() -> {}` |
| `src/sonari/platform/macos/hotkeys.py` | macOS hotkeys | Override `extra_default_bindings()` → Shift+arrows for response-nav |
| `src/sonari/session_stream.py` | Per-session state | Add `nav_turn` (reset in `reset_for_new_prompt`) |
| `src/sonari/daemon.py` | Message handling | Generalize `_nav` to the anchored turn + stale-anchor fallback; add `_nav_response`; route the new `to` values in the NAV handler |
| `tests/*` | | New + updated tests per task |

**Dependency order is strict and sequential: 4 ← (1,2,3), 3 ← 1, 2 independent.** Run as 1 → 2 → 3 → 4.

---

### Task 1: History turn accessors (`turn_ids`, `message_ids_in_turn`)

Add the two pure accessors two-level nav needs. Refactor `message_ids` to delegate (behavior-preserving). This resolves the second half of the §7 seam (turn addressing).

**Files:**
- Modify: `src/sonari/history.py` (`message_ids`; add `message_ids_in_turn`, `turn_ids`)
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: `HistoryEntry.turn_id`, `_turn_id` (Stage 4).
- Produces:
  - `message_ids_in_turn(session: str, turn_id: int) -> list` — distinct message ids of that turn, oldest first, with the same `seq==0` truncated-head exclusion as `message_ids`.
  - `turn_ids(session: str) -> list` — navigable turn ids oldest→newest (a turn is navigable iff `message_ids_in_turn` is non-empty).
  - `message_ids(session)` unchanged in behavior (now delegates for the current turn).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_history.py`:

```python
def test_message_ids_in_turn_returns_that_turns_groups():
    h = SessionHistory()
    h.record("s", "prose", "t0a"); h.end_message("s")
    h.record("s", "prose", "t0b"); h.end_message("s")
    h.start_turn("s")                                       # -> turn 1
    h.record("s", "prose", "t1a")
    t0 = h.message_ids_in_turn("s", 0)
    t1 = h.message_ids_in_turn("s", 1)
    assert len(t0) == 2 and len(t1) == 1
    assert [e.text for e in h.entries_for_message("s", t1[0])] == ["t1a"]
    assert h.message_ids_in_turn("s", 99) == []             # no such turn


def test_message_ids_delegates_to_current_turn():
    h = SessionHistory()
    h.record("s", "prose", "t0a"); h.end_message("s")
    h.start_turn("s")
    h.record("s", "prose", "t1a")
    # message_ids == message_ids of the live turn (regression: Stage 4 behavior kept)
    assert h.message_ids("s") == h.message_ids_in_turn("s", 1)


def test_turn_ids_lists_navigable_turns_oldest_first():
    h = SessionHistory()
    h.record("s", "prose", "a"); h.end_message("s")         # turn 0
    h.start_turn("s"); h.record("s", "prose", "b"); h.end_message("s")   # turn 1
    h.start_turn("s"); h.record("s", "prose", "c")          # turn 2
    assert h.turn_ids("s") == [0, 1, 2]
    assert h.turn_ids("missing") == []


def test_turn_ids_excludes_evicted_and_truncated_turns():
    # cap small enough that turn 0's group HEAD evicts -> turn 0 becomes a fragment
    # (no seq==0 head present) -> not navigable -> excluded from turn_ids.
    h = SessionHistory(cap=3)
    h.record("s", "prose", "a1"); h.record("s", "prose", "a2")
    h.record("s", "prose", "a3")                            # turn 0: one group, 3 entries
    h.start_turn("s")                                       # turn 1 (bumps msg_id -> fresh group)
    h.record("s", "prose", "b1")                            # evicts a1 (turn 0 head)
    assert h.message_ids_in_turn("s", 0) == []              # turn 0 truncated
    assert h.turn_ids("s") == [1]                           # turn 0 excluded
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_history.py -k "in_turn or delegates or turn_ids" -v`
Expected: FAIL — `'SessionHistory' object has no attribute 'message_ids_in_turn'` / `turn_ids`.

- [ ] **Step 3: Implement the accessors**

In `src/sonari/history.py`, REPLACE the whole `message_ids` method with these three methods (the new `message_ids_in_turn`, the delegating `message_ids`, and `turn_ids`):

```python
    def message_ids_in_turn(self, session: str, turn_id: int) -> list:
        """Distinct message ids of the given turn, oldest first. Same truncated-head
        exclusion as `message_ids` (#8): a group whose head was evicted by the rolling
        cap is excluded so nav never replays a fragment. Powers within-response nav
        over any turn — current or past (Stage 5 two-level navigation)."""
        d = self._entries.get(session)
        if not d:
            return []
        ids = []
        seen = set()
        for e in d:
            if e.turn_id != turn_id:
                continue
            if e.msg_id in seen:
                continue
            seen.add(e.msg_id)
            if e.seq == 0:
                ids.append(e.msg_id)
        return ids

    def message_ids(self, session: str) -> list:
        """Distinct message ids of the CURRENT turn, oldest first (the live response).
        Bounded to the current turn so the single-level within-response nav never walks
        into prior turns (Stage 4). Delegates to `message_ids_in_turn` for the live turn;
        `message_ids_in_turn` serves any past turn for Stage 5's two-level nav."""
        return self.message_ids_in_turn(session, self._turn_id.get(session, 0))

    def turn_ids(self, session: str) -> list:
        """Navigable turn ids for the session, oldest first. A turn is navigable iff it
        still has at least one present message-group head (`message_ids_in_turn` non-empty)
        — a turn whose entries were entirely evicted, or whose only survivors are mid-group
        fragments, is excluded. Powers response-to-response navigation (Stage 5)."""
        d = self._entries.get(session)
        if not d:
            return []
        ordered = []
        seen = set()
        for e in d:
            if e.turn_id not in seen:
                seen.add(e.turn_id)
                ordered.append(e.turn_id)
        return [t for t in ordered if self.message_ids_in_turn(session, t)]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_history.py -v`
Expected: PASS — all existing history tests (including the #8 truncation test, now via the delegated path) + the 4 new ones.

- [ ] **Step 5: Run the full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **720 passed, 2 skipped** (716 + 4). No regressions — `message_ids` behavior is unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/sonari/history.py tests/test_history.py
git commit -m "feat(history): add turn_ids + message_ids_in_turn accessors (Stage 5 Task 1)"
```

---

### Task 2: Protocol + keymap + platform binding defaults

Add the two response-nav actions and their macOS `Ctrl+Cmd+Shift+←/→` defaults (Windows unbound). No new protocol message type — they are `to` values on `NAV`.

**Files:**
- Modify: `src/sonari/keymap.py` (`ACTION_MESSAGES`, `default_keymap`, `unbind_action`)
- Modify: `src/sonari/platform/base.py` (`HotkeyBackend.extra_default_bindings`)
- Modify: `src/sonari/platform/macos/hotkeys.py` (override `extra_default_bindings`)
- Test: `tests/test_keymap.py`

**Interfaces:**
- Consumes: `get_platform().hotkey.default_mods()`.
- Produces:
  - `ACTION_MESSAGES["nav_prev_response"] == {"type": "nav", "to": "prev_response"}`, `["nav_next_response"] == {"type": "nav", "to": "next_response"}`.
  - `HotkeyBackend.extra_default_bindings() -> dict` (default `{}`); macOS returns the two response-nav bindings with `["ctrl","cmd","shift"]`.
  - `default_keymap()` includes the response-nav actions on macOS, not on Windows.
  - `unbind_action` recognizes platform-defaulted actions (keys off `default_keymap()`).

- [ ] **Step 1: Write/update the failing tests**

In `tests/test_keymap.py`, REPLACE `test_default_keymap_macos_uses_ctrl_cmd` (lines ~62-68) with:

```python
def test_default_keymap_macos_uses_ctrl_cmd(mac):
    d = keymap.default_keymap()
    assert set(d.keys()) == {"nav_prev", "nav_next", "nav_first", "nav_last",
                             "pause", "mute", "pin_toggle", "jump_waiting",
                             "nav_prev_response", "nav_next_response"}
    assert d["nav_next"]["key"] == "right" and d["nav_next"]["mods"] == ["ctrl", "cmd"]
    assert d["pause"]["key"] == "s" and d["mute"]["key"] == "m"
    # Stage 5: response-level nav = Ctrl+Cmd+Shift+arrows
    assert d["nav_prev_response"] == {"key": "left", "mods": ["ctrl", "cmd", "shift"]}
    assert d["nav_next_response"] == {"key": "right", "mods": ["ctrl", "cmd", "shift"]}
```

REPLACE `test_default_keymap_windows_uses_ctrl_shift_alt` (lines ~71-74) with:

```python
def test_default_keymap_windows_uses_ctrl_shift_alt(win):
    d = keymap.default_keymap()
    assert d["nav_next"]["mods"] == ["ctrl", "shift", "alt"]
    assert d["mute"]["key"] == "m"
    # Stage 5: Windows base chord already includes Shift, so +Shift can't differentiate
    # response-nav from within-nav -> ships UNBOUND on Windows (rebindable by the user).
    assert "nav_prev_response" not in d and "nav_next_response" not in d
```

REPLACE `test_default_keymap_binds_only_nav_pause_mute` (lines ~102-110) with (relax `==` to `<=` — the default key SET is now platform-dependent; the test's intent is "faster/slower ship unbound"):

```python
def test_default_keymap_binds_only_nav_pause_mute():
    # The always-bound core is present on every platform; faster/slower ship UNBOUND.
    km = keymap.default_keymap()
    assert {"nav_prev", "nav_next", "nav_first", "nav_last",
            "pause", "mute", "pin_toggle", "jump_waiting"} <= set(km.keys())
    assert set(km.keys()) <= set(keymap.ACTION_MESSAGES.keys())
    assert "faster" in keymap.ACTION_MESSAGES and "faster" not in km
    assert "slower" in keymap.ACTION_MESSAGES and "slower" not in km
```

ADD these new tests:

```python
def test_response_nav_action_messages():
    assert keymap.ACTION_MESSAGES["nav_prev_response"] == {"type": "nav", "to": "prev_response"}
    assert keymap.ACTION_MESSAGES["nav_next_response"] == {"type": "nav", "to": "next_response"}


def test_response_nav_resolves_with_shift_on_macos(mac):
    resolved = keymap.resolve_keymap(
        {"nav_prev_response": {"key": "left", "mods": ["ctrl", "cmd", "shift"]}})
    row = resolved[0]
    assert row["action"] == "nav_prev_response"
    assert row["keyCode"] == 123                                  # left arrow (Carbon)
    assert row["modifiers"] == (4096 | 256 | 512)                 # ctrl | cmd | shift
    assert json.loads(row["message"]) == {"type": "nav", "to": "prev_response"}


def test_unbind_response_nav_on_macos_writes_unbound_override(mac, monkeypatch, tmp_path):
    km, _ = _patch_keymap_paths(monkeypatch, tmp_path)
    keymap.unbind_action("nav_prev_response")    # mac-defaulted -> explicit null override
    user = json.loads(km.read_text(encoding="utf-8"))
    assert user["nav_prev_response"]["key"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_keymap.py -k "macos or windows or response_nav or binds_only or unbind_response" -v`
Expected: FAIL — `nav_prev_response` not in `ACTION_MESSAGES` / not in the macOS `default_keymap()` / `extra_default_bindings` missing.

- [ ] **Step 3: Implement**

In `src/sonari/keymap.py`, add to `ACTION_MESSAGES` (after the `nav_last` entry, keeping the nav group together):

```python
    # Response-to-response navigation (Stage 5): jump a whole turn at a time. Two new
    # `to` values on the existing NAV message (no new protocol type).
    "nav_prev_response": {"type": "nav", "to": "prev_response"},
    "nav_next_response": {"type": "nav", "to": "next_response"},
```

Replace `default_keymap()` with:

```python
def default_keymap() -> dict:
    """The default action->binding map for the active platform (per-OS chord).

    The `_DEFAULT_KEYS` actions all share the platform's `default_mods()` chord.
    `extra_default_bindings()` adds any per-platform binding that the uniform chord
    can't express (Stage 5: response-nav needs +Shift over the arrows on macOS; on
    platforms whose base chord already includes Shift it returns {} -> unbound)."""
    from sonari.platform import get_platform
    hk = get_platform().hotkey
    mods = hk.default_mods()
    out = {action: {"key": key, "mods": list(mods)}
           for action, key in _DEFAULT_KEYS.items()}
    out.update(hk.extra_default_bindings())
    return out
```

In `unbind_action`, change the default-detection from `_DEFAULT_KEYS` to the actual default map (platform-aware — so a mac-defaulted response-nav action gets an explicit null override, not a silent drop):

```python
def unbind_action(action: str) -> None:
    """Persist 'no hotkey' for *action* in the user's keymap.json. If the action has a
    default binding ON THIS PLATFORM, write an explicit unbound override ({"key": null})
    so it overrides that default; if it has no default, just drop any user binding.
    Raises ValueError for an unknown action."""
    if action not in ACTION_MESSAGES:
        raise ValueError("unknown action: {0}".format(action))
    user = _read_user_keymap()
    if action in default_keymap():
        user[action] = {"key": None, "mods": []}
    else:
        user.pop(action, None)
    _write_user_keymap(user)
```

In `src/sonari/platform/base.py`, add a concrete method to `HotkeyBackend` (next to `default_mods`):

```python
    def extra_default_bindings(self) -> "dict":
        """Per-platform default bindings for actions whose chord the uniform
        default_mods() can't express (e.g. response-level nav needs +Shift over the
        arrows on macOS). Maps action -> {"key", "mods"}. Default: none — the action
        ships UNBOUND on this platform until the user binds it."""
        return {}
```

In `src/sonari/platform/macos/hotkeys.py`, override it on `MacHotkeyBackend`:

```python
    def extra_default_bindings(self) -> dict:
        # Stage 5: response-level nav = Ctrl+Cmd+Shift+arrows (the base Ctrl+Cmd chord
        # has room for +Shift, which differentiates it from within-response ←/→).
        mods = list(self.default_mods()) + ["shift"]
        return {
            "nav_prev_response": {"key": "left", "mods": mods},
            "nav_next_response": {"key": "right", "mods": mods},
        }
```

(Windows inherits the ABC default `{}` — response-nav unbound; revisit at Windows verification.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_keymap.py tests/test_hotkeyd_contract.py tests/test_cli_hotkeyd.py -v`
Expected: PASS — updated keymap tests, the 3 new ones, and the contract/CLI tests (which only do presence/type checks) all green.

- [ ] **Step 5: Run the full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **723 passed, 2 skipped** (720 + 3 new; 3 tests updated in place). Reconcile to GREEN if the integer differs.

- [ ] **Step 6: Commit**

```bash
git add src/sonari/keymap.py src/sonari/platform/base.py src/sonari/platform/macos/hotkeys.py tests/test_keymap.py
git commit -m "feat(keymap): add response-nav actions + macOS Ctrl+Cmd+Shift+arrows (Stage 5 Task 2)"
```

---

### Task 3: `SessionStream.nav_turn` anchor + generalize within-nav + stale-anchor fallback

Add the turn anchor and make the existing within-response keys operate on it. Behavior-preserving while `nav_turn is None` (no production path sets it until Task 4) — so this task is validated by directly setting `nav_turn` in tests. Includes the **stale-anchor fallback**: if the anchored turn was evicted by the rolling cap, within-nav falls back to the live turn instead of announcing "nothing to navigate."

**Files:**
- Modify: `src/sonari/session_stream.py` (`__init__`, `reset_for_new_prompt`)
- Modify: `src/sonari/daemon.py` (`_nav`)
- Test: `tests/test_daemon_nav.py`

**Interfaces:**
- Consumes: `history.turn_ids`, `history.message_ids_in_turn` (Task 1).
- Produces: `SessionStream.nav_turn` (`None` == live turn; reset to `None` by `reset_for_new_prompt`). `_nav` operates on the anchored turn, with a stale-anchor → live fallback.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_daemon_nav.py`:

```python
def test_flush_resets_nav_turn_anchor():
    # A new prompt snaps the response anchor back to live (Stage 5).
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon._stream("fg").nav_turn = 5
    daemon.handle_message({"type": "flush", "session": "fg"})
    assert daemon._stream("fg").nav_turn is None


def test_within_nav_operates_on_anchored_past_turn():
    # With the anchor on a past turn, within-response nav reads THAT turn, not live.
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "T0 a.", "index": 0, "final": True})   # turn 0
    daemon.handle_message({"type": "flush", "session": "fg"})              # -> turn 1 (live)
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "T1 a.", "index": 0, "final": True})
    _drain(queue)
    daemon._stream("fg").nav_turn = 0          # anchor on the PAST turn
    daemon._stream("fg").nav_cursor = None
    _nav(daemon, "first")
    assert [s.text for s in _drain(queue)] == ["T0 a."]   # the anchored turn, not "T1 a."


def test_within_nav_falls_back_to_live_when_anchor_turn_evicted():
    # Stage 5 (anchor-eviction guard): if the anchored turn was evicted by the rolling
    # cap mid-session, within-nav falls back to the live turn rather than announcing empty.
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Live one.", "index": 0, "final": True})
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Live two.", "index": 1, "final": True})
    _drain(queue)
    daemon._stream("fg").nav_turn = 999        # an anchor that no longer exists
    daemon._stream("fg").nav_cursor = None
    _nav(daemon, "first")
    assert [s.text for s in _drain(queue)] == ["Live one.", "Live two."]   # navigated LIVE
    assert daemon._stream("fg").nav_turn is None                           # anchor cleared
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_nav.py -k "anchor or anchored or falls_back" -v`
Expected: FAIL — `test_flush_resets_nav_turn_anchor` & co. fail because `SessionStream` has no `nav_turn` (AttributeError) / `_nav` doesn't consult it.

- [ ] **Step 3: Implement**

In `src/sonari/session_stream.py`, add the field in `__init__` (after `nav_cursor`):

```python
        self.nav_turn = None                # two-level nav anchor: the turn being navigated
                                            # (None == the live turn); a new prompt snaps it back
```

And reset it in `reset_for_new_prompt` (add the line next to `self.nav_cursor = None`):

```python
        self.nav_cursor = None
        self.nav_turn = None
```

In `src/sonari/daemon.py`, REPLACE the head of `_nav` — from the method signature through the `ids = ...` / empty-guard — generalizing it to the anchored turn with the stale-anchor fallback. Specifically replace:

```python
    def _nav(self, session: str, to: str) -> None:
        """Move the per-session message cursor and play from there to the end.
        ...docstring...
        Newly streamed prose enqueues after these and continues seamlessly."""
        ids = self.history.message_ids(session)
        if not ids:
            self._enqueue(session, "prose", "Nothing to navigate yet.", False)
            return
        n = len(ids)
        cur_id = self._stream(session).nav_cursor
        cur = ids.index(cur_id) if cur_id in ids else n - 1
```

with (keep the existing docstring body, just add the anchor note; the load-bearing change is the `ids` computation + fallback):

```python
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
```

Leave the rest of `_nav` (the `if to == ...` branch, the `if new >= n - 1` cursor update, the `self.speaker.cancel()`, `self._drop_pending(...)`, and the seek-and-play loop) UNCHANGED — but note the seek-and-play loop already uses `self._stream(session)`; ensure it now reads through `st` consistently is NOT required (the existing `self._stream(session)` calls return the same object). Do not rewrite those lines.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_nav.py -v`
Expected: PASS — the 3 new anchor tests AND every existing nav test (they all run with `nav_turn is None`, so behavior is identical).

- [ ] **Step 5: Run the full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **726 passed, 2 skipped** (723 + 3). No regressions.

- [ ] **Step 6: Commit**

```bash
git add src/sonari/session_stream.py src/sonari/daemon.py tests/test_daemon_nav.py
git commit -m "feat(daemon): nav_turn anchor + within-nav over anchored turn with stale-anchor fallback (Stage 5 Task 3)"
```

---

### Task 4: `_nav_response` handler + NAV dispatch + orientation cues

Add response-to-response navigation: move the turn anchor, read the target response from its start, speak the relative orientation cue, clamp at the edges. Route the new `to` values in the NAV handler. Pin the live-prose-while-parked behavior.

**Files:**
- Modify: `src/sonari/daemon.py` (NAV handler dispatch; add `_nav_response`)
- Test: `tests/test_daemon_nav.py`, `tests/test_hotkeyd_contract.py`

**Interfaces:**
- Consumes: `history.turn_ids`, `history.message_ids_in_turn`, `history.entries_for_message` (Task 1); `SessionStream.nav_turn` (Task 3); `ACTION_MESSAGES["nav_prev_response"/"nav_next_response"]` (Task 2).
- Produces: `_nav_response(session, direction)` where `direction in ("prev_response","next_response")`; the NAV handler routes those `to` values to it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_daemon_nav.py` (helper builds N responses, each its own turn):

```python
def _responses(daemon, session, texts):
    # Each FLUSH opens a new turn; each prose is that turn's single response.
    for i, t in enumerate(texts):
        daemon.handle_message({"type": "flush", "session": session})
        daemon.handle_message({"type": "prose", "session": session,
                               "delta": t, "index": i, "final": True})


def test_prev_response_reads_previous_response_with_relative_cue():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["R1.", "R2.", "R3."])        # turns: live = R3
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})
    assert [s.text for s in _drain(queue)] == ["1 response back.", "R2."]


def test_prev_response_clamps_at_oldest_with_boundary_cue():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["R1.", "R2.", "R3."])
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})  # R2
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})  # R1 (oldest)
    assert [s.text for s in _drain(queue)] == ["Oldest response.", "R1."]
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})  # clamp
    assert [s.text for s in _drain(queue)] == ["Oldest response.", "R1."]


def test_next_response_returns_to_latest_with_boundary_cue():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["R1.", "R2.", "R3."])
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})  # R2
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "next_response", "session": "fg"})  # back to live R3
    assert [s.text for s in _drain(queue)] == ["Back to the latest.", "R3."]
    assert daemon._stream("fg").nav_turn is None           # anchored back to live


def test_response_nav_with_one_response_says_no_other():
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["Only."])
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})
    assert [s.text for s in _drain(queue)] == ["No other response."]


def test_response_nav_with_no_history_says_nothing_to_navigate():
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})
    assert [s.text for s in _drain(queue)] == ["Nothing to navigate yet."]


def test_live_prose_while_parked_on_past_response_enqueues_after_replay():
    # Advisor pin: parked on a past response, new live prose for the live turn enqueues
    # AFTER the replayed items (no buffering, no yank to live). Same invariant as
    # within-turn nav's "streaming continues after replay".
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["R1.", "R2.", "R3."])
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})  # park on R2
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Live more.", "index": 9, "final": True})        # live (R3) prose
    texts = [s.text for s in _drain(queue)]
    assert "R2." in texts and "Live more." in texts
    assert texts.index("Live more.") > texts.index("R2.")    # after the replay, not interleaved
    assert daemon._stream("fg").nav_turn is not None         # still parked, not yanked to live
```

In `tests/test_hotkeyd_contract.py`, ADD (proves the hotkey bytes drive a real response jump):

```python
def test_response_nav_action_messages_drive_a_jump():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    for i, t in enumerate(["A.", "B."]):
        daemon.handle_message(_msg({"type": "flush"}))
        daemon.handle_message(_msg({"type": "prose", "delta": t, "index": i, "final": True}))
    # drain live playback
    while queue.pop_next() is not None:
        pass
    daemon.handle_message(_msg(keymap.ACTION_MESSAGES["nav_prev_response"]))
    texts = []
    while True:
        it = queue.pop_next()
        if it is None:
            break
        texts.append(it.text)
    assert "A." in texts                              # jumped back to the previous response
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_nav.py -k "response" tests/test_hotkeyd_contract.py -k "response_nav" -v`
Expected: FAIL — the NAV handler passes `prev_response`/`next_response` to `_nav` (which falls through its `if to == ...` chain to `else: return` and does nothing), so nothing is enqueued / no cue.

- [ ] **Step 3: Implement**

In `src/sonari/daemon.py`, REPLACE the NAV handler (lines ~475-480) to route the new `to` values:

```python
        if t == MsgType.NAV:
            fg = self.sessions.foreground()
            if fg is None:
                return None
            to = msg.get("to", "prev")
            if to in ("prev_response", "next_response"):
                self._nav_response(fg, to)
            else:
                self._nav(fg, to)
            return None
```

Add the `_nav_response` method immediately after `_nav` (before `_resume`):

```python
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
        is_live = (new_idx == len(turns) - 1)
        st.nav_turn = None if is_live else target_turn
        # Relative orientation cue; boundary cues take precedence (Nima's decision).
        if is_live:
            cue = "Back to the latest."
        elif new_idx == 0:
            cue = "Oldest response."
        else:
            back = (len(turns) - 1) - new_idx
            cue = "{0} response{1} back.".format(back, "" if back == 1 else "s")
        mids = self.history.message_ids_in_turn(session, target_turn)
        # Anchor the cursor at the START of the target response; None == follow live.
        st.nav_cursor = None if is_live else (mids[0] if mids else None)
        self.speaker.cancel()
        self._drop_pending(st.queue.clear())
        self._enqueue(session, "prose", cue, False, mute_exempt=True)
        for mid in mids:
            for e in self.history.entries_for_message(session, mid):
                self._enqueue(session, e.kind, e.text, False, entry=e)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_nav.py tests/test_hotkeyd_contract.py -v`
Expected: PASS — all response-nav tests, the contract test, and every existing nav/contract test.

- [ ] **Step 5: Run the full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **~734 passed, 2 skipped** (726 + 7 nav + 1 contract). Reconcile to GREEN if the integer differs (the binding requirement is green with zero unexpected failures).

- [ ] **Step 6: Commit**

```bash
git add src/sonari/daemon.py tests/test_daemon_nav.py tests/test_hotkeyd_contract.py
git commit -m "feat(daemon): response-to-response navigation with relative orientation cues (Stage 5 Task 4)"
```

---

## Self-Review

**1. Spec coverage** (`design.md` §5/§6/§8.3 item 5):
- Two new `to` values on `NAV`, bound `Ctrl+Cmd+Shift+←/→` → Tasks 2 + 4. ✓
- `nav_turn` anchor, snapped to live by `reset_for_new_prompt` → Task 3. ✓
- Within-response nav operates on the anchored turn → Task 3. ✓
- Response jump reads the whole target response from its start (seek-and-play) + clamps → Task 4. ✓
- Relative orientation cue "N response(s) back." + boundary cues "Oldest response." / "Back to the latest." (`mute_exempt`) → Task 4. ✓
- History accessors `turn_ids` + `message_ids_in_turn`; `entries_for_message` stays cross-turn → Task 1. ✓
- Read-only; streaming never moves the anchor → Task 3 (anchor only moves on `_nav_response`/FLUSH) + the Task 4 live-prose pin test. ✓
- Cross-platform: macOS bound, Windows unbound (collision avoided) → Task 2, locked by the Windows keymap test. ✓
- **Advisor constraints:** (1) stale-anchor fallback in the within-nav path → Task 3 `test_within_nav_falls_back_to_live_when_anchor_turn_evicted`; (2) live-prose-while-parked pinned → Task 4 test; (3) keymap-completeness tests updated + `unbind_action` change covered → Task 2. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code step shows complete code. ✓

**3. Type/name consistency:** `nav_turn`, `turn_ids`, `message_ids_in_turn`, `extra_default_bindings`, `_nav_response`, `nav_prev_response`/`nav_next_response`, `prev_response`/`next_response` used identically across tasks. NAV handler uses `foreground()` (verified). Cue strings exact: `"N response(s) back."` / `"Oldest response."` / `"Back to the latest."` / `"No other response."` / `"Nothing to navigate yet."`. Test helpers match the verified files. ✓

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-19-sonari-session-streams-stage5.md`.

**Recommended execution:** superpowers:subagent-driven-development on a branch off `main` (`feat/session-streams-stage5`), sequential (Tasks 4←(1,2,3); 3←1; 2 independent — but run 1→2→3→4 since 3/4 share `daemon.py`). Per-task adversarial review; opus whole-branch review at the end (mutation-check the stale-anchor fallback and the orientation-cue boundary logic). Suite green at every step. Do not push unless asked.
