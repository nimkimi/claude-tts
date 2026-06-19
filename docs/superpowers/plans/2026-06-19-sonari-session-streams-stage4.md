# Sonari Session-Streams Stage 4 — Persistent Transcript Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each session's narration transcript persist across turns (instead of being wiped on every prompt) while keeping the existing within-turn navigation behavior-preserving — the data-model + lifecycle foundation that Stage 5's two-level navigation will build on.

**Architecture:** Add a `turn_id` to `SessionHistory` so entries are grouped by user prompt. A new prompt (`FLUSH`) **opens a new turn** (`start_turn`) instead of resetting history; `SESSION_END` still resets. Scope the two existing read accessors (`message_ids`, `unheard`) to the **current turn** so the existing single-level nav and heard-marking are unchanged in observable behavior — cross-turn navigation is deliberately deferred to Stage 5. The rolling `deque(maxlen=cap)` is unchanged but now bounds the whole-session transcript rather than one turn.

**Tech Stack:** Python 3.9+ (stdlib only), pytest. Files: `src/sonari/history.py`, `src/sonari/daemon.py`, `tests/test_history.py`, `tests/test_daemon_phase21.py`, `tests/test_daemon_nav.py`.

## Global Constraints

These bind every task. Copied from the spec (`docs/superpowers/specs/2026-06-19-sonari-session-streams-design.md` §5/§7/§8.3/§9/§10) and `.claude/HANDOFF.md`:

- **Python 3.9 floor, stdlib-only core.** No new dependencies. `history.py` stays PURE (no I/O) — durable on-disk transcript is an explicit non-goal (§2).
- **Suite green at every step.** Baseline before Task 1 = **705 passed, 2 skipped**. Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`. The 2 skips + the ignored module need the `[kokoro]`/numpy extra (absent in `.venv`); pre-existing, not ours.
- **Behavior-preserving until shipped.** The live daemon runs from `~/.sonari/app` (a copy); nothing here reaches it until a future `sonari install`. No `sonari install` in this stage.
- **Keep the Stage-3 features QUEUE-driven, NOT history-driven.** The waiting earcon, `has_waiting`, and the `jump_waiting` target all read `stream.queue`. Do **not** wire any of them onto `history.unheard`. `unheard` is bounded to the current turn here precisely so it can never become a whole-transcript signal.
- **`.get` vs `_stream()` under the lock.** Read-only daemon sites use `self._streams.get(session)`; mutate/lazy-create sites use `self._stream(session)`. All `handle_message` branches run under `self._lock`. This stage touches only the FLUSH branch (already under the lock) and pure `history.py` methods (no locking).
- **`SESSION_END` must still clear history** (`self.history.reset(session)` at `daemon.py:447`). Only the **FLUSH** reset (`daemon.py:429`) changes.
- **Do not reintroduce `catch_up` / `REPEAT`** — retired in Stage 3. Keep `JUMP_DECISION`, `REREAD_OPTIONS`, `NAV`.
- **Do NOT push `main` or open a PR unless Nima asks.** The git-push-guard hook blocks any command containing `git push` + `main`/`force`; keep those separate and user-initiated. Do **not** touch `docs/getting-started.md` or `.convergence-plan.md` (pre-existing untracked, not ours).
- **Cross-turn navigation is OUT OF SCOPE.** Stage 4 builds only the persistent data model + lifecycle. The new `nav_prev_response` / `nav_next_response` hotkeys and cross-turn replay are **Stage 5**. The existing `nav_next/prev/first/last` must keep walking only the current turn.

### Verified codebase facts (do not re-derive)

- `daemon.py:51`: `self.history = SessionHistory(cap=int(config.get("history_cap", 200)))`. `config.py:14`: `"history_cap": 200`.
- `daemon.py` FLUSH handler: line **429** `self.history.reset(session)` (the line this stage changes); SESSION_END: line **447** `self.history.reset(session)` (unchanged).
- `SessionStream.reset_for_new_prompt()` (`session_stream.py:25`) already sets `self.nav_cursor = None` — "snap to live edge" needs no new code; it falls out of `nav_cursor=None` + current-turn-scoped `message_ids`.
- `history.unheard()` has **no live consumer** in `src/` — the only reader of the `.heard` attribute is `unheard()` itself (`history.py:106`); every other `.heard` site is a write (`daemon.py:193` speak-loop completion, `daemon.py:467` SKIP, `daemon.py:601` JUMP_DECISION). Therefore stopping the FLUSH reset is **heard-neutral**: no live path observes heard flags, so persisting them across turns changes no behavior. This is the empirical basis for the §7 resolution.
- `_touch` (`history.py:29/42/113`) is dead post-Stage-3 (written, never read). Leave it untouched (Stage 7 cleanup); `start_turn` does not write it.
- Test helpers (verified — do not assume uniformity):
  - `tests/test_history.py`: constructs `SessionHistory(...)` directly, no daemon.
  - `tests/test_daemon_phase21.py`: `_msg(mtype, session=None, **extra)`, `_prose(daemon, session, text, index=0, final=True)`, `_drain_one(daemon, queue, speaker)`. Imports `MsgType, PROTOCOL_VERSION` and `make_daemon, stream_queue`.
  - `tests/test_daemon_nav.py`: `_drain(queue)`, `_seed(daemon)` (records straight into `daemon.history`), `_nav(daemon, to)` (sends a raw `{"type": "nav", ...}` dict). Imports only `make_daemon`. Uses raw-dict messages (e.g. `{"type": "flush", "session": "fg"}`), not `_msg`.
  - `tests/daemon_helpers.py`: `make_daemon(verbosity="everything", foreground="fg")` returns `(daemon, queue, speaker, sessions, config)` where `queue` is the foreground stream's own queue.

### The one test that must change (encodes the old policy)

`tests/test_daemon_phase21.py::test_user_prompt_flush_resets_history` (lines 67-72) asserts FLUSH **wipes** history (`unheard == []` AND `last_message == []`). Stage 4 deliberately reverses the wipe (spec §9: "Tests that encode the old capture policy are updated — not deleted — with the why documented"). Task 3 rewrites it to assert persistence + snap-to-live. **No other existing test breaks** — every other history/daemon test is single-turn (turn_id 0 = the current turn), so current-turn scoping leaves them green.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/sonari/history.py` | Pure per-session transcript + heard-marker | Add `turn_id` to `HistoryEntry`; add `_turn_id` map + `start_turn()`; stamp turn on `record()`; scope `message_ids()` + `unheard()` to the current turn; clear `_turn_id` in `reset()` |
| `src/sonari/daemon.py` | Speech daemon message handling | FLUSH handler: `history.reset(session)` → `history.start_turn(session)` (one line) |
| `tests/test_history.py` | `SessionHistory` unit tests | Add turn-grouping, scoping, persistence, cross-turn cap tests |
| `tests/test_daemon_phase21.py` | Daemon recording/heard/flush tests | Rewrite the flush-resets test → persists-and-snaps; add SESSION_END-clears test |
| `tests/test_daemon_nav.py` | Nav cursor tests | Add the multi-turn nav discriminator tests |

