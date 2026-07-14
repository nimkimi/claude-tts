# Sonari Session-Chooser Navigation (⌃⌘Tab hold-to-browse) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Authored from the ratified design oracle `docs/superpowers/specs/2026-07-14-sonari-session-chooser-design.md` (read it first — it is the single source of truth). All `file:line` quotes below are verified against `HEAD = 3430cbf` on branch `design/voice-arbitration`.

**Goal:** Replace the pin-prone `CYCLE_SESSION` ring with the **chooser**: hold ⌃⌘, tap Tab to browse spoken previews that move NOTHING; release ⌃ or ⌘ to commit (the old cycle-landing semantics verbatim); digits 1–9 teleport by stable session number. Plus: stable lowest-free **session numbers** + an **MRU recency list** in `SessionManager`, numbers in the ⌃⌘W clauses, a ⌃⌘W **double-press roster**, and a verbosity-gated **registration announce**. The owner mandated a **clean dead-code sweep** of `CYCLE_SESSION` (protocol entry, handler, keymap rows, hotkeyd path, superseded tests) with behavioral coverage MIGRATING to chooser tests. F-RETEST-1 is CLOSED by this design.

**Architecture:** Browsing state is one `ChooserState` object on the daemon (`host._chooser`), mutated only under the existing daemon lock (every handler runs inside `_state.transaction()`), advanced purely by `CHOOSER_STEP`/`CHOOSER_DIGIT` messages — no raise, no focus movement, no anchor recomputation until the single `CHOOSER_COMMIT`. That is why it cannot pin by construction. The candidate list is a **snapshot at open**: current session first, then MRU, then never-visited in registration order, `is_live()`-filtered (the ring's W1 + sp3.2 eviction semantics; muted stays browsable — Fork 2). Previews are delivered exactly like ⌃⌘W cues (speaker stream or playable-workspace fallback, `mute_exempt+pause_exempt+at_front`), each swapping out and barging in on the previous. Commit = `focus(target)` + `voice_state="flowing"` + landing cue + raise — copied verbatim from `on_cycle_session` (`focus.py:137-159`), including the muted-landing `set_speaker(None)` keep-go release. hotkeyd (Swift) grows a chooser-mode FSM: digits are `RegisterEventHotKey`'d ONLY while the chord is held; a ~40 ms poll of `NSEvent.modifierFlags` (permission-free, no event tap) detects release → commit; 30 s cap → cancel.

**Tech Stack:** Python 3, `pytest`, the existing daemon (`src/sonari/daemon/*`, `src/sonari/sessions.py`, `src/sonari/queue.py`), Swift (`hotkeyd/sonari-hotkeyd.swift`, compiled by the repo's `swiftc` path). macOS-only. No new dependencies.

## Global Constraints

- **Baseline:** `941 passed, 1 skipped` (`.venv/bin/python -m pytest -q`, verified at `3430cbf`, ~7 s). Must end green (baseline − migrated/deleted + new tests).
- **R12: `_foreground` is written ONLY by `set_foreground` / `focus` / `unregister`.** The chooser commit uses `focus()`; previews and MRU updates never touch `_foreground`.
- **M1: the speak-loop lock shape is UNTOUCHED.** `host.py` `_speak_loop_once` (`:436-536`) — including the keep-going block (`:480-496`) — is not modified by ANY task. Chooser handlers run in the handler transaction (the same single lock), so previews/commits are already atomic with the loop's pop+claim.
- **`speaker()` vs `workspace()` discipline:** the voice owner is `speaker()`; the front terminal + keyboard is `workspace()` (`sessions.py:83-92`). Previews route to the speaker's stream; the commit moves the workspace via `focus()`. Never conflate them.
- **Fork-2: muted sessions stay reachable.** The chooser filter is `is_live()` ONLY — never `st.stopped`. Committing onto a muted session lands the workspace, keeps the target muted, releases the voice (`set_speaker(None)`) to keep-going.
- **The suite must end GREEN with `tests/test_concurrency_guards.py` green at EVERY commit.** T4 EXTENDS the hammer set with the chooser messages. NEVER weaken an existing assertion.
- **Every tty-dependent test stubs `ttyutil.tty_alive`** (the `_liveness`/`_ident` idiom — `tests/test_sp3fix_ring.py:12-19`). Host-pty coupling has broken tests before. Note `tty_alive("") is True` (fail-open, `src/sonari/ttyutil.py:38-49`), so tests that never set an `Identity` are safe without a stub; ANY test that sets a tty MUST stub.
- **Commit style:** `feat(chooser)` / `fix(chooser)` / `test(chooser)` / `docs(chooser)`. Repo git identity is already set (noreply) — do not touch git config.
- **TDD:** red → green → commit, bite-sized. DRY, YAGNI.
- **Scope fence:** this plan only. NO SP4 frontier/marker work. The intermittent raise failures (10/63) are a separate follow-up — they now affect only whether a committed window comes forward, never browsing. Do not touch `~/.sonari/` (the live install): the Swift change is verified by a compile to `$TMPDIR`; deployment happens via `sonari install`'s `build_swift_binary` srchash path after merge.

### Open decisions STATED (not buried) — spec frictions and their resolutions

- **D1 — unknown digit vs hotkeyd mode-exit (spec §3 vs §5 internal conflict).** §3: "unknown number → error earcon, chooser stays open". §5: "digit → CHOOSER_DIGIT, exit mode" (hotkeyd can't know validity). Both are implemented literally: the daemon errors + keeps the open state; hotkeyd exits its mode. Consequence: after an unknown digit, releasing the chord sends nothing; the daemon's open state (and its captured interrupted item) persists until the next CHOOSER_* message — a fresh ⌃⌘Tab within 30 s **continues the same browse** (a resume affordance), and after 30 s the stale check implicitly cancels + restores. Documented in the live checklist.
- **D2 — numerals, not number-words.** Spoken strings use the numeral ("Voice: work 1, Playing.", "2, bravo.") — `say` renders "1" and "one" identically, and numerals avoid a hand-rolled word table. The spec's "bravo two" is what is HEARD, not a literal-text mandate.
- **D3 — previews are plain speech (no spearcon) in v1.** Spec §3: spearcons "MAY replace the folder word". A preview's `audio_path` would replace the WHOLE utterance (dropping the number — see the speak path `host.py:515-519`), and splitting number-speech + folder-spearcon into two queue items doubles the swap/barge-in bookkeeping. The COMMIT landing cue keeps its spearcon (verbatim cycle parity). Spearcon previews = a possible follow-up, not this plan.
- **D4 — the W roster is unfiltered** (all registered sessions in number order). §7 states no liveness filter, and the summary's waiting/muted counts (`control.py:199-206`) never filtered either — consistent.
- **D5 — the registration announce fires on `SESSION_START` for genuinely NEW sessions only** (resume/clear/compact re-fires of a known id stay silent). Newness is captured BEFORE the Policy-A gate (both gate branches `_record()` the session).
- **D6 — degenerate rosters.** Old cycle errored on `<2` live sessions. The chooser: an EMPTY live-candidate list → error earcon, nothing opens; a SINGLE candidate opens normally and previews "…, current" (spoken feedback — not a silent no-op, so the error tone is unnecessary). The migrated invariant from `test_cycle_one_live_one_phantom_plays_error_tone` is "the phantom can never land", preserved as a candidates-exclusion assertion.
- **D7 — the announce's blast radius is exactly 3 tests** (`tests/test_daemon_setup_health.py`), all updated with the new intended behavior in T5. Verified by grep: no other SESSION_START-sending test asserts stream contents or spoken output that the announce touches.

## Test-harness facts (verified against the repo at `3430cbf` — use these exact shapes)

- `from tests.daemon_helpers import make_daemon, stream_queue` — **`make_daemon(verbosity="everything", foreground="fg")` returns a 5-tuple `(daemon, queue, speaker, sessions, config)`** (`tests/daemon_helpers.py:75-87`). `make_daemon` `set_foreground`s the `foreground` arg (registering it → it gets session number 1 and MRU position 0 after T1) and `queue` is that session's own stream queue; pass `foreground=None` for a no-speaker daemon (then `queue` is a detached empty `SpeechQueue`).
- `FakeSpeaker` (`daemon_helpers.py:36-72`): `speaker.spoken` (list, entries may be None), `speaker.audio_paths`, `speaker.earcons`, `speaker.cancels` (int), `speaker.pitches`; `speak()` returns `self.complete` (default True) immediately — chooser observability tests are **synchronous**: `handle_message(...)` then `daemon._speak_loop_once()` then assert post-state.
- Module-local message helper (define once per new test module, `tests/test_sp3fix_ring.py:7-9`):
  ```python
  def _msg(t, session, **kw):
      from sonari.protocol import PROTOCOL_VERSION
      return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}
  ```
- **Liveness stubbing** (`tests/test_sp3fix_ring.py:12-19`): `_liveness(monkeypatch, dead)` patches `ttyutil.tty_alive`; `_ident(sessions, sid, tty)` sets an `Identity`. `ttyutil.tty_alive("")` is True (fail-open, `ttyutil.py:38-49`).
- `daemon._enqueue(session, kind, text, is_decision, entry=None, mute_exempt=False, pause_exempt=False, at_front=False, names_session=False, audio_path=None)` (`host.py:218-221`). After a call, the freshly-allocated item id equals `daemon._next_id` (`_alloc_id` `host.py:182-184`; `_next_id` property `host.py:137-143`) — the chooser uses this to track its queued preview.
- `daemon._current_item` is a settable property (`host.py:122-127`); `daemon._pending_heard` is the marker dict (property, `host.py:113-115`); `daemon._stream(s).queue._items` is the deque; `daemon._stream(s).stopped` is the per-session mute flag.
- The ⌃⌘W capture-and-requeue pattern to mirror: `control.py:184-225` (`cur = host._current_item`; `entry = host._pending_heard.get(cur.id)`; `speaker.cancel()`; re-`_enqueue` with `entry=` + all flags + `at_front=True`).
- The cycle-landing semantics to copy verbatim into the commit: `focus.py:137-159` (`focus(target)` → `speaker.cancel()` → `voice_state = "flowing"` → muted-landing `set_speaker(None)` → folder/identity/`will_attempt`/`bump_generation` → cue `_enqueue(..., audio_path=_spearcon_path(folder), mute_exempt=True, at_front=True, names_session=True)` → conditional `raise_async(..., on_failure=...host._raise_failed(s, f))`).
- Raise fake: `from tests.test_daemon_focus_follow import RecordingRaiseService` (`test_daemon_focus_follow.py:16-33`): `rs = RecordingRaiseService(will=True)`; `daemon.raise_service = rs`; `rs.attempts` is `[(identity, generation)]`; `will_attempt(identity)` is False for `identity is None`.
- `SessionStart` tests must `monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))` (`tests/test_daemon_focus_follow.py:42`) or `_maybe_guide_setup` (`lifecycle.py:38-53`) may enqueue a guidance cue.
- Handlers: `@handler(MsgType.X)` populates the registry at import (`registry.py:6-10`); feature modules are side-effect-imported in `host.py:22-29`; `daemon/__init__.py:11-43` `assert_complete` makes a missing handler an import-time error — **adding a MsgType requires adding it to that list in the same commit**.
- Concurrency guards (`tests/test_concurrency_guards.py`): real daemon via `_make_real_daemon(runner, foreground="s0")` (`:76-84`); the hammer ops list is at `:242-243`; `MsgType.CYCLE_SESSION` appears there (`:243`) — **the constant's deletion and the ops swap MUST be one commit** or the guard file fails at import. The module-level `_select_keep_going` counting patch (`:158-165`) is restored in a `finally` (`:315-316`) — do not disturb.
- Keymap: `resolve_keymap` (`keymap.py:97-129`) raises on unknown actions but `load_keymap` (`keymap.py:148-150`) silently DROPS user-keymap rows for removed actions — a stale user `keymap.json` still binding `cycle_session_*` degrades gracefully after the swap.
- hotkeyd build: `MacHotkeyBackend.build()` (`platform/macos/hotkeys.py:153-160`) → `build_swift_binary` (`platform/macos/_helpers.py:15-42`) — sha256 srchash skip; changing the `.swift` source makes the next `sonari install` recompile. Default bindings for Tab live in `extra_default_bindings()` (`hotkeys.py:111-121`). The Swift file's message-send path is `sendMessage` (`sonari-hotkeyd.swift:74-91`), JSON helper `jsonLine` (`:127-130`), the 0.5 s focus poll Timer pattern (`:229-235`).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/sonari/sessions.py` | Modify | T1: `_numbers` + `_mru` state, `_assign_number` (in `_record`), `_touch_mru` (in `set_foreground`/`focus`/matched `set_os_focus`), `number()`, `session_for_number()`, `mru()`, unregister frees both. |
| `src/sonari/queue.py` | Modify | T1: `remove_by_id()` (preview swap primitive). |
| `src/sonari/protocol.py` | Modify | T2: add `CHOOSER_STEP/DIGIT/COMMIT/CANCEL`. T4: delete `CYCLE_SESSION` (line 37). |
| `src/sonari/daemon/features/chooser.py` | **New** | T2: the whole chooser — `ChooserState`, snapshot, previews, capture/restore, commit, stale handling, 4 handlers, injectable `_now`. |
| `src/sonari/daemon/host.py` | Modify | T2: import the chooser feature (`:29` block) + `self._chooser = None` in `__init__`. T5: `self._last_where_ts = None`. **The speak loop is untouched.** |
| `src/sonari/daemon/__init__.py` | Modify | T2: +4 chooser types (35). T4: −`CYCLE_SESSION` (34). |
| `src/sonari/keymap.py` | Modify | T3: `cycle_session_*` → `chooser_step_*` in `ACTION_MESSAGES` (lines 40-41) + comment sweep. |
| `src/sonari/platform/macos/hotkeys.py` | Modify | T3: `extra_default_bindings` Tab rows → `chooser_step_*` (lines 119-120). |
| `hotkeyd/sonari-hotkeyd.swift` | Modify | T3: `HotkeyEntry.action`, chooser-mode FSM (dynamic ⌃⌘1-9, 40 ms release poll, 30 s cap), handler routing. |
| `src/sonari/daemon/features/focus.py` | Modify | T4: DELETE `on_cycle_session` (lines 114-160, decorator through end of file). `_waiting_target`/`on_os_focus`/`on_jump_waiting` stay. |
| `src/sonari/daemon/features/playback.py` | Modify | T4: comment sweep only (`:33-37`, `:124`). |
| `src/sonari/daemon/features/lifecycle.py` | Modify | T5: `is_new` capture + registration announce in the SESSION_START block. |
| `src/sonari/daemon/features/control.py` | Modify | T5: `_now`/`W_DOUBLE_S`, `_numbered`, `_roster_text`, `on_where_am_i` numbers + double-press escalation. |
| `tests/test_session_numbers_mru.py` | **New** | T1 unit tests. |
| `tests/test_queue.py` | Modify | T1: `remove_by_id` tests (append). |
| `tests/test_chooser.py` | **New** | T2: the chooser behavioral suite (incl. migrated Fork-2/anchor coverage). |
| `tests/test_protocol.py` | Modify | T2: +4 chooser rows in both exact dicts. T4: −CYCLE_SESSION rows. |
| `tests/test_daemon_registry.py` | Modify | T2: `ALL_31`→`ALL_TYPES` +4 (35). T4: −1 (34). |
| `tests/test_keymap.py` | Modify | T3: the 6 enumerated cycle-binding sites → chooser + a new resolved-keymap assertion. |
| `tests/test_daemon_cycle.py` | **Delete** | T4 (5 tests; coverage map below). |
| `tests/test_sp2_t6_control_grammar.py` | Modify | T4: delete the 2 `⌃⌘Tab` tests (lines 69-89); ⌃⌘S/⌃⌘W tests stay untouched. |
| `tests/test_sp3_cycle.py` | Modify | T4: delete 3 (migrated to `test_chooser.py`), rewrite 1 setup, keep 1. |
| `tests/test_sp3fix_ring.py` | Modify | T4: rewrite tests 1-6 through the chooser; test 7 (jump) stays. |
| `tests/test_identity_eviction.py` | Modify | T4: rewrite test 7 (`:149-157`) through the chooser. |
| `tests/test_daemon_spearcon.py` | Modify | T4: rewrite the 2 cycle spearcon tests as chooser-commit tests. T5: 2 W strings. |
| `tests/test_pitch_dispatch.py` | Modify | T4: rewrite the 3 cycle no-chirp tests as chooser no-chirp tests. |
| `tests/test_concurrency_guards.py` | Modify | T4: hammer ops `CYCLE_SESSION` → the 4 chooser messages + comment updates. Assertions untouched. |
| `tests/test_where_roster.py` | **New** | T5: double-press window, roster string, announce tests. |
| `tests/test_daemon_where_am_i.py`, `tests/test_sp3_voicestate.py`, `tests/test_sp3_hold_entry.py`, `tests/test_sp3fix_grammar.py`, `tests/test_daemon_setup_health.py` | Modify | T5: the enumerated exact-string / count updates. |

**Task order:** T1 → T2 → T3 → T4 → T5 → T6. Suite green after every task: T2 adds the chooser alongside the still-live cycle; T3 swaps only keymap/hotkeyd (cycle handler still importable, its tests still green); T4 deletes cycle + migrates its tests + swaps the hammer op **in one commit** (the guard file imports `MsgType.CYCLE_SESSION`); T5's string changes carry their own test updates.

---

## Task T1 — SessionManager numbers + MRU, SpeechQueue.remove_by_id (primitives)

**Files:** Modify `src/sonari/sessions.py`, `src/sonari/queue.py`. Tests: `tests/test_session_numbers_mru.py` (new), `tests/test_queue.py` (append).

**Interfaces produced** (consumed by T2/T5): `SessionManager.number(session) -> "int | None"`; `SessionManager.session_for_number(n) -> "str | None"`; `SessionManager.mru() -> "list[str]"` (most-recent first, a copy); numbers assigned lowest-free ≥ 1 inside `_record` (every registration path), stable until `unregister` frees them; MRU touched ONLY by `set_foreground`/`focus`/matched `set_os_focus` — NEVER `set_speaker`; `SpeechQueue.remove_by_id(item_id) -> "SpeechItem | None"`. *Depends on: nothing.*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_session_numbers_mru.py (new)
"""Spec §6 session numbers (stable lowest-free) + §8 recency (MRU, deliberate
acts only). Pure SessionManager unit tests — no daemon, no ttys, no stubs needed
(is_live is never called here)."""
from sonari.sessions import Identity, SessionManager


# --- numbering: lowest-free, stable, holes refill, >9 speakable ---
def test_numbers_assigned_lowest_free_at_registration():
    m = SessionManager()
    m.register("a"); m.register("b"); m.register("c")
    assert (m.number("a"), m.number("b"), m.number("c")) == (1, 2, 3)


def test_number_stable_across_re_registration_and_foreground():
    m = SessionManager()
    m.register("a"); m.register("b")
    m.set_foreground("a", cwd="/x/a")      # re-records a
    m.register("b", cwd="/x/b")            # re-records b
    assert m.number("a") == 1 and m.number("b") == 2


def test_unregister_frees_the_number_and_the_hole_refills():
    m = SessionManager()
    m.register("a"); m.register("b"); m.register("c")
    m.unregister("b")
    assert m.number("b") is None
    m.register("d")
    assert m.number("d") == 2              # lowest FREE, not max+1


def test_numbers_above_nine_are_assigned():
    m = SessionManager()
    for i in range(11):
        m.register("s{0}".format(i))
    assert m.number("s10") == 11           # spoken but digit-unreachable (spec §6)


def test_session_for_number_round_trip_and_unknown():
    m = SessionManager()
    m.register("a"); m.register("b")
    assert m.session_for_number(2) == "b"
    assert m.session_for_number(7) is None


def test_set_foreground_and_focus_assign_numbers_too():
    m = SessionManager()
    m.set_foreground("fg")                 # every _record path numbers
    m.focus("j")
    assert m.number("fg") == 1 and m.number("j") == 2


# --- MRU: deliberate acts only ---
def test_mru_updated_by_set_foreground_and_focus_most_recent_first():
    m = SessionManager()
    m.set_foreground("a")
    m.focus("b")
    m.set_foreground("c")
    assert m.mru() == ["c", "b", "a"]
    m.focus("a")                           # re-touch moves to front, no duplicate
    assert m.mru() == ["a", "c", "b"]


def test_mru_never_updated_by_set_speaker():
    m = SessionManager()
    m.set_foreground("a")
    m.register("b")
    m.set_speaker("b")                     # keep-going voice drift is NOT presence
    assert m.mru() == ["a"]


def test_mru_updated_by_matched_os_focus_only():
    m = SessionManager()
    m.set_foreground("a")
    m.register("b")
    m.set_identity("b", Identity(term_program="Apple_Terminal", tty="/dev/ttysB"))
    m.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysB")   # a click: matched
    assert m.mru()[0] == "b"
    m.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysZZ")  # unmatched
    assert m.mru()[0] == "b"               # no phantom touch


def test_unregister_removes_from_mru():
    m = SessionManager()
    m.set_foreground("a")
    m.focus("b")
    m.unregister("b")
    assert m.mru() == ["a"]


def test_mru_returns_a_copy():
    m = SessionManager()
    m.set_foreground("a")
    m.mru().append("evil")
    assert m.mru() == ["a"]
```

```python
# tests/test_queue.py (append)
def test_remove_by_id_removes_and_returns_the_item():
    q = SpeechQueue()
    q.enqueue(_item(7))
    q.enqueue(_item(9))
    got = q.remove_by_id(7)
    assert got is not None and got.id == 7
    assert [it.id for it in q._items] == [9]


def test_remove_by_id_unknown_returns_none_and_leaves_queue():
    q = SpeechQueue()
    q.enqueue(_item(7))
    assert q.remove_by_id(42) is None
    assert len(q) == 1
```

(`tests/test_queue.py` already defines `_item(i)` and imports `SpeechQueue` — `tests/test_queue.py:1-8`, mirrored from the SP2 plan; verify the helper name at the top of the file and reuse it.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_session_numbers_mru.py tests/test_queue.py -q`
Expected: FAIL — `AttributeError: 'SessionManager' object has no attribute 'number'` (etc.) and `'SpeechQueue' object has no attribute 'remove_by_id'`.

- [ ] **Step 3: Implement**

`src/sonari/sessions.py` — in `__init__`, after `self._tty_evicted: "set[str]" = set()` (`:54`):
```python
        # Stable spoken session numbers (chooser spec §6): lowest free >= 1 at
        # registration, stable for the session's lifetime, freed on unregister.
        # Spoken in chooser previews, the W roster, the ⌃⌘W clauses, and the
        # registration announce. NEVER injected into content attribution
        # prefixes or jump cues (noise).
        self._numbers: "dict[str, int]" = {}
        # Recency (spec §8), most-recent first. Updated by DELIBERATE acts only:
        # set_foreground()/focus() (submit, jump, chooser commit) and a MATCHED
        # set_os_focus (a click counts as "you were there"). set_speaker() NEVER
        # touches it — keep-going voice drift is not presence (R12 discipline).
        # In-memory, like the roster.
        self._mru: "list[str]" = []
```

In `_record` (`:56-61`), append as the LAST line of the method:
```python
        self._assign_number(session)
```

New methods, after `_record`:
```python
    def _assign_number(self, session: str) -> None:
        """Assign the lowest free number >= 1 once; stable until unregister."""
        if session in self._numbers:
            return
        used = set(self._numbers.values())
        n = 1
        while n in used:
            n += 1
        self._numbers[session] = n

    def _touch_mru(self, session: str) -> None:
        if session in self._mru:
            self._mru.remove(session)
        self._mru.insert(0, session)

    def number(self, session: str) -> "int | None":
        """The stable spoken number for *session*, or None if unregistered."""
        return self._numbers.get(session)

    def session_for_number(self, n) -> "str | None":
        """The registered session holding number *n*, or None (digit teleport)."""
        for s, num in self._numbers.items():
            if num == n:
                return s
        return None

    def mru(self) -> "list[str]":
        """Deliberately-visited sessions, most-recent first (a copy)."""
        return list(self._mru)
```

In `set_foreground` (`:63-66`), append after `self._speaker = session`:
```python
        self._touch_mru(session)
```

In `focus` (`:182-187`), append after `self._speaker = session`:
```python
        self._touch_mru(session)
```

In `unregister` (`:106-115`), append after `self._tty_evicted.discard(session)`:
```python
        self._numbers.pop(session, None)
        if session in self._mru:
            self._mru.remove(session)
```

In `set_os_focus` (`:189-218`), append after the final `self._os_focused_session = match`:
```python
        if match is not None:
            # A resolved click IS presence (spec §8): the user was demonstrably there.
            self._touch_mru(match)
```

`src/sonari/queue.py` — after `oldest_id` (`:91-96`):
```python
    def remove_by_id(self, item_id: int) -> "SpeechItem | None":
        """Remove and return the queued item with id *item_id*, else None. The
        chooser swaps out its still-queued previous preview before enqueuing the
        next one (each preview replaces + barge-ins the last)."""
        for i, item in enumerate(self._items):
            if item.id == item_id:
                del self._items[i]
                return item
        return None
```

- [ ] **Step 4: Run to verify green + guards**

Run: `.venv/bin/python -m pytest tests/test_session_numbers_mru.py tests/test_queue.py tests/test_sessions.py tests/test_concurrency_guards.py -q`
Expected: PASS. Then the full suite once (`.venv/bin/python -m pytest -q`): the `_touch_mru` additions are read by nothing yet — 941 passed, 1 skipped.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/sessions.py src/sonari/queue.py tests/test_session_numbers_mru.py tests/test_queue.py
git commit -m "feat(chooser): session numbers + MRU recency in SessionManager; SpeechQueue.remove_by_id"
```

---

## Task T2 — Protocol CHOOSER_* + the chooser feature module

**Files:** Modify `src/sonari/protocol.py`, `src/sonari/daemon/__init__.py`, `src/sonari/daemon/host.py` (import + one `__init__` field — NOT the speak loop), `tests/test_protocol.py`, `tests/test_daemon_registry.py`. New: `src/sonari/daemon/features/chooser.py`, `tests/test_chooser.py`.

**Interfaces produced:** MsgTypes `CHOOSER_STEP` (`{"direction": "next"|"prev"}`, defaulting next), `CHOOSER_DIGIT` (`{"digit": 1-9}`), `CHOOSER_COMMIT`, `CHOOSER_CANCEL`; `host._chooser` (a `ChooserState` or None, lock-guarded); `chooser._now` (monkeypatchable clock), `chooser.STALE_S = 30.0`. **Interfaces consumed:** T1's `number/session_for_number/mru`; `host._next_id` after `_enqueue` = the new item's id; the cycle-landing recipe `focus.py:137-159`; the W capture pattern `control.py:184-225`. *Depends on: T1.*

`CYCLE_SESSION` is NOT touched here — both paths coexist until T4, keeping every commit green.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chooser.py (new)
"""The session chooser (spec 2026-07-14 §3): browse previews that move NOTHING,
commit once. Includes the coverage MIGRATED from the deleted CYCLE_SESSION ring:
W1 dead-tty filtering, sp3.2 eviction filtering, muted-stays-browsable (Fork 2),
and the muted-commit keep-go landing."""
import sonari.ttyutil as ttyutil
from sonari.protocol import MsgType
from sonari.queue import SpeechItem
from sonari.sessions import Identity
from sonari.daemon.features import chooser
from tests.daemon_helpers import make_daemon
from tests.test_daemon_focus_follow import RecordingRaiseService


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _liveness(monkeypatch, dead):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff not in `dead`."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _ident(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


def _step(daemon, direction="next"):
    daemon.handle_message(_msg(MsgType.CHOOSER_STEP, "", direction=direction))


# --- open-on-first-step: the FIRST step lands on index 1 (tap-release = ⌘Tab toggle) ---
def test_first_step_opens_and_previews_index_one():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    _step(daemon)
    assert daemon._chooser is not None and daemon._chooser.index == 1
    daemon._speak_loop_once()
    assert speaker.spoken == ["2, bravo."]        # number + folder, nothing else moved
    assert sessions.foreground() == "A"           # previews move NOTHING
    assert sessions.speaker() == "A"


def test_snapshot_order_is_current_then_mru_then_registration_order():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.register("C", cwd="/x/C")
    sessions.register("D", cwd="/x/D")
    sessions.focus("C")                            # deliberate visit -> MRU
    sessions.focus("A")                            # back to A (current)
    _step(daemon)
    assert daemon._chooser.candidates == ["A", "C", "B", "D"]
    assert daemon._chooser.origin == "A"


def test_snapshot_anchor_is_workspace_not_the_diverged_speaker():
    # MIGRATED from test_sp3_cycle.test_cycle_anchor_is_workspace_not_speaker:
    # keep-going drift never re-anchors browsing.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.register("C", cwd="/x/C")
    sessions.set_speaker("C")                      # voice drifted to C; workspace=A
    _step(daemon)
    assert daemon._chooser.origin == "A"           # anchored on the workspace
    assert daemon._chooser.candidates[0] == "A"


def test_step_wraps_past_the_end_back_to_current():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    _step(daemon); _step(daemon)                   # A(0) -> B(1) -> wrap -> A(0)
    assert daemon._chooser.index == 0
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "1, alpha, current."


def test_step_prev_walks_backward():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.register("C", cwd="/x/C")
    _step(daemon, "prev")                          # -1 from 0 wraps to the last
    assert daemon._chooser.index == 2


def test_each_step_swaps_the_previous_queued_preview():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    sessions.register("C", cwd="/x/charlie")
    _step(daemon); _step(daemon)                   # B then C, no loop turn between
    texts = [it.text for it in daemon._stream("A").queue._items]
    assert texts == ["3, charlie."]                # B's preview swapped out, not stacked
    assert speaker.cancels >= 2                    # each preview barge-ins the last


def test_preview_flags_are_the_w_cue_flags():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    _step(daemon)
    item = daemon._stream("A").queue._items[0]
    assert item.mute_exempt and item.pause_exempt  # speakable under mute/hold
    assert item.audio_path is None                 # v1 previews are plain speech (D3)


def test_muted_session_stays_browsable_with_muted_suffix():
    # MIGRATED Fork-2 coverage: filter is is_live ONLY, never st.stopped.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    daemon._stream("B").stopped = True
    _step(daemon)
    daemon._speak_loop_once()
    assert speaker.spoken == ["2, bravo, muted."]


def test_dead_tty_phantom_filtered_from_candidates(monkeypatch):
    # MIGRATED W1 coverage (test_sp3fix_ring pattern).
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")   # phantom
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    _step(daemon)
    assert daemon._chooser.candidates == ["A", "C"]   # phantom B can never land


def test_evicted_session_filtered_from_candidates(monkeypatch):
    # MIGRATED sp3.2 eviction coverage (test_identity_eviction pattern).
    _liveness(monkeypatch, dead=set())             # the node exists (recycled)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("stale", cwd="/x/stale"); _ident(sessions, "stale", "/dev/ttysT")
    sessions.register("fresh", cwd="/x/fresh"); _ident(sessions, "fresh", "/dev/ttysT")
    _step(daemon)
    assert daemon._chooser.candidates == ["A", "fresh"]


def test_empty_live_roster_errors_and_does_not_open():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    _step(daemon)
    assert speaker.earcons == ["error"]
    assert daemon._chooser is None


def test_single_live_candidate_previews_current():
    # MIGRATED from test_cycle_with_fewer_than_two_sessions / one-live-one-phantom:
    # not an error tone anymore — a degenerate browse with honest spoken feedback (D6).
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    _step(daemon)
    daemon._speak_loop_once()
    assert speaker.spoken == ["1, alpha, current."]
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.foreground() == "A"            # no-op landing


# --- commit: the old cycle-landing semantics verbatim ---
def test_commit_lands_focus_flowing_cue_and_raise(monkeypatch):
    monkeypatch.setattr(ttyutil, "tty_alive", lambda tty: True)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    sessions.register("B", cwd="/x/bravo")
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttysB"))
    daemon.voice_state = "quiet-hold"
    _step(daemon)
    assert rs.attempts == []                       # previews NEVER raise
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.foreground() == "B"            # focus(target): workspace + voice
    assert daemon.voice_state == "flowing"         # deliberate re-engage
    assert len(rs.attempts) == 1                   # landing raises (cycle parity)
    ident, gen = rs.attempts[0]
    assert ident.tty == "/dev/ttysB" and gen >= 1
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "bravo."          # the landing cue, names_session
    assert daemon._chooser is None


def test_commit_cue_is_at_front_names_session_mute_exempt():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    daemon._enqueue("B", "prose", "b backlog", False)
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    head = daemon._stream("B").queue._items[0]
    assert head.text == "bravo." and head.names_session and head.mute_exempt


def test_commit_onto_muted_keeps_going_to_active():
    # MIGRATED from test_sp3_cycle.test_cycle_onto_muted_keeps_going_to_active.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/A")
    sessions.register("C", cwd="/x/C")
    daemon._stream("A").stopped = True             # A muted
    daemon._enqueue("C", "prose", "c active", False)
    _step(daemon)                                  # B(0) -> A(1), muted
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.workspace() == "A"             # workspace landed on the mute
    assert sessions.speaker() is None              # voice released (Fork 2 keep-go)
    assert daemon.voice_state == "flowing"
    assert daemon._stream("A").stopped is True     # stays muted (R7)
    daemon._speak_loop_once()                      # keep-going voices an ACTIVE session
    assert sessions.speaker() == "C"
    assert any(s and "c active" in s for s in speaker.spoken)


def test_commit_onto_muted_no_active_reports_via_where_am_i():
    # MIGRATED from test_sp3_cycle.test_cycle_onto_muted_no_active_reports_via_where_am_i.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/A")
    daemon._stream("A").stopped = True
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))   # -> A, muted
    assert sessions.workspace() == "A" and sessions.speaker() is None
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    assert speaker.earcons[-1] == "error"          # muted workspace: honest error tone


def test_commit_updates_mru():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.mru()[0] == "B"                # focus() touched recency


# --- the no-op landing + capture/resume ---
def test_commit_to_current_is_silent_noop_and_resumes_captured():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    daemon._current_item = SpeechItem(id=901, session="A", kind="prose",
                                      text="mid sentence", is_decision=False)
    _step(daemon); _step(daemon)                   # around and back to index 0
    assert daemon._chooser.index == 0
    cancels_before = speaker.cancels
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert speaker.cancels == cancels_before       # no cut at commit
    assert daemon._chooser is None
    head = daemon._stream("A").queue._items[0]
    assert head.text == "mid sentence"             # interrupted speech resumes
    assert not any(it.names_session for it in daemon._stream("A").queue._items)  # no cue


def test_cancel_restores_captured_item_and_moves_nothing():
    class _Entry:
        heard = False
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    entry = _Entry()
    cur = SpeechItem(id=902, session="A", kind="prose",
                     text="cut me", is_decision=False)
    daemon._current_item = cur
    daemon._pending_heard[902] = entry
    _step(daemon)                                  # open captures + cuts
    assert speaker.cancels >= 1
    st = daemon._chooser
    assert st.captured is cur and st.captured_entry is entry
    daemon.handle_message(_msg(MsgType.CHOOSER_CANCEL, ""))
    assert daemon._chooser is None
    head = daemon._stream("A").queue._items[0]
    assert head.text == "cut me"                   # restored at the front
    assert daemon._pending_heard[head.id] is entry # heard-marker carried over
    assert sessions.foreground() == "A"            # nothing moved
    assert not any("2," in (it.text or "") for it in daemon._stream("A").queue._items)


def test_commit_to_other_drops_the_captured_item():
    # Cycle-cut parity: landing elsewhere cuts; the interrupted item does NOT resume.
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._current_item = SpeechItem(id=903, session="A", kind="prose",
                                      text="cut for good", is_decision=False)
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.foreground() == "B"
    assert not any(it.text == "cut for good"
                   for it in daemon._stream("A").queue._items)


# --- digits ---
def test_digit_instant_commits_to_that_number():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    sessions.register("C", cwd="/x/charlie")
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=3))
    assert sessions.foreground() == "C"            # absolute teleport
    assert daemon._chooser is None


def test_digit_without_prior_step_teleports_via_fresh_open():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=2))
    assert sessions.foreground() == "B"
    assert daemon._chooser is None


def test_unknown_digit_errors_and_stays_open():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=7))
    assert speaker.earcons[-1] == "error"
    assert daemon._chooser is not None             # browse continues (spec §3)
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.foreground() == "B"            # the held candidate still commits


def test_digit_to_dead_session_errors(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=2))
    assert speaker.earcons[-1] == "error"          # W1 also guards the teleport
    assert sessions.foreground() == "A"


def test_digit_of_current_session_is_the_noop_landing():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    _step(daemon)
    daemon.handle_message(_msg(MsgType.CHOOSER_DIGIT, "", digit=1))
    assert daemon._chooser is None
    assert sessions.foreground() == "A"
    assert not any(it.names_session for it in daemon._stream("A").queue._items)


# --- stale + orphan messages ---
def test_stale_open_is_implicitly_cancelled_then_fresh(monkeypatch):
    t = {"v": 0.0}
    monkeypatch.setattr(chooser, "_now", lambda: t["v"])
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._current_item = SpeechItem(id=904, session="A", kind="prose",
                                      text="stale capture", is_decision=False)
    _step(daemon)                                  # open at t=0
    t["v"] = 31.0                                  # > STALE_S
    _step(daemon)                                  # implicit cancel + fresh open
    assert daemon._chooser.opened_at == 31.0
    assert daemon._chooser.captured is None        # fresh open had nothing in flight
    assert any(it.text == "stale capture"
               for it in daemon._stream("A").queue._items)   # old capture restored


def test_commit_without_open_is_a_noop():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    assert sessions.foreground() == "A" and speaker.cancels == 0


def test_cancel_without_open_is_a_noop():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message(_msg(MsgType.CHOOSER_CANCEL, ""))
    assert sessions.foreground() == "A" and speaker.cancels == 0


# --- preview routing when the speaker is None ---
def test_preview_falls_back_to_playable_workspace_when_speaker_none():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    sessions.set_speaker(None)                     # voice released (e.g. muted landing)
    _step(daemon)
    assert any(it.text == "2, bravo." for it in daemon._stream("A").queue._items)


def test_preview_errors_when_neither_speaker_nor_playable_workspace():
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/bravo")
    daemon._stream("A").stopped = True             # workspace muted
    sessions.set_speaker(None)
    _step(daemon)
    assert speaker.earcons[-1] == "error"          # honest: nowhere voiceable
    assert daemon._chooser is not None             # commit still possible (blind)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chooser.py -q`
Expected: FAIL at import/dispatch — `MsgType` has no `CHOOSER_STEP` (AttributeError) / no handler module `chooser`.

- [ ] **Step 3: Implement**

`src/sonari/protocol.py` — after `ANSWER_PERMISSION` (`:40`):
```python
    CHOOSER_STEP = "chooser_step"       # ⌃⌘Tab held: step the chooser (msg["direction"]); the first step opens
    CHOOSER_DIGIT = "chooser_digit"     # ⌃⌘1-9 while held: instant commit to that session number (msg["digit"])
    CHOOSER_COMMIT = "chooser_commit"   # chord released: land on the current candidate
    CHOOSER_CANCEL = "chooser_cancel"   # 30 s cap / hotkeyd death: restore the capture, move nothing
```

`src/sonari/daemon/host.py`:
1. In the side-effect import block, after `from sonari.daemon.features import hotkeys  # noqa: F401` (`:29`):
```python
from sonari.daemon.features import chooser  # noqa: F401
```
2. In `SpeechDaemon.__init__`, after the `self._pending_decisions: dict = {}` block (`:96`):
```python
        # The open session-chooser gesture (features/chooser.py ChooserState), or
        # None. Mutated ONLY under self._lock (all chooser handlers run inside
        # _state.transaction()); the speak loop never reads it.
        self._chooser = None
```

`src/sonari/daemon/__init__.py` — comment `all 31 known keys` → `all 35 known keys`; append to the `assert_complete` list after `MsgType.ANSWER_PERMISSION`:
```python
    MsgType.CHOOSER_STEP,
    MsgType.CHOOSER_DIGIT,
    MsgType.CHOOSER_COMMIT,
    MsgType.CHOOSER_CANCEL,
```

`src/sonari/daemon/features/chooser.py` — the complete new module:
```python
"""The session chooser (⌃⌘Tab held — spec 2026-07-14 §3).

Browse spoken previews that move NOTHING (no voice change, no workspace change,
no raise), then commit ONCE on chord-release or a digit. Replaces the old
CYCLE_SESSION ring: browsing state lives HERE, advanced only by the gesture's
own messages, so it cannot pin on OS-focus failures by construction — there is
no anchor recomputation between taps and no raise until the single commit.

All handlers run inside the daemon's _state.transaction() (the one lock), so
every snapshot/preview/commit is atomic with the speak loop's pop+claim (M1).
"""
from __future__ import annotations

import time

from sonari.protocol import MsgType
from sonari.daemon.registry import handler

# Injectable clock: tests monkeypatch chooser._now to drive the stale window.
_now = time.monotonic

# An open older than this is STALE — hotkeyd died mid-gesture (its own 30 s cap
# normally sends CHOOSER_CANCEL first). The next CHOOSER_* message implicitly
# cancels (restores the capture) and starts fresh (spec §3 Cancel).
STALE_S = 30.0


class ChooserState:
    """One open chooser gesture (chord held). Lives on host._chooser."""

    def __init__(self, origin, candidates, opened_at, captured, captured_entry):
        self.origin = origin            # session current at open: the no-op commit target
        self.candidates = candidates    # snapshot: [origin?] + MRU + never-visited (is_live)
        self.index = 0                  # cursor (0 == origin when origin is live)
        self.opened_at = opened_at      # _now() at open (stale detection)
        self.captured = captured        # the in-flight SpeechItem cut at open, or None
        self.captured_entry = captured_entry   # its pending-heard entry, or None
        self.preview_id = None          # the queued preview item's id (swapped each step)
        self.preview_session = None     # which stream holds that preview


def _snapshot(sessions):
    """(origin, candidates) at open. Order: the current session (workspace() —
    it already falls back to foreground), then MRU most-recent first, then
    never-visited sessions in registration order. Filter: is_live() ONLY —
    identical to the old ring's W1 + sp3.2 eviction semantics; muted sessions
    stay browsable (Fork 2)."""
    origin = sessions.workspace() or sessions.foreground()
    out = []
    if origin is not None and sessions.is_live(origin):
        out.append(origin)
    for s in sessions.mru():
        if s != origin and s not in out and sessions.is_live(s):
            out.append(s)
    for s in sessions.session_ids():
        if s != origin and s not in out and sessions.is_live(s):
            out.append(s)
    return origin, out


def _open(host):
    """Open the chooser: snapshot + capture-and-cut the in-flight item (the ⌃⌘W
    pattern, control.py:184-222 — requeued on cancel / no-op commit). Returns the
    new state, or None (error-toned) when no live candidate exists."""
    origin, candidates = _snapshot(host.sessions)
    if not candidates:
        host.speaker.earcon("error")
        return None
    cur = host._current_item
    entry = host._pending_heard.get(cur.id) if cur is not None else None
    if cur is not None:
        host.speaker.cancel()      # cut NOW so a later restore is a true resume
    host._chooser = ChooserState(origin, candidates, _now(), cur, entry)
    return host._chooser


def _state_or_none(host):
    """The live open state, after stale handling: a >STALE_S leftover is
    implicitly cancelled (captured item restored) and reported as None."""
    st = host._chooser
    if st is None:
        return None
    if _now() - st.opened_at > STALE_S:
        _restore_and_clear(host)
        return None
    return st


def _remove_preview(host, st):
    """Swap out the previous preview: drop it from its queue if still waiting,
    cut it if it is the utterance in flight (it is chooser UI, never content)."""
    if st.preview_id is None:
        return
    stream = host._streams.get(st.preview_session)
    if stream is not None:
        stream.queue.remove_by_id(st.preview_id)
    cur = host._current_item
    if cur is not None and cur.id == st.preview_id:
        host.speaker.cancel()
    st.preview_id = None
    st.preview_session = None


def _restore_and_clear(host):
    """The cancel path: remove any pending preview, requeue the captured item at
    the front of its own stream (resume), move nothing, say nothing."""
    st = host._chooser
    if st is None:
        return
    _remove_preview(host, st)
    if st.captured is not None:
        c = st.captured
        host._enqueue(c.session, c.kind, c.text, c.is_decision,
                      entry=st.captured_entry, mute_exempt=c.mute_exempt,
                      pause_exempt=c.pause_exempt, names_session=c.names_session,
                      audio_path=c.audio_path, at_front=True)
    host._chooser = None


def _preview_text(host, st):
    """'{number}, {folder}[, muted][, current].' — '{number}, another session'
    when the folder is unknown. Plain speech in v1 (no spearcon — plan D3)."""
    target = st.candidates[st.index]
    sessions = host.sessions
    folder = sessions.folder(target)
    text = "{0}, {1}".format(sessions.number(target),
                             folder if folder else "another session")
    stream = host._streams.get(target)
    if stream is not None and stream.stopped:
        text += ", muted"
    if target == st.origin:
        text += ", current"
    return text + "."


def _deliver_preview(host, st):
    """Speak one preview exactly like a ⌃⌘W cue: barge-in the previous utterance,
    enqueue to the SPEAKER's stream (or the playable-workspace fallback when the
    speaker is None — mirroring on_where_am_i's None branch, control.py:158-183)
    with mute_exempt + pause_exempt + at_front. Moves NOTHING."""
    _remove_preview(host, st)
    host.speaker.cancel()
    tgt = host.sessions.speaker()
    if tgt is None:
        ws = host.sessions.workspace()
        ws_st = host._streams.get(ws) if ws is not None else None
        playable = ws is not None and not (ws_st is not None and ws_st.stopped)
        if not playable:
            host.speaker.earcon("error")   # nowhere voiceable; browse stays open
            return
        tgt = ws
    host._enqueue(tgt, "prose", _preview_text(host, st), False,
                  mute_exempt=True, pause_exempt=True, at_front=True)
    st.preview_id = host._next_id          # the id _enqueue just allocated
    st.preview_session = tgt


def _commit(host, st, target):
    """Land. target == origin: the silent no-op (no cut, no cue, capture resumes).
    Otherwise: EXACTLY the ratified cycle-landing semantics, copied from the old
    on_cycle_session (focus.py:137-159 at 3430cbf) — focus(), flowing, cut,
    muted-landing keep-go release, names_session cue (spearcon-capable), raise."""
    if target == st.origin:
        _restore_and_clear(host)
        return
    _remove_preview(host, st)
    st.captured = None                     # cycle-cut parity: no resume on a real landing
    host._chooser = None
    sessions = host.sessions
    sessions.focus(target)                 # workspace + voice -> target (R12: the one writer)
    host.speaker.cancel()
    host.voice_state = "flowing"           # a commit is a deliberate re-engage
    if host._stream(target).stopped:
        # Commit-onto-muted (Fork 2, ratified): keep the WORKSPACE on the muted
        # target, RELEASE the voice so keep-going moves it to an ACTIVE session.
        # Do NOT un-mute the target (R7 — it stays muted until its own ⌃⌘S-start).
        sessions.set_speaker(None)
    folder = sessions.folder(target)
    identity = sessions.identity(target)
    will_raise = host._raise().will_attempt(identity)
    # Bump on EVERY commit, raising or not, so a prior in-flight raise sees
    # itself superseded (same reasoning as on_jump_waiting, focus.py:84-90).
    gen = host._raise().bump_generation()
    cue = folder + "." if folder else "Another session."
    host._enqueue(target, "prose", cue, False,
                  audio_path=host._spearcon_path(folder),
                  mute_exempt=True, at_front=True, names_session=True)
    if will_raise:
        host._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: host._raise_failed(s, f))


@handler(MsgType.CHOOSER_STEP)
def on_chooser_step(ctx, msg):
    host = ctx.host
    st = _state_or_none(host)
    if st is None:
        st = _open(host)
        if st is None:
            return None                    # no live candidates: error toned
    step = -1 if msg.get("direction", "next") == "prev" else 1
    # Open-on-first-step: index starts at 0 (current), so the opening step lands
    # on index 1 — a quick tap-and-release IS the previous-session toggle.
    st.index = (st.index + step) % len(st.candidates)
    _deliver_preview(host, st)
    return None


@handler(MsgType.CHOOSER_DIGIT)
def on_chooser_digit(ctx, msg):
    host = ctx.host
    st = _state_or_none(host)
    if st is None:
        st = _open(host)                   # absolute teleport needs no prior step
        if st is None:
            return None
    try:
        digit = int(msg.get("digit"))
    except (TypeError, ValueError):
        digit = None
    target = host.sessions.session_for_number(digit) if digit is not None else None
    if target is None or not host.sessions.is_live(target):
        host.speaker.earcon("error")       # unknown/dead number: browse stays open (§3)
        return None
    _commit(host, st, target)
    return None


@handler(MsgType.CHOOSER_COMMIT)
def on_chooser_commit(ctx, msg):
    host = ctx.host
    st = _state_or_none(host)
    if st is None:
        return None                        # release with no open gesture
    _commit(host, st, st.candidates[st.index])
    return None


@handler(MsgType.CHOOSER_CANCEL)
def on_chooser_cancel(ctx, msg):
    st = _state_or_none(ctx.host)          # stale state restores here too
    if st is not None:
        _restore_and_clear(ctx.host)
    return None
```

`tests/test_protocol.py` — in BOTH exact dicts (`test_msgtype_has_every_constant_with_exact_values` `:53-85` and `test_msgtype_defines_no_extra_string_constants` `:97-129`), add after the `"ANSWER_PERMISSION"` row (keep `"CYCLE_SESSION"` for now — T4 removes it):
```python
        "CHOOSER_STEP": "chooser_step",
        "CHOOSER_DIGIT": "chooser_digit",
        "CHOOSER_COMMIT": "chooser_commit",
        "CHOOSER_CANCEL": "chooser_cancel",
```

`tests/test_daemon_registry.py` — rename `ALL_31` → `ALL_TYPES` (both the definition `:108` and its use `:127`), update the comment `:100-105`, rename `test_all_31_msgtypes_registered` → `test_all_msgtypes_registered`, and append to the list:
```python
    _MsgType.CHOOSER_STEP, _MsgType.CHOOSER_DIGIT,
    _MsgType.CHOOSER_COMMIT, _MsgType.CHOOSER_CANCEL,
```

- [ ] **Step 4: Run to verify green + guards**

Run: `.venv/bin/python -m pytest tests/test_chooser.py tests/test_protocol.py tests/test_daemon_registry.py tests/test_concurrency_guards.py -q`
Expected: PASS. Then the full suite: `.venv/bin/python -m pytest -q` — everything green (cycle still intact).

- [ ] **Step 5: Commit**

```bash
git add src/sonari/protocol.py src/sonari/daemon/__init__.py src/sonari/daemon/host.py src/sonari/daemon/features/chooser.py tests/test_chooser.py tests/test_protocol.py tests/test_daemon_registry.py
git commit -m "feat(chooser): CHOOSER_STEP/DIGIT/COMMIT/CANCEL + the daemon-side chooser (spec §3-§4)"
```

---

## Task T3 — Keymap swap + hotkeyd chooser-mode FSM (Swift)

**Files:** Modify `src/sonari/keymap.py`, `src/sonari/platform/macos/hotkeys.py`, `hotkeyd/sonari-hotkeyd.swift`, `tests/test_keymap.py`.

**Interfaces produced:** keymap actions `chooser_step_next` / `chooser_step_prev` → wire messages `{"type": "chooser_step", "direction": "next"|"prev"}`, default-bound ⌃⌘Tab / ⌃⌘⇧Tab. hotkeyd sends `{"type":"chooser_digit","digit":N}`, `{"type":"chooser_commit"}`, `{"type":"chooser_cancel"}` from its FSM (NOT keymap actions — digits exist only while the chord is held). **The `cycle_session_*` daemon handler stays alive until T4** (its tests drive it via `handle_message`, not hotkeys), so the suite stays green. A stale user `keymap.json` still binding `cycle_session_*` degrades gracefully: `load_keymap` drops unknown actions (`keymap.py:148-150`). *Depends on: T2 (the daemon must handle what hotkeyd now sends).*

- [ ] **Step 1: Write the failing tests**

In `tests/test_keymap.py` apply these exact edits:

1. `test_default_keymap_macos_uses_ctrl_cmd` (`:50-69`): in the expected key-set (`:52-59`) replace `"cycle_session_next", "cycle_session_prev",` with `"chooser_step_next", "chooser_step_prev",`; replace the two asserts at `:68-69` with:
```python
    assert d["chooser_step_next"] == {"key": "tab", "mods": ["ctrl", "cmd"]}
    assert d["chooser_step_prev"] == {"key": "tab", "mods": ["ctrl", "cmd", "shift"]}
```
2. `test_no_two_default_actions_share_a_key` comment (`:221-223`): `cycle_session_next/cycle_session_prev` → `chooser_step_next/chooser_step_prev`.
3. Replace `test_cycle_session_default_bindings_on_macos` (`:270-273`) with:
```python
def test_chooser_step_default_bindings_on_macos(mac):
    d = keymap.default_keymap()
    assert d["chooser_step_next"] == {"key": "tab", "mods": ["ctrl", "cmd"]}
    assert d["chooser_step_prev"] == {"key": "tab", "mods": ["ctrl", "cmd", "shift"]}
```
4. In `test_b_action_messages_present` (`:276-282`) replace the two cycle asserts with:
```python
    assert keymap.ACTION_MESSAGES["chooser_step_next"] == {
        "type": "chooser_step", "direction": "next"}
    assert keymap.ACTION_MESSAGES["chooser_step_prev"] == {
        "type": "chooser_step", "direction": "prev"}
```
5. In `test_full_default_keymap_resolves_without_duplicate_hotkeys` (`:285-292`) replace `"cycle_session_next", "cycle_session_prev",` with `"chooser_step_next", "chooser_step_prev",` in the actions set.
6. Append the spec §10 resolved-keymap assertion:
```python
def test_resolved_default_keymap_has_chooser_and_no_cycle(mac):
    # Spec §10: the resolved keymap hotkeyd reads contains the chooser actions
    # and none of the deleted cycle ones.
    resolved = keymap.resolve_keymap(keymap.default_keymap())
    actions = {e["action"] for e in resolved}
    assert {"chooser_step_next", "chooser_step_prev"} <= actions
    assert not any(a.startswith("cycle_session") for a in actions)
    msgs = [json.loads(e["message"]) for e in resolved]
    assert {"type": "chooser_step", "direction": "next"} in msgs
    assert not any(m.get("type") == "cycle_session" for m in msgs)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_keymap.py -q`
Expected: FAIL — `chooser_step_next` is an unknown action / missing binding.

- [ ] **Step 3: Implement the Python side**

`src/sonari/keymap.py` — replace lines 40-41:
```python
    "chooser_step_next": {"type": "chooser_step", "direction": "next"},   # ⌃⌘Tab (chord held)
    "chooser_step_prev": {"type": "chooser_step", "direction": "prev"},   # ⌃⌘⇧Tab
```
And in the `_DEFAULT_KEYS` comment block (`:49-53`), replace `cycle = Tab/⇧Tab` wording with `the chooser = Tab/⇧Tab`.

`src/sonari/platform/macos/hotkeys.py` — in `extra_default_bindings` (`:111-121`), replace the two cycle rows (`:119-120`) with:
```python
            "chooser_step_next": {"key": "tab", "mods": list(base)},
            "chooser_step_prev": {"key": "tab", "mods": base + ["shift"]},
```
and update the method's comment (`:112-114`): `cycle sessions = ⌃⌘Tab (next) / ⌃⌘⇧Tab (prev)` → `the session chooser = ⌃⌘Tab (step next) / ⌃⌘⇧Tab (step prev); digits/commit/cancel are hotkeyd-FSM-internal, not keymap actions`.

- [ ] **Step 4: Implement the Swift FSM**

`hotkeyd/sonari-hotkeyd.swift` — four exact edits:

**(a)** Replace the `HotkeyEntry` struct (`:21-25`) with:
```swift
struct HotkeyEntry {
    let action: String
    let keyCode: UInt32
    let modifiers: UInt32
    let message: String
}
```

**(b)** In `loadEntries()` (`:36-60`), replace the guard + append (`:49-57`) with:
```swift
        guard let action = obj["action"] as? String,
              let keyCode = obj["keyCode"] as? Int,
              let modifiers = obj["modifiers"] as? Int,
              let message = obj["message"] as? String else {
            continue
        }
        entries.append(HotkeyEntry(
            action: action,
            keyCode: UInt32(keyCode),
            modifiers: UInt32(modifiers),
            message: message))
```

**(c)** Insert the chooser-mode FSM section immediately BEFORE `// Index entries by their hotkey id...` (`:154`):
```swift
// --- Chooser-mode FSM (spec §5). On a chooser_step_* fire: enter mode + send the
// step. While in mode: ⌃⌘1-9 are dynamically registered (they must NEVER shadow
// other apps otherwise); a ~40 ms poll of the CURRENT modifier state
// (NSEvent.modifierFlags — permission-free: no event tap, no new TCC, verified
// 2026-07-14) detects ⌃ or ⌘ release -> CHOOSER_COMMIT; a 30 s cap ->
// CHOOSER_CANCEL. Shift is deliberately NOT monitored: ⇧ toggles step direction
// and is released between steps — its release must never commit. Exit always
// unregisters the digits and stops both timers. The 0.5 s focus poller below is
// untouched. ---
let chooserDigitKeyCodes: [Int: UInt32] = [   // kVK_ANSI_1...kVK_ANSI_9
    1: 18, 2: 19, 3: 20, 4: 21, 5: 23, 6: 22, 7: 26, 8: 28, 9: 25,
]
let kChooserDigitIDBase: UInt32 = 1000        // id space above the resolved entries
var chooserMode = false
var chooserDigitRefs: [EventHotKeyRef] = []
var chooserReleaseTimer: Timer? = nil
var chooserCapTimer: Timer? = nil
var chooserRequiredFlags: NSEvent.ModifierFlags = []

func carbonToNSFlags(_ mask: UInt32) -> NSEvent.ModifierFlags {
    var flags: NSEvent.ModifierFlags = []
    if mask & 4096 != 0 { flags.insert(.control) }   // controlKey
    if mask & 256 != 0 { flags.insert(.command) }    // cmdKey
    if mask & 2048 != 0 { flags.insert(.option) }    // optionKey
    // shiftKey (512) EXCLUDED: its release steps direction, never commits.
    return flags
}

func chooserSend(_ obj: [String: Any]) {
    if let line = jsonLine(obj) { sendMessage(line) }
}

func exitChooserMode() {
    guard chooserMode else { return }
    chooserMode = false
    for ref in chooserDigitRefs { UnregisterEventHotKey(ref) }
    chooserDigitRefs = []
    chooserReleaseTimer?.invalidate()
    chooserReleaseTimer = nil
    chooserCapTimer?.invalidate()
    chooserCapTimer = nil
}

func enterChooserMode(entryModifiers: UInt32) {
    guard !chooserMode else { return }
    chooserMode = true
    chooserRequiredFlags = carbonToNSFlags(entryModifiers)
    // Register the digits on the entry chord minus shift (⇧Tab also enters mode).
    let digitMods = entryModifiers & ~UInt32(512)
    for (digit, keyCode) in chooserDigitKeyCodes {
        var ref: EventHotKeyRef?
        let hotKeyID = EventHotKeyID(signature: kHotKeySignature,
                                     id: kChooserDigitIDBase + UInt32(digit))
        if RegisterEventHotKey(keyCode, digitMods, hotKeyID,
                               GetApplicationEventTarget(), 0, &ref) == noErr,
           let r = ref {
            chooserDigitRefs.append(r)
        }
    }
    // Release poll: commit the moment a required modifier is observed released.
    let poll = Timer(timeInterval: 0.04, repeats: true) { _ in
        let held = NSEvent.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if !chooserRequiredFlags.isSubset(of: held) {
            chooserSend(["type": "chooser_commit"])
            exitChooserMode()
        }
    }
    RunLoop.main.add(poll, forMode: .common)
    chooserReleaseTimer = poll
    // Safety cap: a wedged chord cancels rather than committing somewhere random.
    let cap = Timer(timeInterval: 30.0, repeats: false) { _ in
        chooserSend(["type": "chooser_cancel"])
        exitChooserMode()
    }
    RunLoop.main.add(cap, forMode: .common)
    chooserCapTimer = cap
}
```

**(d)** Replace the body of `hotKeyHandler` (`:157-174`) with:
```swift
let hotKeyHandler: EventHandlerUPP = { (_ nextHandler, _ theEvent, _ userData) -> OSStatus in
    var hkID = EventHotKeyID()
    let status = GetEventParameter(
        theEvent,
        EventParamName(kEventParamDirectObject),
        EventParamType(typeEventHotKeyID),
        nil,
        MemoryLayout<EventHotKeyID>.size,
        nil,
        &hkID
    )
    if status == noErr && hkID.signature == kHotKeySignature {
        if hkID.id >= kChooserDigitIDBase {
            // A chooser digit — registered only while in mode: teleport + exit (§5).
            let digit = Int(hkID.id - kChooserDigitIDBase)
            chooserSend(["type": "chooser_digit", "digit": digit])
            exitChooserMode()
        } else if let entry = entriesByID[hkID.id] {
            if entry.action == "chooser_step_next" || entry.action == "chooser_step_prev" {
                enterChooserMode(entryModifiers: entry.modifiers)
                sendMessage(entry.message)    // the first step opens daemon-side
            } else {
                sendMessage(entry.message)
            }
        }
    }
    return noErr
}
```
(Note `jsonLine` is defined at `:127-130`, ABOVE the insertion point — the FSM may call it. `entriesByID` ids are the array indices (`:198-199`), far below 1000.)

- [ ] **Step 5: Verify — Python green + Swift compiles**

Run: `.venv/bin/python -m pytest tests/test_keymap.py tests/test_hotkeys.py tests/test_cli_install.py tests/test_concurrency_guards.py -q` (if `tests/test_hotkeys.py` does not exist, drop it from the command)
Expected: PASS.
Run: `swiftc hotkeyd/sonari-hotkeyd.swift -o "$TMPDIR/sonari-hotkeyd-check" && echo BUILD-OK`
Expected: `BUILD-OK` (warnings acceptable; errors are not). Do NOT write into `~/.sonari/` — the live install rebuilds via `sonari install`'s srchash path after merge.
Then the full suite: `.venv/bin/python -m pytest -q` — green (the cycle handler still exists; only its hotkey rows are gone).

> The gesture itself (hold-chord, release-commit, digit shadowing) is NOT machine-verifiable here — it is on the owner's live checklist in T6.

- [ ] **Step 6: Commit**

```bash
git add src/sonari/keymap.py src/sonari/platform/macos/hotkeys.py hotkeyd/sonari-hotkeyd.swift tests/test_keymap.py
git commit -m "feat(chooser): keymap chooser_step_* + hotkeyd chooser-mode FSM (dynamic digits, release-poll, 30s cap)"
```

---

## Task T4 — The CYCLE_SESSION dead-code sweep + test migration (ONE commit)

The owner-mandated clean sweep. **Everything in this task is one commit** — `tests/test_concurrency_guards.py:243` imports `MsgType.CYCLE_SESSION`, so the constant's deletion and the hammer swap cannot be split.

**Files:** Modify `src/sonari/protocol.py`, `src/sonari/daemon/__init__.py`, `src/sonari/daemon/features/focus.py`, `src/sonari/daemon/features/playback.py`, `src/sonari/sessions.py` (comment), `tests/test_protocol.py`, `tests/test_daemon_registry.py`, `tests/test_sp2_t6_control_grammar.py`, `tests/test_sp3_cycle.py`, `tests/test_sp3fix_ring.py`, `tests/test_identity_eviction.py`, `tests/test_daemon_spearcon.py`, `tests/test_pitch_dispatch.py`, `tests/test_concurrency_guards.py`. Delete: `tests/test_daemon_cycle.py`. *Depends on: T2, T3.*

### The complete deletion inventory (verified by grep at `3430cbf`)

**Source symbols deleted:**
| Site | Symbol |
|---|---|
| `src/sonari/protocol.py:37` | `MsgType.CYCLE_SESSION` |
| `src/sonari/daemon/features/focus.py:114-160` | `@handler(MsgType.CYCLE_SESSION)` + `on_cycle_session` (the blank separator line through end of file; `_waiting_target`, `on_os_focus`, `on_jump_waiting` STAY) |
| `src/sonari/daemon/__init__.py:39` | the `MsgType.CYCLE_SESSION,` row (+ comment `35` → `34`) |
| `src/sonari/keymap.py:40-41` | `cycle_session_next` / `cycle_session_prev` — **already swapped in T3** |
| `src/sonari/platform/macos/hotkeys.py:119-120` | cycle default bindings — **already swapped in T3** |
| `hotkeyd/sonari-hotkeyd.swift` | no cycle-specific path existed (dumb sender); FSM added in T3 |

**Comment sweep (no behavior):** `playback.py:33-37` ("cycle-onto-muted" → "a chooser commit onto a mute"), `playback.py:124` ("mirroring on_cycle_session and on_jump_waiting" → "mirroring on_jump_waiting and the chooser commit"), `sessions.py:95-96` ("the cycle roster (⌃⌘Tab)" → "the roster (chooser snapshot / W roster)").

**Tests deleted outright (10), with the coverage's new home:**
| Deleted test | Coverage migrated to |
|---|---|
| `tests/test_daemon_cycle.py::test_cycle_next_moves_voice_to_the_next_session_and_cues_it` | `test_chooser.py::test_first_step_opens_and_previews_index_one` + `::test_commit_lands_focus_flowing_cue_and_raise` (T2) |
| `tests/test_daemon_cycle.py::test_cycle_next_wraps_from_last_to_first` | `test_chooser.py::test_step_wraps_past_the_end_back_to_current` (T2) |
| `tests/test_daemon_cycle.py::test_cycle_prev_wraps_from_first_to_last` | `test_chooser.py::test_step_prev_walks_backward` (T2) |
| `tests/test_daemon_cycle.py::test_cycle_with_fewer_than_two_sessions_errors_and_does_not_switch` | `test_chooser.py::test_empty_live_roster_errors_and_does_not_open` + `::test_single_live_candidate_previews_current` (T2; behavior change D6) |
| `tests/test_daemon_cycle.py::test_cycle_raises_target_window` | `test_chooser.py::test_commit_lands_focus_flowing_cue_and_raise` (T2) |
| `tests/test_sp2_t6_control_grammar.py::test_cycle_session_from_workspace_not_speaker_under_divergence` (`:69-81`) | `test_chooser.py::test_snapshot_anchor_is_workspace_not_the_diverged_speaker` (T2) |
| `tests/test_sp2_t6_control_grammar.py::test_cycle_session_parity_when_speaker_equals_foreground` (`:83-89`) | subsumed by every chooser commit test (parity is the default state) |
| `tests/test_sp3_cycle.py::test_cycle_anchor_is_workspace_not_speaker` | `test_chooser.py::test_snapshot_anchor_is_workspace_not_the_diverged_speaker` (T2) |
| `tests/test_sp3_cycle.py::test_cycle_onto_muted_keeps_going_to_active` | `test_chooser.py::test_commit_onto_muted_keeps_going_to_active` (T2) |
| `tests/test_sp3_cycle.py::test_cycle_onto_muted_no_active_reports_via_where_am_i` | `test_chooser.py::test_commit_onto_muted_no_active_reports_via_where_am_i` (T2) |

**Tests rewritten in place (this task, complete code below):** `test_sp3_cycle.py::test_ctrl_s_starts_navigated_muted_workspace` (setup via chooser; the ⌃⌘S assertions unchanged — `::test_ctrl_s_stops_speaker_when_workspace_active` needs NO change, it never cycles); `test_sp3fix_ring.py` tests 1-6 (test 7 `test_jump_waiting_skips_phantom_backlog` stays); `test_identity_eviction.py::test_cycle_skips_evicted_phantom_after_recycle` (`:149-157`); `test_daemon_spearcon.py::test_cycle_uses_spearcon_audio_path_on_hit` + `::test_cycle_falls_back_to_speech_on_miss` (`:11-33`); `test_pitch_dispatch.py::test_cycle_next_does_not_chirp` + `::test_cycle_prev_does_not_chirp` + `::test_cycle_under_two_sessions_does_not_chirp` (`:9-26`). Protocol-table rows removed: `tests/test_protocol.py:81,125`; `tests/test_daemon_registry.py` `ALL_TYPES` row.

- [ ] **Step 1: Red — delete the protocol constant first**

Delete `src/sonari/protocol.py:37`. Run `.venv/bin/python -m pytest tests/test_protocol.py -q` — Expected: FAIL (`AttributeError: type object 'MsgType' has no attribute 'CYCLE_SESSION'` across the suite). This is the red step proving every consumer is found; now sweep them all.

- [ ] **Step 2: Sweep the source**

1. `src/sonari/daemon/features/focus.py`: delete lines 114-160 (the blank line, `@handler(MsgType.CYCLE_SESSION)`, and the whole `on_cycle_session` — it is the last function in the file).
2. `src/sonari/daemon/__init__.py`: delete the `MsgType.CYCLE_SESSION,` row (`:39`); comment `all 35 known keys` → `all 34 known keys`.
3. Comment sweep as inventoried above (`playback.py:33-37`, `playback.py:124`, `sessions.py:95-96`).

- [ ] **Step 3: Sweep + migrate the tests**

1. `git rm tests/test_daemon_cycle.py`
2. `tests/test_sp2_t6_control_grammar.py`: delete lines 68-89 (the `⌃⌘Tab` section header + both tests). The ⌃⌘S and ⌃⌘W tests stay byte-identical.
3. `tests/test_protocol.py`: remove the `"CYCLE_SESSION": "cycle_session",` row from BOTH dicts (`:81`, `:125` at the pre-edit line numbers).
4. `tests/test_daemon_registry.py`: remove `_MsgType.CYCLE_SESSION,` from `ALL_TYPES`.
5. `tests/test_sp3_cycle.py`: delete the 3 migrated tests (see inventory); replace `test_ctrl_s_starts_navigated_muted_workspace` with:
```python
# --- Fork 4: ⌃⌘S STARTS the navigated-to muted workspace (not stop the active speaker) ---
def test_ctrl_s_starts_navigated_muted_workspace():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="B")
    sessions.register("A", cwd="/x/A")             # candidates [B, A, C]
    sessions.register("C", cwd="/x/C")
    daemon._stream("A").stopped = True             # A muted
    daemon._enqueue("A", "prose", "a pile", False)
    daemon._enqueue("C", "prose", "c active", False)
    # Navigate onto the mute via the chooser: first step lands index 1 = A; commit.
    daemon.handle_message(_msg(MsgType.CHOOSER_STEP, "", direction="next"))
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))   # workspace=A(muted), keep-go
    daemon._speak_loop_once()                       # voice keep-goes to C
    assert sessions.workspace() == "A" and sessions.speaker() == "C"
    daemon.handle_message(_msg(MsgType.STOP_SESSION, ""))   # ⌃⌘S: workspace A is muted -> START A
    assert daemon._stream("A").stopped is False     # A started (un-muted)
    assert sessions.speaker() == "A"                # voice moved to the started session
    assert daemon._stream("C").stopped is False     # C the ACTIVE speaker was NOT stopped
```
(One wrinkle: the chooser's preview sits at the front of B's stream at commit time and is removed by `_remove_preview` — the keep-go `_speak_loop_once` therefore voices `c active`, exactly as before.) Keep `test_ctrl_s_stops_speaker_when_workspace_active` unchanged. Update the module to import `MsgType` usage as-is (already does).
6. `tests/test_sp3fix_ring.py`: replace tests 1-6 with the chooser equivalents (keep the module docstring, `_msg`, `_liveness`, `_ident`, and test 7 exactly as they are):
```python
def _browse(daemon):
    daemon.handle_message(_msg(MsgType.CHOOSER_STEP, "", direction="next"))


def _commit(daemon):
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))


# --- 1. the chooser skips a dead-tty phantom and lands on the next LIVE session ---
def test_chooser_skips_dead_tty_phantom_lands_on_next_live(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")   # phantom
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    _browse(daemon); _commit(daemon)
    assert sessions.speaker() == "C"          # candidates [A,C]; step -> C, phantom skipped


# --- 2. R7: a MUTED (stopped) session with a LIVE tty stays chooser-reachable ---
def test_chooser_keeps_muted_but_live_session_reachable(monkeypatch):
    _liveness(monkeypatch, dead=set())
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    daemon._stream("B").stopped = True        # muted, but its terminal is open
    _browse(daemon); _commit(daemon)
    assert sessions.workspace() == "B"        # muted-live stays reachable (not filtered)


# --- 3. muted + dead tty -> filtered (muted-live vs muted-dead distinguished) ---
def test_chooser_filters_muted_and_dead_session(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    daemon._stream("B").stopped = True        # muted AND terminal closed
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    _browse(daemon); _commit(daemon)
    assert sessions.speaker() == "C"          # muted+dead B filtered; landed on live C


# --- 4. empty-tty session -> NOT filtered (fail-open) ---
def test_chooser_does_not_filter_empty_tty_session(monkeypatch):
    _liveness(monkeypatch, dead=set())
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "")   # empty tty
    _browse(daemon); _commit(daemon)
    assert sessions.workspace() == "B"        # empty tty fail-open -> stays reachable


# --- 5. anchor-is-the-phantom: the dead origin is excluded; browsing lands live ---
def test_chooser_when_anchor_is_phantom_lands_on_live(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysA"})   # the origin A itself is dead
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    _browse(daemon)
    assert daemon._chooser.candidates == ["B", "C"]   # A excluded entirely
    _commit(daemon)
    assert sessions.speaker() == "C"          # index 1 of [B, C]; never A
    assert sessions.workspace() != "A"


# --- 6. 1 live + 1 phantom -> the phantom can never land (D6: degenerate browse) ---
def test_chooser_one_live_one_phantom_never_lands_the_phantom(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysB"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    _browse(daemon)
    assert daemon._chooser.candidates == ["A"]    # the phantom is not even browsable
    _commit(daemon)
    assert sessions.foreground() == "A"           # no-op landing; B never satisfied
```
Also update the module docstring's first line to say "chooser" instead of "cycle" and adjust the file's imports if needed (it already imports `MsgType`).
7. `tests/test_identity_eviction.py`: replace test 7 (`:148-157`) with:
```python
# --- 7. the chooser: a recycled node no longer REVIVES the phantom in ⌃⌘Tab ---
def test_chooser_skips_evicted_phantom_after_recycle(monkeypatch):
    _liveness(monkeypatch, dead=set())            # node exists again (recycled)
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("stale", cwd="/x/stale"); _ident(sessions, "stale", "/dev/ttysT")
    sessions.register("fresh", cwd="/x/fresh"); _ident(sessions, "fresh", "/dev/ttysT")
    daemon.handle_message(_msg(MsgType.CHOOSER_STEP, "", direction="next"))
    daemon.handle_message(_msg(MsgType.CHOOSER_COMMIT, ""))
    # candidates [A, fresh] (stale evicted); step -> fresh. Pre-fix it landed on stale.
    assert sessions.speaker() == "fresh"
```
8. `tests/test_daemon_spearcon.py`: replace the two cycle tests (`:11-33`) with:
```python
def test_chooser_commit_uses_spearcon_audio_path_on_hit():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha"); sessions.register("B", cwd="/x/bravo")
    sessions.set_foreground("A")
    p = _hit(daemon, "bravo")
    daemon.handle_message({"type": "chooser_step", "direction": "next"})
    daemon.handle_message({"type": "chooser_commit"})
    item = daemon._stream("B").queue._items[0]
    assert item.audio_path == p                       # the LANDING cue is spearcon-capable
    assert item.names_session and item.mute_exempt
    daemon._speak_loop_once()
    assert speaker.audio_paths == [p]                 # afplayed, not spoken


def test_chooser_commit_falls_back_to_speech_on_miss():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha"); sessions.register("B", cwd="/x/bravo")
    sessions.set_foreground("A")
    daemon.handle_message({"type": "chooser_step", "direction": "next"})
    daemon.handle_message({"type": "chooser_commit"})
    item = daemon._stream("B").queue._items[0]
    assert item.audio_path is None
    daemon._speak_loop_once()
    assert speaker.spoken == ["bravo."]               # unchanged spoken landing cue
    assert "bravo" in daemon._spearcons.generated     # kicked background gen
```
(The commit's `_remove_preview` cleared the preview from A's stream, so the loop's first pop is the landing cue in B's stream — `speaker.spoken`/`audio_paths` stay single-element.)
9. `tests/test_pitch_dispatch.py`: replace the three cycle tests (`:9-26`) with:
```python
def test_chooser_step_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    _two(daemon, sessions); sessions.set_foreground("A")
    daemon.handle_message({"type": "chooser_step", "direction": "next"})
    daemon.handle_message({"type": "chooser_step", "direction": "prev"})
    assert speaker.pitches == []


def test_chooser_commit_does_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    _two(daemon, sessions); sessions.set_foreground("A")
    daemon.handle_message({"type": "chooser_step", "direction": "next"})
    daemon.handle_message({"type": "chooser_commit"})
    assert speaker.pitches == []


def test_chooser_error_paths_do_not_chirp():
    daemon, q, speaker, sessions, _ = make_daemon(foreground="A")
    daemon.handle_message({"type": "chooser_digit", "digit": 9})   # unknown number
    assert speaker.pitches == []          # error case: earcon, never a directional cue
```
10. `tests/test_concurrency_guards.py`: in the hammer `ops` list (`:242-243`) replace `MsgType.CYCLE_SESSION` with the four chooser messages:
```python
            ops = [MsgType.STOP_SESSION, MsgType.FLUSH, MsgType.SET_FOREGROUND,
                   MsgType.JUMP_WAITING, MsgType.CHOOSER_STEP, MsgType.CHOOSER_DIGIT,
                   MsgType.CHOOSER_COMMIT, MsgType.CHOOSER_CANCEL, MsgType.STOP_ALL]
```
(`_msg(op, sess)` carries no `direction`/`digit`: `CHOOSER_STEP` defaults to "next"; a digit-less `CHOOSER_DIGIT` exercises the error path — both are legitimate robustness inputs. A hammered commit runs `sessions.focus()` + the muted-landing `set_speaker(None)` — the exact pointer-race shape the old `CYCLE_SESSION` op exercised — plus the new capture/restore interleavings. `will_attempt(None)` is False with no identities, so no raise fires, same as the `JUMP_WAITING` op.)
Then update the three comment passages that name the old op — the s_bg preamble (`:110-132`), the `hammer()` docstring comment (`:211-241`), and the counter comments (`:276-296`): replace each mention of `CYCLE_SESSION` with "a chooser commit (CHOOSER_STEP→CHOOSER_COMMIT)" and each `on_cycle_session` with `the chooser commit (features/chooser.py)`. The described mechanics are unchanged — commit calls `sessions.focus(target)` (parking the workspace), unconditionally lifts `voice_state` to flowing, and releases the speaker off a muted landing — so ONLY the names change. **Do not alter any assertion, counter, or the `finally` restore.**

- [ ] **Step 4: Verify the sweep is total, then green**

Run: `grep -rn "cycle_session\|CYCLE_SESSION\|on_cycle_session" src tests hotkeyd` — Expected: **zero hits**. (Historic docs/plans under `docs/` are exempt.)
Run: `.venv/bin/python -m pytest -q`
Expected: ALL green — 941 − 10 deleted + T1/T2/T3's additions, 1 skipped; `tests/test_concurrency_guards.py` green (run it twice more back-to-back: `.venv/bin/python -m pytest tests/test_concurrency_guards.py -q` — the stress guard is probabilistic; if `real_keep_going_fires` ever flakes, widen the idle window per its own comment, never weaken).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(chooser)!: delete CYCLE_SESSION end-to-end; coverage migrated to the chooser (owner-mandated sweep)"
```

---

## Task T5 — Numbers in ⌃⌘W, the double-press roster, the registration announce

**Files:** Modify `src/sonari/daemon/features/control.py`, `src/sonari/daemon/features/lifecycle.py`, `src/sonari/daemon/host.py` (one `__init__` field). New: `tests/test_where_roster.py`. Modify (enumerated string/count updates): `tests/test_daemon_where_am_i.py`, `tests/test_daemon_spearcon.py`, `tests/test_sp3_voicestate.py`, `tests/test_sp3_hold_entry.py`, `tests/test_sp3fix_grammar.py`, `tests/test_daemon_setup_health.py`.

**Interfaces produced:** `control._now` (monkeypatchable), `control.W_DOUBLE_S = 2.0`, `host._last_where_ts`; the numbered W grammar `"Voice: {folder} {n}, {state}.[ Keyboard: {folder} {n}.] {W} waiting, {M} muted."`; the roster grammar `"{n}, {folder}[, muted][, {k} waiting]."` space-joined in number order; the announce `"{folder}, {n}."` (`"Another session, {n}."` when folder unknown). **Interfaces consumed:** T1's `number()`. *Depends on: T1 (T2/T4 only for suite ordering).*

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_where_roster.py (new)
"""Spec §6/§7: numbers in the ⌃⌘W clauses, the double-press roster (2.0 s,
daemon-side, injectable clock), and the verbosity-gated registration announce."""
from sonari.protocol import MsgType
from sonari.daemon.features import control, lifecycle
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _clock(monkeypatch, start=100.0):
    t = {"v": start}
    monkeypatch.setattr(control, "_now", lambda: t["v"])
    return t


# --- double-press detection: 1.9 s escalates, 2.1 s does not ---
def test_double_press_within_2s_escalates_to_the_roster(monkeypatch):
    t = _clock(monkeypatch)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")
    sessions.register("B", cwd="/x/api")
    daemon._enqueue("B", "prose", "b1", False)
    daemon._enqueue("B", "prose", "b2", False)
    daemon._stream("B").stopped = True                  # muted AND 2 waiting
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Voice: web 1, Playing. 0 waiting, 1 muted."
    t["v"] += 1.9
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "1, web. 2, api, muted, 2 waiting."


def test_slow_second_press_repeats_the_summary(monkeypatch):
    t = _clock(monkeypatch)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    t["v"] += 2.1
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Voice: web 1, Playing. 0 waiting, 0 muted."


def test_roster_lists_all_sessions_in_number_order(monkeypatch):
    t = _clock(monkeypatch)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")
    sessions.register("B", cwd="/x/api")
    sessions.register("C")                              # unknown folder
    daemon._enqueue("C", "prose", "c1", False)
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    t["v"] += 0.5
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "1, web. 2, api. 3, another session, 1 waiting."


def test_roster_delivery_barges_in_and_resumes_like_the_summary(monkeypatch):
    from sonari.queue import SpeechItem
    t = _clock(monkeypatch)
    daemon, queue, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    daemon._current_item = SpeechItem(id=905, session="A", kind="prose",
                                      text="interrupted", is_decision=False)
    t["v"] += 1.0
    cancels_before = speaker.cancels
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    assert speaker.cancels == cancels_before + 1        # barge-in
    texts = [it.text for it in daemon._stream("A").queue._items]
    assert texts[0] == "1, web."                        # roster first
    assert texts[1] == "interrupted"                    # then the resume


# --- the registration announce ---
def test_session_start_announces_folder_and_number(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x/proj"))
    q = stream_queue(daemon, "s1")
    assert len(q) == 1
    item = q.pop_next()
    assert item.text == "proj, 1."
    assert item.mute_exempt and item.names_session


def test_announce_suppressed_at_verbosity_quiet(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, _ = make_daemon(verbosity="quiet", foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x/proj"))
    assert len(stream_queue(daemon, "s1")) == 0


def test_announce_not_refired_on_resume_of_a_known_session(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x/proj"))
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x/proj"))  # resume/compact
    assert len(stream_queue(daemon, "s1")) == 1        # ONE announce, not two


def test_announce_unknown_folder_says_another_session(monkeypatch):
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon, queue, speaker, sessions, _ = make_daemon(foreground=None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1"))
    assert stream_queue(daemon, "s1").pop_next().text == "Another session, 1."
```

And apply these exact assertion updates in the existing files (each is `"Voice: {folder}"` → `"Voice: {folder} {n}"`, and the Keyboard clause gains its number):

| File:line (pre-edit) | Old exact string | New exact string |
|---|---|---|
| `tests/test_daemon_where_am_i.py:10` | `"Voice: work, Playing. 0 waiting, 0 muted."` | `"Voice: work 1, Playing. 0 waiting, 0 muted."` |
| `tests/test_daemon_where_am_i.py:18` | `"Voice: Unknown session, Playing. 0 waiting, 0 muted."` | `"Voice: Unknown session 1, Playing. 0 waiting, 0 muted."` |
| `tests/test_daemon_where_am_i.py:27` | `"Voice: work, Stopped. 0 waiting, 0 muted."` | `"Voice: work 1, Stopped. 0 waiting, 0 muted."` |
| `tests/test_daemon_where_am_i.py:39` | `"Voice: work, Playing. 2 waiting, 1 muted."` | `"Voice: work 1, Playing. 2 waiting, 1 muted."` |
| `tests/test_daemon_where_am_i.py:55` | `"Voice: work, Playing. 0 waiting, 0 muted."` | `"Voice: work 1, Playing. 0 waiting, 0 muted."` |
| `tests/test_daemon_where_am_i.py:77` | `"Voice: work, Playing. 0 waiting, 0 muted."` | `"Voice: work 1, Playing. 0 waiting, 0 muted."` |
| `tests/test_daemon_spearcon.py:85` | `["Voice: work, Playing. 0 waiting, 0 muted."]` | `["Voice: work 1, Playing. 0 waiting, 0 muted."]` |
| `tests/test_daemon_spearcon.py:94` | `["Voice: work, Playing. 0 waiting, 0 muted."]` | `["Voice: work 1, Playing. 0 waiting, 0 muted."]` |
| `tests/test_sp3_voicestate.py:44` | `["Voice: work, Playing. 0 waiting, 0 muted."]` | `["Voice: work 1, Playing. 0 waiting, 0 muted."]` |
| `tests/test_sp3_hold_entry.py:93` | `"Voice: work, On hold. 0 waiting, 0 muted."` | `"Voice: work 1, On hold. 0 waiting, 0 muted."` |
| `tests/test_sp3_hold_entry.py:102` | `"Voice: work, All stopped. 0 waiting, 0 muted."` | `"Voice: work 1, All stopped. 0 waiting, 0 muted."` |
| `tests/test_sp3fix_grammar.py:20` | `"Voice: api, Playing. Keyboard: web. 1 waiting, 1 muted."` | `"Voice: api 2, Playing. Keyboard: web 1. 1 waiting, 1 muted."` |
| `tests/test_sp3fix_grammar.py:29` | `["Voice: work, Playing. 0 waiting, 0 muted."]` | `["Voice: work 1, Playing. 0 waiting, 0 muted."]` |
| `tests/test_sp3fix_grammar.py:41` | `["Voice: work, Playing. 0 waiting, 2 muted."]` | `["Voice: work 1, Playing. 0 waiting, 2 muted."]` |

(`tests/test_sp2_t6_control_grammar.py:106-107` uses SUBSTRING asserts — `"Voice: bravo" in s` — which still pass with `"Voice: bravo 2"`: NO change there. The None-speaker cues "Nothing playing."/"On hold."/"All stopped." carry no number: unchanged.)

And the announce's blast radius (`tests/test_daemon_setup_health.py` — D7):
1. `test_session_start_enqueues_one_cue_when_not_installed` (`:89-99`) — replace the queue assertions with:
```python
    q = stream_queue(daemon, "s1")
    assert len(q) == 2                       # the registration announce + the guidance cue
    first = q.pop_next()
    assert first.text == "Another session, 1."   # announce leads (names the session)
    item = q.pop_next()
    assert item.kind == "prose"
    assert "slash sonari install" in item.text.lower()
```
2. `test_session_start_silent_when_ok` (`:102-106`) — rename + retarget the intent (health guidance is silent; the announce is deliberate):
```python
def test_session_start_announce_only_when_ok(monkeypatch):
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    monkeypatch.setattr(lifecycle, "_setup_health", lambda v: ("ok", None))
    daemon.handle_message(_ss("s1"))
    q = stream_queue(daemon, "s1")
    assert len(q) == 1                       # ONLY the announce; no guidance cue
    assert q.pop_next().text == "Another session, 1."
```
3. `test_session_start_cue_throttled_per_session` (`:109-115`) — the count becomes 2 with a comment:
```python
    assert len(stream_queue(daemon, "s1")) == 2  # 1 announce (first start only) + 1 cue; neither repeats
```
(If `stream_queue` is not yet imported at the top of that file, add it to the existing `tests.daemon_helpers` import.)

- [ ] **Step 2: Run to verify the reds**

Run: `.venv/bin/python -m pytest tests/test_where_roster.py tests/test_daemon_where_am_i.py tests/test_daemon_setup_health.py -q`
Expected: `test_where_roster.py` FAILS (no numbers, no roster, no announce); the edited exact-string tests FAIL (code still speaks the un-numbered strings); setup-health edits FAIL (no announce yet).

- [ ] **Step 3: Implement**

`src/sonari/daemon/host.py` — in `SpeechDaemon.__init__`, after the `self._chooser = None` block (T2):
```python
        # ⌃⌘W double-press detection (spec §7): the monotonic time of the last
        # WHERE_AM_I, daemon-side. Written under the handler lock.
        self._last_where_ts = None
```

`src/sonari/daemon/features/control.py`:
1. After the imports (below `VERBOSITY_LEVELS`, `:11`):
```python
# Injectable clock for the ⌃⌘W double-press window (tests patch control._now).
_now = time.monotonic

# A second WHERE_AM_I within this window escalates to the numbered roster (§7).
W_DOUBLE_S = 2.0


def _numbered(host, session):
    """'{folder} {n}' — the spoken name+number for a session ('Unknown session'
    fallback; the number is omitted only if the session is somehow unregistered)."""
    folder = host.sessions.folder(session) or "Unknown session"
    n = host.sessions.number(session)
    return "{0} {1}".format(folder, n) if n is not None else folder


def _roster_text(host):
    """The numbered roster (double-⌃⌘W, §7): EVERY registered session in NUMBER
    order, '{n}, {folder}[, muted][, {k} waiting].' — waiting is that stream's
    queue length when > 0. Unfiltered, like the summary's counts (plan D4)."""
    sessions = host.sessions
    ids = sorted(sessions.session_ids(), key=lambda s: sessions.number(s) or 0)
    parts = []
    for s in ids:
        seg = "{0}, {1}".format(sessions.number(s),
                                sessions.folder(s) or "another session")
        st = host._streams.get(s)
        if st is not None and st.stopped:
            seg += ", muted"
        k = len(st.queue) if st is not None else 0
        if k > 0:
            seg += ", {0} waiting".format(k)
        parts.append(seg + ".")
    return " ".join(parts)
```
2. In `on_where_am_i` (`:146-225`) apply exactly three edits:
   - At the top of the function body (before the `fg = host.sessions.speaker()` line, keeping the `host = ctx.host` line first):
```python
    # Double-press escalation (§7): a second W within the window speaks the
    # numbered roster instead of repeating the summary. Detection is daemon-side
    # (no new binding); the clock is module-level for test injection.
    now = _now()
    prev_ts = host._last_where_ts
    host._last_where_ts = now
    roster = prev_ts is not None and (now - prev_ts) <= W_DOUBLE_S
```
   - In the `fg is None` branch, change the `cue = (...)` assignment to:
```python
            cue = (_roster_text(host) if roster
                   else "All stopped." if vs == "stopped-all"
                   else "On hold." if vs == "quiet-hold"
                   else "Nothing playing.")
```
   - In the main branch: change `voice_folder = host.sessions.folder(fg) or "Unknown session"` to `voice_folder = _numbered(host, fg)`; change the keyboard clause `kbd = (" Keyboard: {0}.".format(host.sessions.folder(ws) or "Unknown session") ...` to `kbd = (" Keyboard: {0}.".format(_numbered(host, ws)) ...`; and change the final `text = ...` composition to:
```python
    if roster:
        text = _roster_text(host)
    else:
        text = "Voice: {0}, {1}.{2} {3} waiting, {4} muted.".format(
            voice_folder, state, kbd, waiting, muted)
```
   Everything else in the function (capture, `speaker.cancel()`, the resume re-enqueue, the final `_enqueue` to `fg`'s stream) is byte-identical — the roster inherits the summary's exact delivery per §7.

`src/sonari/daemon/features/lifecycle.py` — in `on_set_foreground`:
1. After `cwd = msg.get("cwd")` (`:61`):
```python
    # Newness must be captured BEFORE the Policy-A gate: both gate branches
    # _record() the session, so this is the only observation point (announce D5).
    is_new = session not in ctx.host.sessions.session_ids()
```
2. Inside the `if t == MsgType.SESSION_START:` block, immediately after `ctx.host.sessions.register(session, cwd=cwd)` (`:98`) and BEFORE `_maybe_guide_setup`:
```python
        if is_new and ctx.verbosity != "quiet":
            # Registration announce (spec §6): "{folder}, {number}." so digit
            # teleports are learnable eyes-free. Suppressed at quiet; never
            # re-fired on resume/clear/compact of a known id. Lands in the new
            # session's own stream (voiced now if it took the voice, else heard
            # when keep-going/jump reaches it). names_session: it names itself.
            folder = ctx.host.sessions.folder(session)
            ctx.host._enqueue(
                session, "prose",
                "{0}, {1}.".format(folder or "Another session",
                                   ctx.host.sessions.number(session)),
                False, mute_exempt=True, names_session=True)
```

- [ ] **Step 4: Run to verify green + guards**

Run: `.venv/bin/python -m pytest tests/test_where_roster.py tests/test_daemon_where_am_i.py tests/test_daemon_spearcon.py tests/test_sp3_voicestate.py tests/test_sp3_hold_entry.py tests/test_sp3fix_grammar.py tests/test_sp2_t6_control_grammar.py tests/test_daemon_setup_health.py tests/test_daemon_focus_follow.py tests/test_daemon_control.py tests/test_concurrency_guards.py -q`
Expected: PASS. (The last three files send SESSION_START but assert only foreground/identity/registry facts — verified at `3430cbf` — so the announce cannot break them; they are in the run to prove it.) Then the full suite: `.venv/bin/python -m pytest -q` — green.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/host.py src/sonari/daemon/features/control.py src/sonari/daemon/features/lifecycle.py tests/test_where_roster.py tests/test_daemon_where_am_i.py tests/test_daemon_spearcon.py tests/test_sp3_voicestate.py tests/test_sp3_hold_entry.py tests/test_sp3fix_grammar.py tests/test_daemon_setup_health.py
git commit -m "feat(chooser): numbered W clauses + double-press roster (2.0s) + registration announce (spec §6-§7)"
```

---

## Task T6 — Final verification + the owner's live checklist

**Files:** none modified (verification + the build-report checklist only). *Depends on: T1–T5.*

- [ ] **Step 1: Full suite, guards emphasized**

Run: `.venv/bin/python -m pytest -q`
Expected: all green, `1 skipped` (the pre-existing skip), total ≈ 941 − 10 deleted + ~45 added (do not hard-pin the number; pin ZERO failures). Then run the guards three times back-to-back: `for i in 1 2 3; do .venv/bin/python -m pytest tests/test_concurrency_guards.py -q || break; done` — all three green.

- [ ] **Step 2: The sweep is still total + the Swift binary still compiles**

Run: `grep -rn "cycle_session\|CYCLE_SESSION" src tests hotkeyd` → zero hits.
Run: `swiftc hotkeyd/sonari-hotkeyd.swift -o "$TMPDIR/sonari-hotkeyd-check" && echo BUILD-OK` → BUILD-OK.
Run: `.venv/bin/python -m pytest tests/test_keymap.py::test_resolved_default_keymap_has_chooser_and_no_cycle -q` → green (the spec §10 resolved-keymap assertion).

- [ ] **Step 3: Ship the live checklist in the build report** (the gesture layer is human-verifiable only; deployment = merge → `sonari install`, which recompiles hotkeyd via the srchash path and rewrites the resolved keymap):

1. Hold ⌃⌘, tap Tab: hear "{n}, {folder}" previews; hold Tab for key-repeat fast-walking; ⇧Tab steps back.
2. Quick tap-and-release: lands on the previous session (the ⌘Tab toggle), landing cue + window raise.
3. Release on "…, current": silence, interrupted speech resumes where it was cut.
4. Digit while held: instant teleport; an unknown digit: error earcon (NOTE D1: after an unknown digit the chord is inert — re-press ⌃⌘Tab within 30 s to resume the same browse).
5. ⌃⌘1–9 in another app with NO chord held: must reach that app, never Sonari (digits unregister on mode exit).
6. Commit onto a muted session: window comes forward, the mute stays silent, another active session keeps talking.
7. ⌃⌘W once: "Voice: {folder} {n}, …"; quick second press: the numbered roster.
8. Open a brand-new session: hear "{folder}, {n}." (and nothing at `sonari verbosity quiet`).
9. Wedge test: hold the chord 30 s doing nothing → cancel (speech resumes, nothing moves).

---

## Self-Review

**1. Spec coverage (against the oracle's §3–§10):**
- **§3 the chooser:** entry/step/⇧Tab/digit/release-commit/30 s cancel — T2 handlers + T3 FSM; candidate order (current → MRU → never-visited, `is_live` filter, wrap) — `_snapshot` + tests; previews (barge-in, W-cue delivery flags, "{n}, {folder}[, muted][, current].", move NOTHING) — `_deliver_preview` + `test_preview_flags_are_the_w_cue_flags`/`test_first_step_opens_and_previews_index_one` (raise-free pinned by `rs.attempts == []`); commit verbatim cycle-landing incl. Fork-2 muted keep-go — `_commit` + migrated tests; no-op index-0 landing + capture/resume — `test_commit_to_current_is_silent_noop_and_resumes_captured`; tap-release = previous-session toggle — open-on-first-step lands index 1 by construction. ✓
- **§4 wire protocol:** 4 new MsgTypes added (T2), `CYCLE_SESSION` + keymap actions + handler + hotkeyd path + superseded tests deleted (T4) with a grep-zero proof and a per-test migration map. ✓
- **§5 hotkeyd:** FSM with mode-scoped digit registration, 40 ms `NSEvent.modifierFlags` poll (shift excluded from the release condition — ⇧Tab), 30 s cap, exit-always cleanup; the 0.5 s focus poller untouched. Compile-verified; gesture on the live checklist. ✓
- **§6 numbers:** lowest-free/stable/freed (T1), spoken in previews + roster + W clauses + announce (T2/T5), NOT in attribution or jump cues (nothing touches `_attributed_text`/`on_jump_waiting`), >9 spoken-but-unreachable pinned by `test_numbers_above_nine_are_assigned`. ✓
- **§7 double-W roster:** 2.0 s daemon-side monotonic window, injectable clock, number-ordered "{n}, {folder}[, muted][, {k} waiting].", summary-identical delivery — T5 + `test_roster_delivery_barges_in_and_resumes_like_the_summary`. 1.9/2.1 boundary tests as spec §10 demands. ✓
- **§8 MRU:** deliberate-acts-only writers (`set_foreground`/`focus`/matched `set_os_focus`), `set_speaker` never — T1 tests pin all four rules. ✓
- **§9 invariants:** R12 (commit uses `focus()`; the ONLY new `_foreground` writers are the pre-existing setters), M1 (no speak-loop edit anywhere in the plan), Fork-2 + W1/eviction through the chooser (migrated tests), hammer gains all four chooser messages with zero weakened assertions (T4). ✓
- **§10 testing list:** numbering ✓ (T1), MRU rules ✓ (T1), open/step/wrap/digit/commit/cancel/stale ✓ (T2), muted-commit keep-go ✓, eviction+dead-tty through the chooser ✓ (T2 + T4 rewrites), W double-press 1.9/2.1 ✓, capture/resume ✓, verbosity-gated announce ✓ (T5), swiftc build + resolved-keymap assertion ✓ (T3/T6), live checklist ✓ (T6).

**2. Placeholder scan:** every step carries complete, transcribable code (no `...`, no TODO). The only non-code deliverable is T6's checklist, which is itself the deliverable.

**3. Name/type consistency across tasks:** `number/session_for_number/mru` (T1 defs → T2 `_snapshot`/`_preview_text`/`on_chooser_digit`, T5 `_numbered`/`_roster_text`); `remove_by_id` (T1 → T2 `_remove_preview`); `host._chooser` + `ChooserState` field names used identically in module and tests; `chooser._now`/`STALE_S` and `control._now`/`W_DOUBLE_S` are module-level for monkeypatching; the wire strings `chooser_step/chooser_digit/chooser_commit/chooser_cancel` match protocol constants, `ACTION_MESSAGES`, and the Swift literals.

**4. Green-at-every-commit sequencing:** T2 adds the chooser beside the live cycle; T3 swaps only bindings (cycle handler + its `handle_message`-driven tests untouched); T4 deletes constant+handler+tests+hammer-op in ONE commit (the guards file imports the constant — split = red); T5's behavior changes carry their exact-string updates in the same commit, with the announce's full blast radius (3 setup-health tests) pre-enumerated by grep, not discovered mid-flight.

**5. Known risks, stated:** (a) the stress guard is probabilistic — its own comments mandate widening the idle window over weakening, echoed in T4/T6; (b) D1's inert-chord-after-unknown-digit is a spec-internal conflict resolved in the daemon's favor and surfaced on the live checklist rather than silently settled; (c) the Swift release-poll reads `NSEvent.modifierFlags` inside an AppKit app (`NSApplication.shared` + `app.run()` already exist at `sonari-hotkeyd.swift:225-237`) — permission-free per the spec's 2026-07-14 verification; only the live checklist can prove the felt gesture.
