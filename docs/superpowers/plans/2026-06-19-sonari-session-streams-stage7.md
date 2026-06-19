# Stage 7 — Backlog bounds, caps, cleanup, dead-code Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the per-session-streams campaign: bound the per-stream speech backlog (the spec's promised cap), fix the deferred Stage-5 response-nav edge, and remove enumerated dead code.

**Architecture:** Three sequential tasks over shared files (`history.py`, `daemon.py`, `queue.py`, `session_stream.py`, `config.py`). Verification/cleanup-heavy; only two intended behavior changes (backlog eviction; nav edge-case anchor), both spec-approved. The voice/threading model is unchanged.

**Tech Stack:** Python 3.9 (floor), stdlib-only core, pytest.

## Global Constraints

- **Python 3.9 floor; stdlib-only core.** No new dependencies.
- **Full suite green at every step:** `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py` — baseline **739 passed, 2 skipped** (the ignore + 2 skips need the `[kokoro]`/numpy extra, absent in `.venv`; pre-existing).
- **Behavior-preserving** EXCEPT the two intended changes: (a) the per-stream `SpeechQueue` now evicts the oldest **non-decision** item at the cap; (b) `_nav_response` pins the anchor to the newest-content turn (instead of `None`) when the live turn is empty.
- **Decisions (locked, from the spec §11 + the user):** `backlog_cap = 200` (config default, matches `history_cap`, tune by feel); **decision items (`is_decision`: plan/choice/permission) are EXEMPT from eviction** — never silently dropped.
- **Concurrency convention (preserve):** read stream state via `self._streams.get(...)` (absent-safe), mutate via `_stream()` under `self._lock`; the speak loop plays `foreground().queue`.
- **Do NOT touch** `docs/getting-started.md` or `.convergence-plan.md` (pre-existing untracked, not ours).
- **TDD:** for the two behavior changes, write the failing test first; the pure deletions are guarded by the existing suite.

---

### Task 1: Cleanup — dead `_touch`/`_tick`, vestigial `_seed` param, dead-remnant sweep

**Files:**
- Modify: `src/sonari/history.py` (remove `_touch`/`_tick`)
- Modify: `tests/test_daemon_control.py` (remove the unused `_seed` first arg + update callers)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new (pure removal). `SessionHistory.current_turn` is added in Task 2, not here.

- [ ] **Step 1: Confirm `_touch`/`_tick` are dead, then remove them.**

`grep -n "_touch\|_tick" src/sonari/history.py` must show ONLY: init (`self._touch ... ; self._tick = 0`), the write in `record` (`self._tick += 1; self._touch[session] = self._tick`), and the pop in `reset` (`self._touch.pop(session, None)`). No reader of the dict's *value* exists (the "recency across sessions" map was never consumed). Remove all four sites:
- In `__init__`: delete the two lines `self._touch: "dict[str, int]" = {}   # recency across sessions` and `self._tick = 0`.
- In `record`: delete the two lines `self._tick += 1` and `self._touch[session] = self._tick`.
- In `reset`: delete the line `self._touch.pop(session, None)`.

- [ ] **Step 2: Run the suite to confirm the deletion is behavior-neutral**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: 739 passed, 2 skipped.

- [ ] **Step 3: Remove the vestigial `queue` first arg from the `_seed` helper.**

In `tests/test_daemon_control.py`, the helper `def _seed(queue, daemon, session, n, decision_at=None):` never uses `queue` (its body enqueues into `daemon._stream(session).queue`). Change the signature to:

```python
def _seed(daemon, session, n, decision_at=None):
```

Then update EVERY caller to drop the first argument. The callers (verify by grep `_seed(` in the file) are, e.g.:
- `_seed(queue, daemon, "fg", 2)` → `_seed(daemon, "fg", 2)`
- `_seed(queue, daemon, "other", 1)` → `_seed(daemon, "other", 1)`
- `_seed(stream_queue(daemon, "b"), daemon, "b", 2)` → `_seed(daemon, "b", 2)`
- `_seed(queue, daemon, "a", 2)` → `_seed(daemon, "a", 2)`
- `_seed(queue, daemon, "fg", 3)` (two occurrences) → `_seed(daemon, "fg", 3)`
- `_seed(queue, daemon, "fg", 4, decision_at=2)` → `_seed(daemon, "fg", 4, decision_at=2)`