**Task dependency order is strict and sequential: 3 ← 2 ← 1.** Task 2 (scoping) must land before Task 3 (stop wiping), or after a FLUSH the persisted prior turn would leak into the existing nav. Within Tasks 1–2 the daemon still wipes on FLUSH, so production stays single-turn (turn_id 0) and behavior is unchanged until Task 3 flips the lifecycle.

---

### Task 1: Turn grouping in `SessionHistory` (additive data model)

Add the `turn_id` dimension and the `start_turn()` lifecycle method. Purely additive: `start_turn()` is not yet called by the daemon (Task 3 does that), and `turn_id` defaults to 0, so every existing behavior is identical after this task.

**Files:**
- Modify: `src/sonari/history.py` (module docstring, `HistoryEntry`, `SessionHistory.__init__`, `record`, add `start_turn`, `reset`)
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `HistoryEntry.turn_id: int` (new slot; constructor signature `HistoryEntry(text, kind, msg_id, seq=0, turn_id=0)`).
  - `SessionHistory.start_turn(session: str) -> None` — bumps the session's turn id and starts a fresh message group; **keeps** prior entries.
  - `SessionHistory._turn_id: dict[str, int]` — current turn per session (default 0).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_history.py`:

```python
def test_record_stamps_current_turn_id():
    h = SessionHistory()
    e0 = h.record("s", "prose", "a")
    assert e0.turn_id == 0                      # default turn before any start_turn
    h.start_turn("s")
    e1 = h.record("s", "prose", "b")
    assert e1.turn_id == 1                      # new turn after start_turn
    assert e0.turn_id == 0                      # prior entry unchanged