If a caller's only use of `queue`/`stream_queue(...)` was the dropped argument, that local becomes unused — leave other uses intact; only drop the argument.

- [ ] **Step 4: Bounded dead-remnant sweep (timeboxed — do not balloon).**

Grep ONCE for leftover remnants of the retired voice-owner lattice and other unused fields. Run: `grep -rn "_voice_owner\|_captured_msg\|_owner_open\|_owner_mid_reply\|_claim_for_decision\|catch_up\|other_session_with_unheard" src/sonari/`. Expected: NO hits in `src/` (all retired in Stages 2–3). If a hit appears in `src/`, remove it only if it is provably unreferenced (grep the symbol repo-wide); otherwise leave it and NOTE it in the task report. Do not expand beyond the three named vestiges plus anything this grep proves dead. The keycode `'j'=38` item is ALREADY covered by `tests/test_macos_hotkeys.py::test_display_tables_cover_every_keycode_and_modifier` (it iterates every `KEY_CODES.values()`); add no new assertion — note it satisfied.

- [ ] **Step 5: Run the suite**

Run: `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: 739 passed, 2 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/sonari/history.py tests/test_daemon_control.py
git commit -m "refactor: remove dead _touch/_tick + vestigial _seed param (Stage 7 cleanup)"
```

---

### Task 2: Nav Minor fix — `current_turn()` accessor + `_nav_response` live-anchor logic

**Files:**
- Modify: `src/sonari/history.py` (add `current_turn`)
- Modify: `src/sonari/daemon.py` (`_nav_response`, ~lines 792–824)
- Test: `tests/test_daemon_nav.py`

**Interfaces:**
- Consumes: `SessionHistory.turn_ids(session)`, `message_ids_in_turn(session, turn_id)` (existing).
- Produces: `SessionHistory.current_turn(session: str) -> int` — the session's current (live) turn id, `0` if the session is unknown. Used by `_nav_response` to distinguish "newest navigable turn" from "the actual live turn".