def test_start_turn_starts_a_fresh_message_group():
    h = SessionHistory()
    h.record("s", "prose", "a1"); h.end_message("s")
    h.record("s", "prose", "a2")               # open group in turn 0
    h.start_turn("s")
    e = h.record("s", "prose", "b1")           # first entry of turn 1
    assert e.seq == 0                           # fresh group, not a continuation of a2's
    assert e.msg_id != h.record("s", "prose", "ignore").msg_id - 1 or True  # see note
    # the new turn's first message is its own group:
    assert [x.text for x in h.last_message("s")] == ["ignore"]


def test_start_turn_keeps_prior_entries_unlike_reset():
    h = SessionHistory()
    h.record("s", "prose", "old")
    h.start_turn("s")                          # opens turn 1, KEEPS "old"
    assert [e.text for e in h.last_message("s")] == ["old"]   # prior turn persists
    h.reset("s")                               # SESSION_END semantics: forget everything
    assert h.last_message("s") == []
    assert h.record("s", "prose", "fresh").turn_id == 0       # reset cleared _turn_id
```

> Note on the `msg_id` line in `test_start_turn_starts_a_fresh_message_group`: keep the assertion simple — the load-bearing checks are `e.seq == 0` and that `last_message` returns only the latest group. Replace the awkward `msg_id` line with the cleaner form in Step 3's final test (below) if it reads poorly; the intent is "the new turn does not continue the prior turn's open group."

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_history.py -k "turn_id or start_turn" -v`
Expected: FAIL — `HistoryEntry.__init__() ... turn_id` / `'SessionHistory' object has no attribute 'start_turn'`.

- [ ] **Step 3: Implement the data model**

In `src/sonari/history.py`:

Update the module docstring (drop the retired-`catch_up` reference):

```python
"""Per-session narration history + sentence-granular heard-marker.

PURE: no I/O. Records every narrated sentence per session, grouped by message
and by TURN (one turn per user prompt). `heard` flips True only when the speak
loop confirms the utterance COMPLETED, so an interrupted sentence stays unheard.
Powers within-turn nav (next/prev/first/last) and the persistent cross-turn
transcript (Stage 4); SESSION_END clears it, a new prompt only opens a new turn.
"""
```

Replace `HistoryEntry`:

```python
class HistoryEntry:
    __slots__ = ("text", "kind", "msg_id", "seq", "turn_id", "heard")

    def __init__(self, text: str, kind: str, msg_id: int, seq: int = 0,
                 turn_id: int = 0) -> None:
        self.text = text
        self.kind = kind          # prose|choice|plan|permission
        self.msg_id = msg_id      # message group; bumped by end_message()/start_turn()
        self.seq = seq            # 0-based index within the group; seq 0 == its head
        self.turn_id = turn_id    # turn group; bumped by start_turn() (a new prompt)
        self.heard = False
```

In `SessionHistory.__init__`, add the `_turn_id` map (place it next to `_group_seq`):

```python
        self._group_seq: "dict[str, int]" = {}   # next entry index within the open group
        self._turn_id: "dict[str, int]" = {}     # current turn per session (a new prompt bumps it)
```

In `record`, stamp the turn id (only the `HistoryEntry(...)` line changes):

```python
        entry = HistoryEntry(text, kind, self._msg_id.get(session, 0), seq,
                             self._turn_id.get(session, 0))
```

Add `start_turn` (place it right after `end_message`):

```python
    def start_turn(self, session: str) -> None:
        """Open a new turn (a new user prompt). Subsequent entries belong to the
        new turn, and a fresh message group is started so the new turn never
        continues the prior turn's still-open group. Unlike reset(), the prior
        turn's entries are KEPT — the transcript persists across turns (Stage 4);
        only SESSION_END drops it."""
        self._turn_id[session] = self._turn_id.get(session, 0) + 1
        self._msg_id[session] = self._msg_id.get(session, 0) + 1
        self._group_seq[session] = 0
```

In `reset`, also drop `_turn_id`:

```python
    def reset(self, session: str) -> None:
        """Forget a session entirely (SESSION_END)."""
        self._entries.pop(session, None)
        self._msg_id.pop(session, None)
        self._group_seq.pop(session, None)
        self._turn_id.pop(session, None)
        self._touch.pop(session, None)
```