**Background (why):** `turn_ids()` excludes a turn with no present message-group head, so in the FLUSH→first-prose window the empty live turn is NOT in `turn_ids`. The old `is_live = (new_idx == len(turns) - 1)` then treats the newest *navigable* turn as live and sets `nav_turn = None` (== follow live), pointing the anchor at the empty live turn — so a follow-up within-nav says "Nothing to navigate yet." Fix: only set `nav_turn = None` when the target IS the actual live turn; otherwise pin to the newest navigable turn (verified safe — streaming still plays while parked, see `test_live_prose_while_parked_on_past_response_enqueues_after_replay`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_daemon_nav.py` (it already has `make_daemon`, `_drain`, `_responses`):

```python
def test_back_to_latest_with_empty_live_turn_pins_anchor_not_none():
    # Deferred Stage-5 Minor: a FLUSH after the last prose opens an EMPTY live turn
    # (excluded from turn_ids). Navigating back to the latest must pin the anchor to the
    # newest CONTENT turn (not None == the empty live turn), so a follow-up within-nav
    # still works instead of saying "Nothing to navigate yet."
    daemon, queue, *_ = make_daemon(foreground="fg")
    _responses(daemon, "fg", ["R1.", "R2."])         # turns with content; live turn has R2.
    _drain(queue)
    daemon.handle_message({"type": "flush", "session": "fg"})   # opens an EMPTY live turn
    daemon.handle_message({"type": "nav", "to": "prev_response", "session": "fg"})  # park back
    _drain(queue)
    daemon.handle_message({"type": "nav", "to": "next_response", "session": "fg"})  # to latest
    cues = [s.text for s in _drain(queue)]
    assert "Back to the latest." in cues                         # cue unchanged
    st = daemon._stream("fg")
    assert st.nav_turn is not None                               # PINNED, not the empty live turn
    assert st.nav_turn in daemon.history.turn_ids("fg")          # a real navigable turn
    # within-nav over the pinned turn works (no dead-end cue)
    daemon.handle_message({"type": "nav", "to": "prev", "session": "fg"})
    assert "Nothing to navigate yet." not in [s.text for s in _drain(queue)]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_nav.py::test_back_to_latest_with_empty_live_turn_pins_anchor_not_none -v`
Expected: FAIL — the final assertion sees "Nothing to navigate yet." (old behavior anchors to None at the empty live turn).

- [ ] **Step 3: Add the `current_turn` accessor**

In `src/sonari/history.py`, add (near `turn_ids`):

```python
    def current_turn(self, session: str) -> int:
        """The session's current (live) turn id — the turn a new entry would join.
        Unlike the last id in `turn_ids()`, this is the LIVE turn even when it has no
        entries yet (the FLUSH->first-prose window), so callers can tell 'newest
        navigable turn' apart from 'the live turn'. Defaults to 0 for an unknown session."""
        return self._turn_id.get(session, 0)
```

- [ ] **Step 4: Fix `_nav_response`**

In `src/sonari/daemon.py`, replace the `is_live`/cue/cursor block of `_nav_response` (the lines from `is_live = (new_idx == len(turns) - 1)` through `st.nav_cursor = None if is_live else (mids[0] if mids else None)`) with:

```python
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
```

- [ ] **Step 5: Run the new test + the full nav file + the suite**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_nav.py -v && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: the new test PASSES; `test_next_response_returns_to_latest_with_boundary_cue` (live turn has content → `target == current_turn` → `nav_turn is None`) still PASSES; suite 740 passed, 2 skipped.

- [ ] **Step 6: Commit**

```bash
git add src/sonari/history.py src/sonari/daemon.py tests/test_daemon_nav.py
git commit -m "fix(daemon): pin response-nav anchor to newest-content turn when live turn empty (Stage 7; closes deferred Stage-5 Minor)"
```

---

### Task 3: Backlog cap — bound the per-stream `SpeechQueue`

**Files:**
- Modify: `src/sonari/queue.py` (`SpeechQueue.__init__` cap; `enqueue` evicts+returns)
- Modify: `src/sonari/session_stream.py` (`__init__` accepts `queue_cap`)
- Modify: `src/sonari/daemon.py` (`__init__` reads `backlog_cap`; `_stream` injects it; `_enqueue` drops the evicted item's pending-heard)
- Modify: `src/sonari/config.py` (add `backlog_cap` default)
- Test: `tests/test_queue.py` (create if absent), `tests/test_daemon_streams.py`

**Interfaces:**
- Consumes: `SpeechItem.is_decision` (existing field), `self._drop_pending(items)` (existing daemon helper).
- Produces:
  - `SpeechQueue(cap: "int | None" = None)`; `enqueue(item) -> "SpeechItem | None"` returns the evicted item (or `None`).
  - `SessionStream(queue_cap: "int | None" = None)` — forwards to `SpeechQueue(cap=queue_cap)`.
  - `DEFAULTS["backlog_cap"] = 200`.

- [ ] **Step 1: Write the failing tests (queue-level)**

Create `tests/test_queue.py` (or append if it exists):

```python
from sonari.queue import SpeechQueue
from sonari.queue import SpeechItem


def _item(i, decision=False):
    return SpeechItem(id=i, session="s", kind="plan" if decision else "prose",
                      text="t{0}".format(i), is_decision=decision)


def test_enqueue_unbounded_when_no_cap():
    q = SpeechQueue()
    assert all(q.enqueue(_item(i)) is None for i in range(50))
    assert len(q) == 50


def test_enqueue_evicts_and_returns_oldest_prose_at_cap():
    q = SpeechQueue(cap=3)
    assert q.enqueue(_item(0)) is None
    assert q.enqueue(_item(1)) is None
    assert q.enqueue(_item(2)) is None
    evicted = q.enqueue(_item(3))            # full -> evict oldest (id 0)
    assert evicted is not None and evicted.id == 0
    assert len(q) == 3
    assert [it.id for it in (q.pop_next(), q.pop_next(), q.pop_next())] == [1, 2, 3]


def test_enqueue_exempts_decisions_evicting_oldest_prose_instead():
    q = SpeechQueue(cap=3)
    q.enqueue(_item(0, decision=True))       # a waiting decision, oldest
    q.enqueue(_item(1))                       # prose
    q.enqueue(_item(2))                       # prose
    evicted = q.enqueue(_item(3))            # full -> skip the decision, evict oldest prose (id 1)
    assert evicted is not None and evicted.id == 1
    assert [it.id for it in list(q._items)] == [0, 2, 3]   # decision retained at head


def test_enqueue_all_decisions_exceeds_cap_rather_than_drop():
    q = SpeechQueue(cap=2)
    assert q.enqueue(_item(0, decision=True)) is None
    assert q.enqueue(_item(1, decision=True)) is None
    assert q.enqueue(_item(2, decision=True)) is None       # nothing evictable -> exceed cap
    assert len(q) == 3


def test_enqueue_front_is_not_subject_to_cap():
    q = SpeechQueue(cap=2)
    q.enqueue(_item(1)); q.enqueue(_item(2))
    q.enqueue_front(_item(0))                 # resume-requeue: re-inserts a just-popped item
    assert [it.id for it in list(q._items)] == [0, 1, 2]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_queue.py -v`
Expected: FAIL — `SpeechQueue()` takes no `cap`; `enqueue` returns `None` always.

- [ ] **Step 3: Implement the cap in `SpeechQueue`**

In `src/sonari/queue.py`, change `__init__` and `enqueue`:

```python
    def __init__(self, cap: "int | None" = None) -> None:
        self._items: "deque[SpeechItem]" = deque()
        self._cap = cap                      # None == unbounded backlog

    def enqueue(self, item: SpeechItem) -> "SpeechItem | None":
        """Append *item*. When a cap is set and the queue is full, evict and RETURN the
        oldest NON-decision item so the caller can drop its pending-heard marker (a
        backgrounded stream must stay memory-bounded). Decision items (plan/choice/
        permission) are EXEMPT — never silently dropped, so a waiting prompt is preserved;
        if every queued item is a decision, the queue is allowed to exceed the cap rather
        than drop one. Returns the evicted item, or None."""
        evicted = None
        if self._cap is not None and len(self._items) >= self._cap:
            for i, it in enumerate(self._items):
                if not it.is_decision:
                    evicted = it
                    del self._items[i]
                    break
            # all-decisions: nothing evictable; the queue temporarily exceeds the cap
        self._items.append(item)
        return evicted
```

Leave `enqueue_front` unchanged but document it is cap-exempt — add to its docstring: `Not subject to the cap: it re-inserts an item just popped from this queue.`

- [ ] **Step 4: Run the queue tests**

Run: `source .venv/bin/activate && python -m pytest tests/test_queue.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing daemon-level test (eviction cleans pending-heard)**

Append to `tests/test_daemon_streams.py` (uses `make_daemon`):

```python
def test_backlog_cap_evicts_oldest_prose_and_drops_its_pending_heard():
    # A capped background stream must drop the evicted item's _pending_heard entry,
    # else the cap bounds the queue but leaks the pending dict (defeating the bound).
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon._backlog_cap = 2
    daemon._streams.clear()                  # rebuild streams under the small cap
    entries = [daemon.history.record("bg", "prose", "p{0}".format(i)) for i in range(3)]
    for i, e in enumerate(entries):
        daemon._enqueue("bg", "prose", "p{0}".format(i), False, entry=e)
    bg = daemon._stream("bg").queue
    assert len(bg) == 2                                   # capped
    assert entries[0].id not in [it.id for it in list(bg._items)]   # oldest evicted
    assert entries[0].id not in daemon._pending_heard     # its pending entry dropped (no leak)
    assert entries[1].id in daemon._pending_heard         # survivors retained
```

Note: `_enqueue` registers `_pending_heard[item.id] = entry` keyed by the SpeechItem id; `HistoryEntry` exposes no `.id`, so the test compares item ids via the queue and the pending dict. Adjust the assertions to key off the SpeechItem ids actually enqueued if the helper returns them — keep the intent: oldest item's pending entry is gone, survivors' remain.

- [ ] **Step 6: Run it to verify it fails**

Run: `source .venv/bin/activate && python -m pytest "tests/test_daemon_streams.py::test_backlog_cap_evicts_oldest_prose_and_drops_its_pending_heard" -v`
Expected: FAIL — streams are unbounded (no `_backlog_cap`; `SessionStream`/`SpeechQueue` take no cap).

- [ ] **Step 7: Wire the cap through SessionStream, daemon, and config**

`src/sonari/session_stream.py` — change `__init__`:

```python
    def __init__(self, queue_cap: "int | None" = None) -> None:
        self.queue = SpeechQueue(cap=queue_cap)   # this session's own pending-speech queue
```
(Leave the rest of `__init__` and `reset_for_new_prompt` unchanged — `reset` must NOT recreate the queue.)

`src/sonari/config.py` — add to `DEFAULTS`, next to `history_cap`:
```python
    "backlog_cap": 200,
```

`src/sonari/daemon.py` `__init__` — after the `self.history = SessionHistory(...)` line, add:
```python
        self._backlog_cap = int(config.get("backlog_cap", 200))
```

`src/sonari/daemon.py` `_stream` — change the construction:
```python
            s = SessionStream(queue_cap=self._backlog_cap)
```

`src/sonari/daemon.py` `_enqueue` — change the non-front branch to capture and drop the evicted item:
```python
        if at_front:
            st.queue.enqueue_front(item)
        else:
            evicted = st.queue.enqueue(item)
            if evicted is not None:
                self._drop_pending([evicted])
```

- [ ] **Step 8: Run the daemon test, the streams file, and the full suite**

Run: `source .venv/bin/activate && python -m pytest tests/test_daemon_streams.py tests/test_queue.py -v && python -m pytest -q --ignore=tests/test_kokoro.py`
Expected: new tests PASS; suite green (≈ 746 passed, 2 skipped — exact count depends on test additions). If any existing test that enqueues > 200 items to a single stream now sees eviction, that is the intended cap; update such a test with the why documented (none expected — existing tests enqueue far fewer).

- [ ] **Step 9: Commit**

```bash
git add src/sonari/queue.py src/sonari/session_stream.py src/sonari/daemon.py src/sonari/config.py tests/test_queue.py tests/test_daemon_streams.py
git commit -m "feat(queue): cap per-stream backlog at 200, evict oldest prose (decisions exempt) (Stage 7)"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** §8.3 item 7 (backlog bounds/caps/cleanup/dead-code) → Tasks 1+3; deferred Stage-5 nav Minor → Task 2; §11 resolved cap decisions → Task 3. The `'j'=38` cosmetic → already covered (Task 1 Step 4 notes it). All Stage-7 items mapped.
- **Type consistency:** `enqueue -> "SpeechItem | None"`, `current_turn -> int`, `SessionStream(queue_cap=...)`, `SpeechQueue(cap=...)`, `DEFAULTS["backlog_cap"]` used consistently across tasks.
- **No placeholders:** every code/test step shows the actual code.
- **Mutation-verify in review:** Task 2 test fails on old anchor logic (Step 2); Task 3's daemon test fails if the `_drop_pending([evicted])` call is removed (the leak guard) — the reviewer must confirm this non-vacuity.