- [ ] **Step 4: Replace the awkward assertion, then run the tests to verify they pass**

Finalize `test_start_turn_starts_a_fresh_message_group` to its clean form:

```python
def test_start_turn_starts_a_fresh_message_group():
    h = SessionHistory()
    h.record("s", "prose", "a1"); h.end_message("s")
    h.record("s", "prose", "a2")               # an OPEN group in turn 0 (no end_message)
    h.start_turn("s")
    e = h.record("s", "prose", "b1")           # first entry of turn 1
    assert e.seq == 0                           # fresh group, did not continue a2's group
    assert e.turn_id == 1
    assert [x.text for x in h.last_message("s")] == ["b1"]   # its own group
```

Run: `source .venv/bin/activate && python -m pytest tests/test_history.py -v`
Expected: PASS (all existing history tests + the 3 new ones).

- [ ] **Step 5: Run the full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **708 passed, 2 skipped** (705 baseline + 3 new). No regressions — `start_turn` is not yet called by the daemon, so production behavior is unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/sonari/history.py tests/test_history.py
git commit -m "feat(history): add turn_id grouping + start_turn (Stage 4 Task 1)"
```

---

### Task 2: Scope `message_ids` + `unheard` to the current turn

Bound the two read accessors that downstream nav/heard logic uses to the **current turn only**. This is behavior-preserving for single-turn history (every existing test), and it is the §7 seam resolution: `unheard` can never span the whole transcript, and the existing single-level nav can never walk into a prior turn (cross-turn nav is Stage 5).

**Files:**
- Modify: `src/sonari/history.py` (`message_ids`, `unheard`)
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: `_turn_id` and `HistoryEntry.turn_id` from Task 1.
- Produces: `message_ids(session)` and `unheard(session)` now return only current-turn entries. `entries_for_message(session, msg_id)` is **unchanged** (explicit-id lookup, not turn-scoped) — Stage 5's cross-turn replay relies on it reaching any turn's group.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_history.py`:

```python
def test_message_ids_scoped_to_current_turn():
    h = SessionHistory()
    h.record("s", "prose", "t0a"); h.end_message("s")        # turn 0, msg group
    h.record("s", "prose", "t0b"); h.end_message("s")        # turn 0, msg group
    h.start_turn("s")                                        # -> turn 1
    h.record("s", "prose", "t1a")                            # turn 1, msg group
    ids = h.message_ids("s")
    assert len(ids) == 1                                     # only the current (turn 1) group
    # the current-turn id resolves to the turn-1 entry, not a turn-0 one:
    assert [e.text for e in h.entries_for_message("s", ids[0])] == ["t1a"]


def test_entries_for_message_still_reaches_prior_turns():
    # Stage 5 replays past turns via explicit msg_id -> entries_for_message must NOT
    # be turn-scoped (only message_ids/unheard are).
    h = SessionHistory()
    h.record("s", "prose", "old"); h.end_message("s")
    old_id = h.message_ids("s")[0]                           # captured while turn 0 is current
    h.start_turn("s")
    h.record("s", "prose", "new")
    assert [e.text for e in h.entries_for_message("s", old_id)] == ["old"]   # still reachable


def test_unheard_bounded_to_current_turn():
    # §7: unheard stays bounded to the live turn even though the transcript persists.
    h = SessionHistory()
    h.record("s", "prose", "t0")                            # turn 0, never heard
    h.start_turn("s")
    h.record("s", "prose", "t1")                            # turn 1, never heard
    assert [e.text for e in h.unheard("s")] == ["t1"]       # the prior-turn unheard is excluded


def test_empty_current_turn_has_no_nav_or_unheard_but_persists():
    # snap-to-live-edge: after opening a turn with no entries yet, the current-turn
    # views are empty (nothing to navigate / nothing unheard), but the prior turn
    # is retained (persistence).
    h = SessionHistory()
    h.record("s", "prose", "kept")
    h.start_turn("s")                                       # turn 1 is empty
    assert h.message_ids("s") == []
    assert h.unheard("s") == []
    assert [e.text for e in h.last_message("s")] == ["kept"]   # not wiped
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_history.py -k "scoped or bounded or current_turn or reaches_prior" -v`
Expected: FAIL — `message_ids`/`unheard` still return entries from all turns (e.g. `len(ids) == 1` fails with 2; `unheard` returns `["t0", "t1"]`).

- [ ] **Step 3: Scope the two accessors**

In `src/sonari/history.py`, replace `message_ids`:

```python
    def message_ids(self, session: str) -> list:
        """Distinct message ids of the CURRENT turn, oldest first. Each id is one
        'item' (an assistant message / paragraph) within the live turn. History
        persists across turns (Stage 4), so this is bounded to the current turn —
        the existing within-turn nav must not walk into prior turns (cross-turn
        navigation is Stage 5). Powers the next/prev/first/last navigation cursor."""
        d = self._entries.get(session)
        if not d:
            return []
        cur_turn = self._turn_id.get(session, 0)
        ids = []
        seen = set()
        for e in d:
            if e.turn_id != cur_turn:
                continue
            if e.msg_id in seen:
                continue
            seen.add(e.msg_id)
            # The first PRESENT entry of a group. If its seq != 0 the group's head
            # was evicted by the rolling cap, so the group is truncated — exclude it
            # from navigation rather than letting nav replay a fragment (#8).
            if e.seq == 0:
                ids.append(e.msg_id)
        return ids
```

Replace `unheard`:

```python
    def unheard(self, session: str) -> list:
        """Not-yet-heard entries of the CURRENT turn only, oldest first.

        §7 (Stage 4): the transcript persists across turns, but `unheard` stays
        bounded to the live turn. With catch_up/REPEAT retired it has no replay
        consumer; spanning the whole transcript would be unbounded and meaningless.
        Heard-marking still flips entries from the speak loop regardless of turn."""
        cur_turn = self._turn_id.get(session, 0)
        return [e for e in self._entries.get(session, ())
                if e.turn_id == cur_turn and not e.heard]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_history.py -v`
Expected: PASS — all existing tests stay green (they are single-turn, turn_id 0 = current turn) and the 4 new ones pass.

- [ ] **Step 5: Run the full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **712 passed, 2 skipped** (708 + 4 new). No regressions: production still wipes history on FLUSH (Task 3 not done yet), so it stays single-turn and the scoping is inert.

- [ ] **Step 6: Commit**

```bash
git add src/sonari/history.py tests/test_history.py
git commit -m "feat(history): scope message_ids + unheard to the current turn (Stage 4 Task 2)"
```

---

### Task 3: FLUSH opens a turn (persist) instead of resetting; SESSION_END still resets

Flip the lifecycle. The daemon's FLUSH handler stops wiping history and opens a new turn; SESSION_END still wipes. This is the behavior change of the stage. Includes the policy-test rewrite (spec §9), the SESSION_END-clears lock, the multi-turn nav discriminator (the test the existing nav suite is blind to), and the cross-turn cap behavior.

**Files:**
- Modify: `src/sonari/daemon.py` (FLUSH handler, line 429)
- Modify: `tests/test_daemon_phase21.py` (rewrite the flush-resets test; add SESSION_END test)
- Modify: `tests/test_daemon_nav.py` (add multi-turn discriminator tests)
- Modify: `tests/test_history.py` (add cross-turn cap test)

**Interfaces:**
- Consumes: `SessionHistory.start_turn(session)` (Task 1), current-turn scoping of `message_ids`/`unheard` (Task 2), `SessionStream.reset_for_new_prompt()` (sets `nav_cursor=None`, unchanged).
- Produces: no new public interface. After this task: a new prompt keeps the prior turn's transcript; the current turn snaps to the live edge; `SESSION_END` still clears.

- [ ] **Step 1: Write/replace the failing daemon tests**

In `tests/test_daemon_phase21.py`, **replace** `test_user_prompt_flush_resets_history` (lines 67-72) with:

```python
def test_user_prompt_flush_persists_prior_turn_and_snaps_to_live():
    # Stage 4 (was test_user_prompt_flush_resets_history): a new prompt (FLUSH) no
    # longer WIPES history. The prior turn's transcript PERSISTS (navigable later in
    # Stage 5); FLUSH opens a fresh turn, so the current-turn views are empty
    # (snapped to the live edge) but the prior turn is retained.
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    _prose(daemon, "fg", "Old stuff. ")
    daemon.handle_message(_msg(MsgType.FLUSH, "fg"))
    # current turn is empty -> nothing to navigate yet, nothing unheard (live edge)
    assert daemon.history.message_ids("fg") == []
    assert daemon.history.unheard("fg") == []
    # but the prior turn PERSISTS (Stage 3 wiped it here; Stage 4 keeps it)
    assert [e.text for e in daemon.history.last_message("fg")] == ["Old stuff."]
```

Add (next to it) a lock on SESSION_END still clearing:

```python
def test_session_end_still_clears_history():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    _prose(daemon, "fg", "Some text. ")
    daemon.handle_message(_msg(MsgType.SESSION_END, "fg"))
    assert daemon.history.last_message("fg") == []
    assert daemon.history.unheard("fg") == []
    assert daemon.history.message_ids("fg") == []
```

In `tests/test_daemon_nav.py`, add the multi-turn discriminator tests (these use the file's existing `_drain` and `_nav` helpers; FLUSH/prose go via raw dicts as elsewhere in this file):

```python
def test_nav_stays_within_current_turn_after_new_prompt():
    # Stage 4 discriminator (the existing nav suite is blind to this — every other
    # test seeds a single turn). History persists across turns, but the existing
    # within-turn nav must NOT walk into a prior turn; that's Stage 5's two-level nav.
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "T1 alpha.", "index": 0, "final": True})
    daemon.handle_message({"type": "flush", "session": "fg"})        # open turn 2
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "T2 one.", "index": 0, "final": True})
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "T2 two.", "index": 1, "final": True})
    _drain(queue)                                                    # clear live playback
    _nav(daemon, "first")                                           # first of CURRENT turn
    texts = [s.text for s in _drain(queue)]
    assert texts == ["T2 one.", "T2 two."]                          # whole current turn
    assert "T1 alpha." not in texts                                 # never the prior turn


def test_nav_prev_clamps_at_current_turn_start_not_prior_turn():
    # After a new prompt with a single message in the fresh turn, 'prev' clamps on
    # that message and never reaches into the prior turn's transcript.
    daemon, queue, *_ = make_daemon(foreground="fg")
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Prior turn.", "index": 0, "final": True})
    daemon.handle_message({"type": "flush", "session": "fg"})        # open new turn
    daemon.handle_message({"type": "prose", "session": "fg",
                           "delta": "Current turn.", "index": 0, "final": True})
    _drain(queue)
    for _ in range(3):
        _nav(daemon, "prev")                                        # clamps, no wrap/leak
    texts = [s.text for s in _drain(queue)]
    assert texts == ["Current turn."]
    assert "Prior turn." not in texts
```

In `tests/test_history.py`, add the cross-turn cap test:

```python
def test_cap_spans_whole_session_evicting_oldest_turns():
    # Stage 4: the rolling cap now bounds the WHOLE-session transcript (it was
    # effectively per-turn when history reset each prompt). Oldest entries (oldest
    # turns) evict first; the current turn stays intact and navigable.
    h = SessionHistory(cap=3)
    h.start_turn("s")                                  # turn 1
    h.record("s", "prose", "t1"); h.end_message("s")
    h.start_turn("s")                                  # turn 2
    h.record("s", "prose", "t2a"); h.end_message("s")
    h.record("s", "prose", "t2b"); h.end_message("s")
    h.record("s", "prose", "t2c")                      # 4th entry -> evicts "t1"
    ids = h.message_ids("s")
    assert len(ids) == 3                               # current turn (t2a/t2b/t2c) intact
    # the evicted oldest turn is gone from the transcript entirely
    assert all(e.text != "t1" for e in h._entries["s"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_phase21.py::test_user_prompt_flush_persists_prior_turn_and_snaps_to_live tests/test_daemon_nav.py::test_nav_stays_within_current_turn_after_new_prompt -v`
Expected: FAIL — FLUSH still calls `history.reset`, so `last_message` is `[]` after FLUSH (persistence assertion fails) and the nav discriminator returns `[]` / "Nothing to navigate yet." instead of the current turn. (`test_cap_spans_whole_session_evicting_oldest_turns` may already pass — it exercises `start_turn` directly, which exists from Task 1; that is fine, it is a lock on the new cap meaning.)

- [ ] **Step 3: Flip the FLUSH lifecycle**

In `src/sonari/daemon.py`, in the `MsgType.FLUSH` handler, change line 429 from:

```python
            st.reset_for_new_prompt()
            self.history.reset(session)
```

to:

```python
            st.reset_for_new_prompt()
            # Stage 4: a new prompt opens a NEW TURN and KEEPS the prior turn's
            # transcript (persistent, navigable in Stage 5). reset_for_new_prompt()
            # already cleared live playback (queue, assembler, nav_cursor -> snap to
            # live edge); history is no longer wiped here. SESSION_END still clears it.
            self.history.start_turn(session)
```

Leave the SESSION_END handler (`self.history.reset(session)` at line 447) **unchanged**.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_phase21.py tests/test_daemon_nav.py tests/test_history.py -v`
Expected: PASS — the rewritten flush test, the SESSION_END test, both nav discriminators, the cap test, and all pre-existing tests in these files.

- [ ] **Step 5: Run the full suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: **716 passed, 2 skipped** (712 after Task 2, minus 1 removed test `test_user_prompt_flush_resets_history`, plus 5 new: persists-and-snaps, session-end-clears, 2 nav discriminators, cap-spans-session). Net +4 over Task 2 → 716. Confirm zero failures.

> If the count differs, reconcile before committing: a removed/renamed test changes the arithmetic but the suite must be green with no unexpected failures. The binding requirement is **green**, not the exact integer.

- [ ] **Step 6: Commit**

```bash
git add src/sonari/daemon.py tests/test_daemon_phase21.py tests/test_daemon_nav.py tests/test_history.py
git commit -m "feat(daemon): persist transcript across turns — FLUSH opens a turn, SESSION_END still clears (Stage 4 Task 3)"
```

---

## Self-Review

**1. Spec coverage** (`design.md` §5/§7/§8.3 item 4):
- "History no longer wiped on FLUSH; SESSION_END still clears" → Task 3 (FLUSH→`start_turn`, SESSION_END→`reset`). ✓
- "A new prompt resets live playback but keeps the transcript" → Task 3 relies on `reset_for_new_prompt()` (live) + `start_turn()` (keeps transcript). ✓
- "Turn grouping — each user prompt opens a new turn; within a turn, message groups work as today" → Task 1 (`turn_id` + `start_turn`). ✓
- "Snap the nav cursor to the live edge on a new prompt" → falls out of `nav_cursor=None` (unchanged) + current-turn `message_ids` (Task 2); locked by `test_empty_current_turn_has_no_nav_or_unheard_but_persists` and `test_user_prompt_flush_persists_prior_turn_and_snaps_to_live`. ✓
- "Capped, in-memory; oldest turns drop past the cap" → `deque(maxlen=cap)` unchanged; cross-turn eviction locked by `test_cap_spans_whole_session_evicting_oldest_turns`. ✓
- §7 "`unheard` must stay bounded to recent backlog, never the whole transcript" → Task 2 scopes `unheard` to the current turn; `test_unheard_bounded_to_current_turn`. ✓
- §7 "`message_ids` must group by turn" → Task 1 `turn_id`; Task 2 scopes `message_ids` to the current turn; `entries_for_message` stays cross-turn for Stage 5 (`test_entries_for_message_still_reaches_prior_turns`). ✓
- **Deliberately deferred to Stage 5** (NOT in this plan): `nav_prev_response`/`nav_next_response`, cross-turn replay, a public `turn_ids()` accessor (YAGNI — no consumer until Stage 5). The data-model foundation (the `turn_id` field) is laid here. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code step shows complete code. The one awkward assertion in Task 1 Step 1 is explicitly finalized in Step 4. ✓

**3. Type/name consistency:** `start_turn`, `turn_id`, `_turn_id` used identically across Tasks 1–3. `message_ids`/`unheard`/`entries_for_message`/`reset`/`record`/`last_message`/`end_message` match `history.py`. `_msg`/`_prose`/`_drain`/`_nav`/`make_daemon`/`stream_queue` match the verified test helpers. `MsgType.FLUSH`/`SESSION_END`/`PROSE`/`NAV` match `protocol.py`. ✓

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-19-sonari-session-streams-stage4.md`.

**Recommended execution:** superpowers:subagent-driven-development on a branch off `main` (`feat/session-streams-stage4`), sequential (Tasks 3←2←1 share `history.py`/`daemon.py` and have ordering deps — no parallel implementers). Per-task: implement → adversarial task-review → bounded fix; opus whole-branch review at the end. Suite green at every step. Do not push unless Nima asks.
