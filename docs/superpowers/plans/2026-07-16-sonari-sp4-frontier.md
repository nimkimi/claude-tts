# Sonari SP4 — Transcript Frontier + Forward-Read Primitive + Tool Fidelity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every session a monotonic "furthest-dealt-with" frontier (distinct from the browse cursor), a cross-turn forward-from-frontier read primitive, tool-use transcript capture at every verbosity, and the deliberate pile-skip gesture — the data-model layer SP5's catch-up action will consume.

**Architecture:** The frontier is a stored per-session scalar `(msg_id, seq)` on `SessionStream` (sibling to `nav_cursor`/`nav_turn`), advanced O(1) inside the existing speak-loop lock at exactly two write paths — a `forward`-provenance item completing in `note_spoken`, and the deliberate pile-skip gesture — never derived from the scattered `heard` flags (that derivation is the B1 defect). A new `SpeechItem.forward` flag discriminates forward-readout enqueues from browse-replay enqueues so a review gesture never drags the frontier. The forward-from-frontier read is a new frontier-keyed sibling accessor on `SessionHistory` that spans the open session lifetime keyed on the stable `(msg_id, seq)`; `history.unheard()` stays turn-scoped for its shipped ⌃⌘W consumers. Tool captures record a `tool` `HistoryEntry` at every verbosity. `⌃⌘S`-start becomes a quiet resume that drops the pre-start queue while the pile persists in history behind the frozen frontier.

**Tech Stack:** Python 3, pytest, the existing Sonari daemon (`say`/`afplay`/`sonari-hotkeyd`). No new runtime deps. macOS-only.

## Global Constraints

Every task's requirements implicitly include this section.

**Campaign-wide (from `docs/superpowers/plans/2026-06-29-sonari-voice-arbitration-campaign.md:11-18`):**
- **Keep the machinery, rebuild only the decision layer.** Do NOT touch the mechanism of: per-session streams (`session_stream.py`), `SpeechQueue` (`queue.py`), `ProseAssembler`, the speak-loop pop+claim+speak+note_spoken core and cancel-epoch/barge-in (`host.py`/`speaker.py`), `SessionHistory` **storage** (extend, never replace), the dispatch/registry/server/Ctx glue.
- **macOS-only.** Python 3 / `say` / `afplay` / the `sonari-hotkeyd` Swift binary. No new runtime deps.
- **Decisions ratified 2026-06-29 (binding):** verbosity = **global**; catch-up chord = co-designed with Nima at SP5.
- **Deploy is Nima's step** (`./bin/sonari install` from a real GUI Terminal). Live audio feel is his ears — never use him as a mechanical-repro harness.

**SP4-specific (from the controller brief + `.superpowers/sdd/sp4-recon-synthesis.md`):**
- **(a) Agent-neutrality at the adapter boundary.** No Claude-Code payload shapes past `hooks_entry` into protocol / history / core. Any new field crossing the socket must be tool-agnostic (the tool `HistoryEntry` stores an already-computed generic `summary` string, not a CC-specific shape).
- **(b) Spoken-grammar principles** (every NEW spoken string obeys ALL; source: `docs/superpowers/specs/2026-07-16-sonari-whereami-grammar-v2.md`): sentence boundaries only (never semicolons); a role word adjacent to every number; dial digits number-before-name; secondary magnitudes as words; value-tier ordering (barge-in keeps the valuable prefix); never a standalone clippable landmark for a high-stakes fact.
- **(c) The 4 permanent concurrency-guard tests in `tests/test_concurrency_guards.py` stay green at EVERY commit, assertions never weakened.** SP4's frontier surface ADDS its own hammers (frontier monotonicity under contention; provenance-gated advance — a browse replay flips `heard` yet the frontier stays; a forward readout advances it).
- **(d) TDD per task; conventional commits; suite green at every commit.**
- **Frontier invariants:** monotonic (only advances, never retreats); O(1) compare on the speak-loop path (never an O(n) `history` scan inside `self._lock`); advancing it never writes `_foreground`/workspace and never touches MRU; **new arrival never advances it**; its unit is the **whole item** (advances only on full completion, never mid-item); stored as a plain JSON-shaped `(int, int)` tuple or `None` so SP6 can serialize it unchanged.
- **SP4 does NOT ship:** the catch-up action / `MsgType` / handler / chord (SP5); any serialization or persistence (SP6); the spoken medium tool-summary rendering (§15 Pass-2).

**Baseline:** `main @ 5731abe`, `1073 passed, 1 skipped` via `.venv/bin/python -m pytest -q`.

---

### Task 1: `SpeechItem.forward` provenance flag (foundation, no behavior yet)

Adds a forward-vs-browse discriminator to `SpeechItem` and threads it through `_enqueue`. Default `False` (opt-in): a forgotten `forward=True` on a readout path makes the frontier *lag* (catch-up harmlessly re-reads); a forgotten `forward=False` on a browse path makes the frontier *advance on review* (the B1 bug). Also preserves the flag at the **four** barge-in re-queue sites — three reconstruct the item via `_enqueue(...)` (must pass `forward=`), one re-inserts the same object (already safe). No frontier exists yet, so nothing *reads* `forward` in this task; it just round-trips.

**Files:**
- Modify: `src/sonari/queue.py:8-17` (add `forward` field to `SpeechItem`)
- Modify: `src/sonari/daemon/host.py:231-258` (`_enqueue`: add `forward` param, pass to `SpeechItem`)
- Modify: `src/sonari/daemon/features/control.py:348-351` (⌃⌘W re-queue: `forward=cur.forward`)
- Modify: `src/sonari/daemon/features/playback.py:191-194` (⌃⌘R re-queue: `forward=cur.forward`)
- Modify: `src/sonari/daemon/features/chooser.py:118-121` (chooser restore re-queue: `forward=c.forward`)
- Test: `tests/test_frontier.py` (new)

**Interfaces:**
- Produces: `SpeechItem.forward: bool = False` (dataclass field); `SpeechDaemon._enqueue(..., forward: bool = False) -> int`. `host.py:594`'s L2 re-queue uses `enqueue_front(item)` (same object) — inherits `forward` for free, do NOT change it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_frontier.py
from sonari.queue import SpeechItem
from sonari.sessions import SessionManager
from sonari.daemon import SpeechDaemon
from sonari.protocol import MsgType, PROTOCOL_VERSION
from sonari.config import DEFAULTS


def _cfg():
    c = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    c["verbosity"] = "everything"
    return c


class _FakeSpeaker:
    def __init__(self): self._epoch = 0
    def cancel(self): self._epoch += 1
    def cancel_epoch(self): return self._epoch
    def earcon(self, kind): pass


def test_speech_item_forward_defaults_false():
    it = SpeechItem(id=1, session="s", kind="prose", text="x", is_decision=False)
    assert it.forward is False


def test_enqueue_threads_forward_flag():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    d._enqueue("s0", "prose", "a", False, forward=True)
    d._enqueue("s0", "prose", "b", False)
    items = list(d._stream("s0").queue._items)
    assert items[0].forward is True and items[1].forward is False


def test_whereami_requeue_preserves_forward():
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    it = SpeechItem(id=99, session="s0", kind="prose", text="live",
                    is_decision=False, forward=True)
    d._current_item = it                       # in-flight forward readout
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.WHERE_AM_I,
                          "session": "s0"})
    requeued = [x for x in d._stream("s0").queue._items if x.text == "live"]
    assert requeued and requeued[0].forward is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_frontier.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'forward'` (and the ⌃⌘W re-queue drops the flag).

- [ ] **Step 3: Add the `forward` field to `SpeechItem`** (`src/sonari/queue.py`, after the `audio_path` field, line 17)

```python
    audio_path: "str | None" = None  # when set, the speak loop afplays this file (spearcon) instead of say
    forward: bool = False  # SP4 provenance: True only at forward-readout enqueue sites (prose/decision/
                           # tool-announce readout). Browse-replay + control cues stay False so a review
                           # gesture never advances the frontier (B1). Read only by note_spoken's advance.
```

- [ ] **Step 4: Thread `forward` through `_enqueue`** (`src/sonari/daemon/host.py`)

In the signature (line 231-234) add the parameter after `audio_path`:

```python
    def _enqueue(self, session: str, kind: str, text: str, is_decision: bool,
                 entry=None, mute_exempt: bool = False,
                 pause_exempt: bool = False, at_front: bool = False,
                 names_session: bool = False, audio_path=None,
                 forward: bool = False) -> int:
```

In the `SpeechItem(...)` construction (line 237-247) add `forward=forward,` after `audio_path=audio_path,`.

- [ ] **Step 5: Preserve `forward` at the three reconstruction re-queue sites**

`src/sonari/daemon/features/control.py:348-351` (⌃⌘W) — add `forward=cur.forward,`:

```python
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, mute_exempt=cur.mute_exempt,
                      pause_exempt=cur.pause_exempt, names_session=cur.names_session,
                      audio_path=cur.audio_path, forward=cur.forward, at_front=True)
```

`src/sonari/daemon/features/playback.py:191-194` (⌃⌘R) — add `forward=cur.forward,` to the same call.

`src/sonari/daemon/features/chooser.py:118-121` (chooser restore) — add `forward=c.forward,` to the `host._enqueue(c.session, ...)` call.

- [ ] **Step 6: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_frontier.py -q`
Expected: PASS (3 tests).

- [ ] **Step 7: Full suite green, then commit**

Run: `.venv/bin/python -m pytest -q` → Expected: `1076 passed, 1 skipped` (+3).

```bash
git add src/sonari/queue.py src/sonari/daemon/host.py \
  src/sonari/daemon/features/control.py src/sonari/daemon/features/playback.py \
  src/sonari/daemon/features/chooser.py tests/test_frontier.py
git commit -m "feat(sp4): add SpeechItem.forward provenance flag, preserved across barge-in re-queues"
```

---

### Task 2: `SessionStream.frontier` + provenance-gated advance in `note_spoken`

The monotonic frontier scalar and its ONLY two write paths. This task wires write-path (a): a `forward=True` item completing in `note_spoken` advances `st.frontier = max(frontier, (entry.msg_id, entry.seq))`, gated on `completed`. Browse replays (`forward=False`) still flip `heard` for their other uses but leave the frontier put — the B1 fix. The advance is O(1) (the key is already on the entry) and stays inside `note_spoken`'s existing lock (M1). Sets `forward=True` at the forward-readout enqueue sites so real readouts actually advance it. `⌃⌘D`'s cancel → `completed=False` → no advance falls out for free (Q7).

**Files:**
- Modify: `src/sonari/session_stream.py` (add `self.frontier = None` in `__init__`; add `advance_frontier`; do NOT add frontier to `reset_for_new_prompt`)
- Modify: `src/sonari/daemon/host.py:320-331` (`note_spoken`: advance frontier)
- Modify: `src/sonari/daemon/host.py:275-284` (`_flush_prose_buffer`: `forward=True`)
- Modify: `src/sonari/daemon/features/decisions.py:104,121,138,172` (choice/plan/permission/permission_request readout enqueues: `forward=True`)
- Test: `tests/test_frontier.py` (append)

**Interfaces:**
- Consumes: `SpeechItem.forward` (Task 1); `HistoryEntry.msg_id`/`.seq` (`history.py:16`).
- Produces: `SessionStream.frontier: "tuple[int,int] | None"` (None == nothing dealt-with); `SessionStream.advance_frontier(key) -> None` (monotonic, None-safe). Later tasks (skip, T6) call `advance_frontier`; the SP5 read primitive (T3) keys off `.frontier`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_frontier.py`)

```python
def test_advance_frontier_monotonic():
    from sonari.session_stream import SessionStream
    st = SessionStream()
    assert st.frontier is None
    st.advance_frontier((1, 0)); assert st.frontier == (1, 0)
    st.advance_frontier((2, 3)); assert st.frontier == (2, 3)
    st.advance_frontier((1, 5)); assert st.frontier == (2, 3)   # never retreats
    st.advance_frontier(None);   assert st.frontier == (2, 3)   # None-safe no-op


def test_frontier_survives_new_prompt_reset():
    from sonari.session_stream import SessionStream
    st = SessionStream(); st.advance_frontier((4, 1))
    st.reset_for_new_prompt()
    assert st.frontier == (4, 1)                # monotonic across turns; only SESSION_END drops it


def test_note_spoken_advances_frontier_only_on_forward_completion():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    e = d.history.record("s0", "prose", "hello")     # (msg_id 0, seq 0)
    it = SpeechItem(id=1, session="s0", kind="prose", text="hello",
                    is_decision=False, forward=True)
    d._state._pending_heard[it.id] = e; d._current_item = it
    d.note_spoken(it, completed=True)
    assert e.heard is True and st.frontier == (e.msg_id, e.seq)


def test_browse_replay_flips_heard_but_frontier_stays():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    e = d.history.record("s0", "prose", "old")
    it = SpeechItem(id=1, session="s0", kind="prose", text="old",
                    is_decision=False, forward=False)   # browse replay: NOT forward
    d._state._pending_heard[it.id] = e; d._current_item = it
    d.note_spoken(it, completed=True)
    assert e.heard is True                    # heard still flips (nav's other uses)
    assert st.frontier is None                # but the frontier did NOT move (B1)


def test_mid_item_barge_in_leaves_frontier_unchanged():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    e = d.history.record("s0", "prose", "cut")
    it = SpeechItem(id=1, session="s0", kind="prose", text="cut",
                    is_decision=False, forward=True)
    d._state._pending_heard[it.id] = e; d._current_item = it
    d.note_spoken(it, completed=False)        # R-8: mid-item cut, not full completion
    assert e.heard is False and st.frontier is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_frontier.py -q`
Expected: FAIL — `AttributeError: 'SessionStream' object has no attribute 'frontier'`.

- [ ] **Step 3: Add the frontier field + method** (`src/sonari/session_stream.py`, after `self.guided = False`, line 24)

```python
        self.guided = False                 # received the setup-guidance cue once
        # SP4 frontier: the monotonic "furthest I've dealt with" high-water mark,
        # (msg_id, seq) of a HistoryEntry, None == nothing dealt-with yet. DISTINCT
        # from nav_cursor (browse). Advanced ONLY by note_spoken (forward completion)
        # and the pile-skip gesture; never derived from heard (B1); never retreats;
        # NOT reset on a new prompt (cross-turn) — dropped only when SESSION_END pops
        # the stream. Plain JSON-shaped tuple so SP6 serializes it unchanged.
        self.frontier = None
```

Add the method after `reset_for_new_prompt` (do NOT touch `reset_for_new_prompt` — the frontier must survive a new prompt):

```python
    def advance_frontier(self, key) -> None:
        """Monotonically advance the frontier to key=(msg_id, seq). No-op unless key
        is strictly ahead (None frontier == nothing dealt-with yet). The frontier
        NEVER retreats and is NOT derived from the heard flags."""
        if key is not None and (self.frontier is None or key > self.frontier):
            self.frontier = key
```

- [ ] **Step 4: Advance the frontier in `note_spoken`** (`src/sonari/daemon/host.py:327-331`, inside the existing `with self._lock:`)

```python
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
```

- [ ] **Step 5: Mark the forward-readout enqueue sites**

`src/sonari/daemon/host.py:284` (`_flush_prose_buffer` — the normal prose readout):

```python
        for text, entry in buf:
            self._enqueue(session, "prose", text, False, entry=entry, forward=True)
```

`src/sonari/daemon/features/decisions.py` — add `forward=True` to each decision-readout enqueue: `on_choice` (line 104, `"choice"`), `on_plan` (121, `"plan"`), `on_permission` (138, `"permission"`), `on_permission_request` (172, `"permission"` — keep the `item_id =` assignment). Example for `on_choice`:

```python
    ctx.host._enqueue(session, "choice", text, True, entry=entry, forward=True)
```

- [ ] **Step 6: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_frontier.py -q`
Expected: PASS.

- [ ] **Step 7: Full suite green, then commit**

Run: `.venv/bin/python -m pytest -q` → Expected: `1082 passed, 1 skipped` (+6).

```bash
git add src/sonari/session_stream.py src/sonari/daemon/host.py \
  src/sonari/daemon/features/decisions.py tests/test_frontier.py
git commit -m "feat(sp4): monotonic per-session frontier, advanced only on forward-readout completion"
```

---

### Task 3: cross-turn forward-from-frontier read primitive + live-edge accessor

The mechanism SP5's catch-up action consumes (SP4 builds the primitive, not the action — R-2/R-3). A frontier-keyed **sibling** to `unheard()`, spanning the open session lifetime keyed on the stable `(msg_id, seq)`. **Reconciliation (KNOWN DRIFT #2):** `unheard()` now has shipped production consumers that rely on its current-turn scoping as a floor — the ⌃⌘W "{u} unheard" clause (`control.py:65`) and `unheard_age()`'s "stale" word (`history.py:162`). So SP4 does **not** widen `unheard()`; it ADDS `unheard_from_frontier()` and leaves `unheard()` turn-scoped, keeping the byte-exact ⌃⌘W oracles in `tests/test_whereami_v2.py` green. The new read keys off the frontier scalar, never `heard` (a browse replay flips `heard` above the frontier — a heard-based read would drop those from catch-up, B1 on the read side). Also builds the fail-LOUD aged-out **detection** (R-1); SP5 speaks the cue.

**Files:**
- Modify: `src/sonari/history.py` (add `unheard_from_frontier` + `newest_key`; leave `unheard()`/`unheard_age()` untouched)
- Test: `tests/test_history.py` (append)

**Interfaces:**
- Produces: `SessionHistory.unheard_from_frontier(session, frontier) -> "tuple[list, bool]"` returning `(entries, aged_out)` — entries strictly ahead of `frontier=(msg_id,seq)` (None => all), oldest first; `aged_out` True when the frontier referent is behind the oldest surviving entry. `SessionHistory.newest_key(session) -> "tuple[int,int] | None"` (the live edge).
- Consumes: `HistoryEntry.msg_id`/`.seq`; the per-session `deque` in `_entries`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_history.py`)

```python
def test_unheard_from_frontier_spans_turns_keyed_on_msgid_seq():
    from sonari.history import SessionHistory
    h = SessionHistory()
    h.record("s", "prose", "t1a"); h.end_message("s")   # (0,0)
    h.record("s", "prose", "t1b")                         # (1,0)
    h.start_turn("s")                                     # new turn: msg_id bumps
    h.record("s", "prose", "t2a")                         # (2,0)
    entries, aged = h.unheard_from_frontier("s", (0, 0))
    assert [e.text for e in entries] == ["t1b", "t2a"] and aged is False   # crosses turns
    entries, aged = h.unheard_from_frontier("s", None)
    assert [e.text for e in entries] == ["t1a", "t1b", "t2a"] and aged is False


def test_unheard_from_frontier_ignores_heard_flag():
    from sonari.history import SessionHistory
    h = SessionHistory()
    h.record("s", "prose", "a"); h.end_message("s")       # (0,0)
    b = h.record("s", "prose", "b")                        # (1,0)
    b.heard = True                                         # browse-replayed ABOVE the frontier
    entries, _ = h.unheard_from_frontier("s", (0, 0))
    assert [e.text for e in entries] == ["b"]              # heard=True yet still returned (B1 read side)


def test_unheard_from_frontier_aged_out_fail_loud():
    from sonari.history import SessionHistory
    h = SessionHistory(cap=3)                              # tiny window forces eviction
    for i in range(5):
        h.record("s", "prose", "m{0}".format(i)); h.end_message("s")
    entries, aged = h.unheard_from_frontier("s", (0, 0))   # frontier behind the evicted head
    assert aged is True
    assert entries[0].text == "m2"                         # resumes at oldest SURVIVING, never mid-pile


def test_newest_key_is_live_edge():
    from sonari.history import SessionHistory
    h = SessionHistory()
    assert h.newest_key("s") is None
    h.record("s", "prose", "a"); h.end_message("s")
    last = h.record("s", "prose", "b")
    assert h.newest_key("s") == (last.msg_id, last.seq)


def test_unheard_stays_turn_scoped_for_shipped_consumers():
    from sonari.history import SessionHistory
    h = SessionHistory()
    h.record("s", "prose", "old"); h.start_turn("s")
    h.record("s", "prose", "new")
    assert [e.text for e in h.unheard("s")] == ["new"]     # unchanged: current-turn floor
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_history.py -q`
Expected: FAIL — `AttributeError: 'SessionHistory' object has no attribute 'unheard_from_frontier'`.

- [ ] **Step 3: Add the primitive + live-edge accessor** (`src/sonari/history.py`, after `unheard_age` — before `reset`, line 172)

```python
    def unheard_from_frontier(self, session, frontier):
        """Entries strictly AHEAD of *frontier*=(msg_id, seq), oldest first, across the
        OPEN SESSION LIFETIME (all turns). The cross-turn forward-read the SP5 catch-up
        action consumes — a frontier-aware SIBLING to unheard() (which stays turn-scoped
        for its ⌃⌘W consumers). frontier=None => every surviving entry.

        Keys off the frontier scalar, NOT `heard`: a browse replay flips heard=True on
        entries ABOVE the frontier, so a heard-based read would silently drop those
        browsed-but-not-caught-up items from catch-up (B1 on the read side). Drops BOTH
        the turn filter AND the `not heard` filter of unheard().

        Returns (entries, aged_out). aged_out is True when *frontier* points BEHIND the
        oldest surviving entry (its referent was evicted from the bounded deque): a gap
        aged out of the window, so the consumer plays the fail-LOUD 'earlier output aged
        out' cue and resumes at entries[0] — never a silent mid-pile start (R-1). O(n) —
        runs only on the human-paced catch-up/skip pull, NEVER in the keep-going lock."""
        d = self._entries.get(session)
        if not d:
            return [], False
        if frontier is None:
            return list(d), False
        entries = [e for e in d if (e.msg_id, e.seq) > frontier]
        aged_out = frontier < (d[0].msg_id, d[0].seq)
        return entries, aged_out

    def newest_key(self, session):
        """The (msg_id, seq) of the newest surviving entry (the live edge), or None when
        the session has no history. The deliberate pile-skip advances the frontier to
        this (§10.1)."""
        d = self._entries.get(session)
        return (d[-1].msg_id, d[-1].seq) if d else None
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_history.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite green, then commit**

Run: `.venv/bin/python -m pytest -q` → Expected: `1087 passed, 1 skipped` (+5).

```bash
git add src/sonari/history.py tests/test_history.py
git commit -m "feat(sp4): frontier-keyed cross-turn forward-read primitive with aged-out detection"
```

---

### Task 4: tool-use transcript capture at every verbosity (Fork A = A1)

Today `on_tool` (`prose.py:39-50`) records **nothing** to history in **any** verbosity — a total gap. Fork A1: record a `tool` `HistoryEntry` carrying the already-computed generic input `summary` at **every** verbosity (agent-neutral — no new hook, no CC-specific shape crosses the socket). The everything-verbosity announce also links to that entry with `forward=True` so hearing it advances the frontier (Q8). Medium's spoken summary rendering and quiet stay §15 Pass-2 — this task is about **capture**, not the spoken render.

**Files:**
- Modify: `src/sonari/history.py:21` (kind comment: add `tool`)
- Modify: `src/sonari/daemon/features/prose.py:39-50` (`on_tool`)
- Test: `tests/test_frontier.py` (append)

**Interfaces:**
- Consumes: `history.record` / `history.end_message`; `_flush_prose_buffer`; `_enqueue(..., entry=, forward=True)` (Tasks 1-2).
- Produces: a `HistoryEntry` with `kind == "tool"`, `text == summary or "Running {tool}."`, at every verbosity.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_frontier.py`)

```python
def _tool_msg(session, tool, summary):
    from sonari.protocol import PROTOCOL_VERSION, MsgType
    return {"v": PROTOCOL_VERSION, "type": MsgType.TOOL, "session": session,
            "tool": tool, "summary": summary}


def test_on_tool_records_history_at_every_verbosity():
    for verb in ("everything", "medium", "quiet"):
        sessions = SessionManager(); sessions.set_foreground("s0")
        c = _cfg(); c["verbosity"] = verb
        d = SpeechDaemon(_FakeSpeaker(), sessions, c)
        with d._state.transaction():
            d.handle_message(_tool_msg("s0", "Grep", "searching for TODO"))
        entries = d.history.entries_for_message("s0", 0)
        assert [(e.kind, e.text) for e in entries] == [("tool", "searching for TODO")], verb


def test_on_tool_announces_forward_at_everything_only():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())      # everything
    with d._state.transaction():
        d.handle_message(_tool_msg("s0", "Grep", "searching for TODO"))
    announce = [x for x in d._stream("s0").queue._items if x.kind == "tool_announce"]
    assert announce and announce[0].forward is True

    sessions2 = SessionManager(); sessions2.set_foreground("s0")
    c = _cfg(); c["verbosity"] = "medium"
    d2 = SpeechDaemon(_FakeSpeaker(), sessions2, c)
    with d2._state.transaction():
        d2.handle_message(_tool_msg("s0", "Grep", "searching for TODO"))
    assert not [x for x in d2._stream("s0").queue._items if x.kind == "tool_announce"]


def test_on_tool_falls_back_to_running_tool_when_no_summary():
    sessions = SessionManager(); sessions.set_foreground("s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    with d._state.transaction():
        d.handle_message(_tool_msg("s0", "Bash", ""))
    entries = d.history.entries_for_message("s0", 0)
    assert entries[0].text == "Running Bash."
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_frontier.py -k on_tool -q`
Expected: FAIL — `entries_for_message` returns `[]` (nothing recorded today).

- [ ] **Step 3: Rewrite `on_tool`** (`src/sonari/daemon/features/prose.py:39-50`)

```python
@handler(MsgType.TOOL)
def on_tool(ctx, msg):
    session = ctx.session
    verbosity = ctx.verbosity
    tool = msg.get("tool", "")
    summary = (msg.get("summary") or "").strip()
    text = summary if summary else "Running {0}.".format(tool)
    # Fork A/A1: record the tool-use input summary to the transcript at EVERY
    # verbosity (today on_tool records nothing in any verbosity — a total gap).
    # `summary` is the already-computed generic string; agent-neutral, no new hook,
    # no CC-specific shape crosses the socket. Own message group, like a decision.
    entry = ctx.host.history.record(session, "tool", text)
    ctx.host.history.end_message(session)
    if verbosity == "everything":
        # Everything also ANNOUNCES (medium's spoken summary + quiet are §15 Pass-2).
        # Keep textual order: read prose that preceded this tool call first, then link
        # the announce to its entry (forward=True) so hearing it advances the frontier.
        ctx.host._flush_prose_buffer(session)
        ctx.host._enqueue(session, "tool_announce", text, False,
                          entry=entry, forward=True)
    return None
```

- [ ] **Step 4: Update the `HistoryEntry.kind` comment** (`src/sonari/history.py:21`)

```python
        self.kind = kind          # prose|choice|plan|permission|tool
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_frontier.py -k on_tool -q`
Expected: PASS.

- [ ] **Step 6: Full suite green, then commit**

Run: `.venv/bin/python -m pytest -q` → Expected: `1090 passed, 1 skipped` (+3). If a pre-existing test asserted `on_tool` records **nothing** at medium/quiet (checking history emptiness, not queue emptiness), it encoded the gap this task closes — update it to assert the `tool` entry is recorded, and note the change in the commit body.

```bash
git add src/sonari/daemon/features/prose.py src/sonari/history.py tests/test_frontier.py
git commit -m "feat(sp4): capture tool-use input summary to the transcript at every verbosity (Fork A1)"
```

---

### Task 5: `⌃⌘S`-start is a quiet resume — drop the pre-start pile (Fork D = D2)

Today `⌃⌘S`-start un-stops the session, makes it the speaker, and the speak loop then drains its whole pre-start queue in FIFO — a flood (a **direct queue-drain**, not keep-going). D2: start re-engages the voice and auto-flows only **post-start** output; the pre-start pile stays behind the frozen frontier for the SP5 catch-up key. **SP4 seam:** in `on_stop_session`'s resume branch, drop the pre-start **queue** (dropping its `_pending_heard` markers) — the pile persists in the `history` transcript behind the frontier (nothing advanced it), and the voice flows only on post-start output. **Decision handling:** the clear drops queued decision items too, but they persist in history and a live blocking permission stays answerable via `_pending_decisions` / `⌃⌘D` — the binding contract is only "frontier stays behind the pile, pile reachable by catch-up from history." The mechanism ruled out (do NOT inherit): "advance the frontier **past** the pile on start" — that would put the pile *below* the frontier and *out of* catch-up, silently dropping the backlog (contra R7). The D7 global mode-switch flood (leaving quiet) is KEPT and untouched.

**Files:**
- Modify: `src/sonari/daemon/features/playback.py:49-57` (`on_stop_session` resume branch)
- Test: `tests/test_frontier.py` (append)

**Interfaces:**
- Consumes: `st.queue.clear()` (returns dropped items, `queue.py:83`); `_drop_pending`; `unheard_from_frontier` (Task 3).
- Produces: no new symbol; a behavior change to the ⌃⌘S-start resume path.

- [ ] **Step 1: Write the failing test** (append to `tests/test_frontier.py`)

```python
def test_start_is_quiet_resume_drops_pre_start_pile_keeps_history():
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    st.stopped = True                              # stopped, piling behind a frozen frontier
    e1 = d.history.record("s0", "prose", "pile 1")
    d._enqueue("s0", "prose", "pile 1", False, entry=e1, forward=True)
    e2 = d.history.record("s0", "prose", "pile 2")
    d._enqueue("s0", "prose", "pile 2", False, entry=e2, forward=True)
    assert len(st.queue) == 2 and st.frontier is None
    with d._state.transaction():                   # ⌃⌘S-start (Fork-4 asymmetric START)
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.STOP_SESSION,
                          "session": "s0"})
    assert st.stopped is False
    assert [x.text for x in st.queue._items] == ["Resumed."]   # pre-start pile dropped from the queue
    assert st.frontier is None                                 # frontier stayed BEHIND the pile
    entries, _ = d.history.unheard_from_frontier("s0", st.frontier)
    assert [e.text for e in entries] == ["pile 1", "pile 2"]   # pile persists, catch-up-reachable
    assert d._pending_heard == {}                              # markers dropped, no orphans
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_frontier.py -k quiet_resume -q`
Expected: FAIL — the queue still holds `["Resumed.", "pile 1", "pile 2"]` (the pile was not dropped).

- [ ] **Step 3: Drop the pre-start queue in the resume branch** (`src/sonari/daemon/features/playback.py:49-57`)

```python
    if st.stopped:
        # Resuming (⌃⌘S-start re-engage). D2 (2026-07-16): a QUIET RESUME. Un-stop,
        # lift to flowing, and MOVE THE VOICE to the started session. DROP the pre-start
        # QUEUE (its buffered pile) so start does NOT auto-drain it — the pile persists
        # in the history transcript BEHIND the frozen frontier (nothing advanced it),
        # reachable later by SP5's catch-up; only post-start output flows. Dropped
        # decision items also persist in history, and a live blocking permission stays
        # answerable via _pending_decisions / ⌃⌘D. The clear MUST precede the "Resumed."
        # enqueue (which is at_front) so it is not itself dropped.
        st.stopped = False
        ctx.host.voice_state = "flowing"
        sessions.set_speaker(fg)
        ctx.host._drop_pending(st.queue.clear())
        ctx.host._enqueue(fg, "prose", "Resumed.", False, mute_exempt=True, at_front=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_frontier.py -k quiet_resume -q`
Expected: PASS.

- [ ] **Step 5: Full suite green, then commit**

Run: `.venv/bin/python -m pytest -q` → Expected: `1091 passed, 1 skipped` (+1). If a pre-existing test asserted that ⌃⌘S-start drains/plays the pre-start pile, it encoded the old flood — update it to the D2 quiet-resume behavior and note it in the commit body.

```bash
git add src/sonari/daemon/features/playback.py tests/test_frontier.py
git commit -m "feat(sp4): make ctrl-cmd-S start a quiet resume, dropping the pre-start pile behind the frontier (Fork D2)"
```

---

### Task 6: the deliberate pile-skip gesture (Fork B = B1, Fork C = C1)

Frontier write-path (b): a distinct, confirmatory gesture — **not** the safe `⌃⌘↓` return — that advances the frontier **past** the unheard pile to the live edge, **speaks a count**, and stops the flood, **without** marking the skipped entries `heard` (they stay `heard=False`, browsable, but below the frontier = out of the auto-catch-up path). **Fork C1:** under divergence (`voice_state == flowing` and `speaker != workspace`) it targets the flooding **speaker** and does not move the window; otherwise the **workspace**. SP4 ships the **action + keymap plumbing**; the action ships **UNBOUND** — the final chord is an **OWNER EAR-GATE** (Nima co-designs chords by ear). **Proposed chord: `⌃⌘⇧↓`** — rationale: mnemonic sibling to the safe `⌃⌘↓` return (down = toward live), with Shift as the standard deliberate/destructive modifier; expressible entirely via `keymap.json` (mods `["ctrl","cmd","shift"]`, key `down`) with no platform-backend change. **Do NOT bind it in this task** — leave `skip_pile` out of `_DEFAULT_KEYS`; the binding is the ear-gate line item below.

> **⚠ OWNER EAR-GATE (Nima):** the pile-skip **chord** and the **"Skipping N items." string** are his by-ear calls. SP4 ships the action unbound with the proposed `⌃⌘⇧↓` + the proposed string; both await his listening pass (co-design one piece at a time, per his working style). The action + plumbing are done; only the chord + wording are open.

**Files:**
- Modify: `src/sonari/protocol.py:44` (add `SKIP_PILE = "skip_pile"`)
- Modify: `src/sonari/daemon/features/playback.py` (add `on_skip_pile` handler)
- Modify: `src/sonari/keymap.py:26-48` (add `"skip_pile"` to `ACTION_MESSAGES`; ship UNBOUND — NOT in `_DEFAULT_KEYS`)
- Test: `tests/test_frontier.py` (append)

**Interfaces:**
- Consumes: `history.unheard_from_frontier` + `history.newest_key` (Task 3); `st.advance_frontier` (Task 2); `_drop_pending` + `st.queue.clear()`.
- Produces: `MsgType.SKIP_PILE = "skip_pile"`; `on_skip_pile(ctx, msg)` handler; `ACTION_MESSAGES["skip_pile"] = {"type": "skip_pile"}` (unbound).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_frontier.py`)

```python
def test_skip_pile_is_a_resolvable_unbound_action():
    from sonari.keymap import ACTION_MESSAGES, resolve_keymap, default_keymap
    assert ACTION_MESSAGES["skip_pile"] == {"type": "skip_pile"}
    assert "skip_pile" not in default_keymap()          # ships UNBOUND — his ear-gate chord
    binding = default_keymap()["where_am_i"]            # a known-good key+mods for this platform
    resolved = resolve_keymap({"skip_pile": binding})
    assert any(r["action"] == "skip_pile" for r in resolved)   # bindable via keymap.json


def test_skip_pile_advances_frontier_and_announces_count():
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    st = d._stream("s0")
    for i in range(3):
        e = d.history.record("s0", "prose", "p{0}".format(i)); d.history.end_message("s0")
        d._enqueue("s0", "prose", "p{0}".format(i), False, entry=e, forward=True)
    assert st.frontier is None and len(st.queue) == 3
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SKIP_PILE, "session": "s0"})
    assert st.frontier == d.history.newest_key("s0")     # advanced PAST the pile to live
    assert all(not e.heard for e in d.history.entries_for_message("s0", 2))  # NOT marked heard
    ahead, _ = d.history.unheard_from_frontier("s0", st.frontier)
    assert ahead == []                                   # pile now below the frontier
    assert [x.text for x in st.queue._items] == ["Skipping 3 items."]  # count cue; pile dropped
    assert d._pending_heard == {}


def test_skip_pile_nothing_to_skip_does_not_nag():
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SKIP_PILE, "session": "s0"})
    assert [x.text for x in d._stream("s0").queue._items] == ["Nothing to skip."]


def test_skip_pile_targets_speaker_under_divergence():
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    sessions = SessionManager(); sessions.set_foreground("ws")
    sessions.register("ws", cwd="/x/ws"); sessions.register("spk", cwd="/x/spk")
    sessions.set_speaker("spk")                          # diverged: speaker != workspace
    d = SpeechDaemon(_FakeSpeaker(), sessions, _cfg())
    d.voice_state = "flowing"
    spk_st = d._stream("spk")
    e = d.history.record("spk", "prose", "flood")
    d._enqueue("spk", "prose", "flood", False, entry=e, forward=True)
    with d._state.transaction():
        d.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SKIP_PILE, "session": "ws"})
    assert spk_st.frontier == d.history.newest_key("spk")   # C1: the SPEAKER's frontier advanced
    assert d._stream("ws").frontier is None                 # the workspace was NOT touched
    assert sessions.workspace() == "ws"                     # window unmoved
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_frontier.py -k skip_pile -q`
Expected: FAIL — `KeyError: 'skip_pile'` (no action) / `AttributeError` on `MsgType.SKIP_PILE`.

- [ ] **Step 3: Add the MsgType** (`src/sonari/protocol.py`, after `REPEAT_LAST`, line 44)

```python
    REPEAT_LAST = "repeat_last"         # ⌃⌘R: re-speak the last completed content utterance
    SKIP_PILE = "skip_pile"             # deliberate pile-skip: advance the frontier past the pile (SP4)
```

- [ ] **Step 4: Add the `on_skip_pile` handler** (`src/sonari/daemon/features/playback.py`, after `on_skip`, line 29)

```python
@handler(MsgType.SKIP_PILE)
def on_skip_pile(ctx, msg):
    # Deliberate PILE-SKIP (Fork B1): a distinct confirmatory gesture (NOT the safe ⌃⌘↓
    # browse return) that advances the frontier PAST the unheard pile to the live edge
    # and SPEAKS a count — the skipped entries stay heard=False in the transcript
    # (browsable) but BELOW the frontier (out of the auto-catch-up path). Fork C1: under
    # divergence (voice flowing, speaker != workspace) it targets the flooding SPEAKER
    # and does NOT move the window; otherwise the workspace. Frontier write-path (b).
    host = ctx.host
    sessions = host.sessions
    ws = sessions.workspace()
    spk = sessions.speaker()
    if host.voice_state == "flowing" and spk is not None and spk != ws:
        target = spk                     # C1: skip the flooder, keep the window put
    else:
        target = ws
    if target is None:
        host.speaker.earcon("error")
        return None
    st = host._stream(target)
    entries, _ = host.history.unheard_from_frontier(target, st.frontier)
    count = len(entries)
    if count == 0:
        # Nothing ahead of the frontier — do NOT nag "skipped 0" (B3 rejected).
        host._enqueue(target, "prose", "Nothing to skip.", False,
                      mute_exempt=True, pause_exempt=True, at_front=True)
        return None
    # Advance PAST the pile to the live edge WITHOUT marking the skipped entries heard.
    st.advance_frontier(host.history.newest_key(target))
    # Stop the flood: drop the target's queued pile and cut its in-flight utterance.
    host._drop_pending(st.queue.clear())
    cur = host._current_item
    if cur is not None and cur.session == target:
        host.speaker.cancel()
    # Confirmatory count — OWNER EAR-GATE string (grammar v2: role word "item(s)"
    # adjacent to the number). Barge-in-class cue: mute_exempt + pause_exempt + at_front.
    noun = "item" if count == 1 else "items"
    host._enqueue(target, "prose", "Skipping {0} {1}.".format(count, noun), False,
                  mute_exempt=True, pause_exempt=True, at_front=True)
    return None
```

- [ ] **Step 5: Register the keymap action, UNBOUND** (`src/sonari/keymap.py`)

Add to `ACTION_MESSAGES` (after `slower`, line 47):

```python
    "slower": {"type": "set_rate", "delta": -25},
    # SP4 pile-skip: bindable + resolvable, but ships UNBOUND (NOT in _DEFAULT_KEYS) —
    # the chord is Nima's ear-gate. Proposed: ⌃⌘⇧↓ (keymap.json: key "down",
    # mods ["ctrl","cmd","shift"]).
    "skip_pile": {"type": "skip_pile"},
```

Leave `_DEFAULT_KEYS` unchanged (do NOT add `skip_pile`).

- [ ] **Step 6: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_frontier.py -k skip_pile -q`
Expected: PASS (4 tests).

- [ ] **Step 7: Full suite green, then commit**

Run: `.venv/bin/python -m pytest -q` → Expected: `1095 passed, 1 skipped` (+4).

```bash
git add src/sonari/protocol.py src/sonari/daemon/features/playback.py \
  src/sonari/keymap.py tests/test_frontier.py
git commit -m "feat(sp4): add the deliberate pile-skip gesture (Fork B1/C1), shipped unbound for ear-gate"
```

---

### Task 7: SP4 concurrency hammers (join the permanent guard set)

Campaign `:14` — "any speak-loop change adds itself to the hammer set." SP4's frontier write paths run under `self._lock`, so they join `tests/test_concurrency_guards.py`. Two deterministic B1/monotonicity hammers (PERMANENT, never weaken), plus feeding `SKIP_PILE` into the existing real-threaded storm so the skip path's `queue.clear()` + frontier advance are exercised under contention (the existing orphan-marker assertion then covers it — no assertion is weakened).

**Files:**
- Modify: `tests/test_concurrency_guards.py` (append two tests; add `MsgType.SKIP_PILE` to the storm's `ops` list)

**Interfaces:**
- Consumes: `note_spoken` + `SpeechItem.forward` (Tasks 1-2); `SessionStream.frontier`; `MsgType.SKIP_PILE` handler (Task 6).

- [ ] **Step 1: Add `SKIP_PILE` to the storm rotation** (`tests/test_concurrency_guards.py`, the `ops` list at lines 257-262 inside `hammer`)

Append `MsgType.SKIP_PILE` to the list (additive — do NOT remove or reorder existing ops, do NOT weaken any assertion):

```python
            ops = [MsgType.STOP_SESSION, MsgType.FLUSH, MsgType.SET_FOREGROUND,
                   MsgType.JUMP_WAITING, MsgType.CHOOSER_STEP, MsgType.CHOOSER_DIGIT,
                   MsgType.CHOOSER_COMMIT, MsgType.CHOOSER_CANCEL, MsgType.STOP_ALL,
                   MsgType.REPEAT_LAST,
                   # SP4: exercise the pile-skip's queue.clear() + frontier advance under
                   # contention; the orphaned-marker assertion below covers its enqueues.
                   MsgType.SKIP_PILE]
```

- [ ] **Step 2: Write the two deterministic hammers** (append to `tests/test_concurrency_guards.py`)

```python
def test_frontier_provenance_gated_advance_is_permanent():
    """B1 PERMANENT: the frontier is the max over FORWARD-provenance completions, NOT
    over the heard flag. A browse replay completes and flips heard=True on an entry
    ABOVE the frontier, yet the frontier does NOT move; a forward readout advances it.
    NEVER weaken this — a heard-derived frontier is the B1 corruption."""
    from sonari.queue import SpeechItem
    sessions = SessionManager(); sessions.set_foreground("s0")
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    daemon = SpeechDaemon(_RaisingSpeaker(), sessions, config)
    st = daemon._stream("s0")
    e0 = daemon.history.record("s0", "prose", "a"); daemon.history.end_message("s0")
    e1 = daemon.history.record("s0", "prose", "b")          # (1,0), ABOVE e0
    br = SpeechItem(id=1, session="s0", kind="prose", text="b",
                    is_decision=False, forward=False)       # browse replay
    daemon._pending_heard[br.id] = e1; daemon._current_item = br
    daemon.note_spoken(br, completed=True)
    assert e1.heard is True and st.frontier is None         # heard flipped, frontier did NOT move
    fw = SpeechItem(id=2, session="s0", kind="prose", text="a",
                    is_decision=False, forward=True)        # forward readout
    daemon._pending_heard[fw.id] = e0; daemon._current_item = fw
    daemon.note_spoken(fw, completed=True)
    assert st.frontier == (e0.msg_id, e0.seq)               # forward readout advances it


def test_frontier_never_retreats_across_write_paths():
    """Monotonicity: forward-hear -> advance; browse-replay-above -> no move; pile-skip
    -> advance to live edge; new arrival -> no move. The frontier never retreats."""
    from sonari.queue import SpeechItem
    sessions = SessionManager(); sessions.set_foreground("s0")
    sessions.register("s0", cwd="/x/s0")
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    config["verbosity"] = "everything"
    daemon = SpeechDaemon(_RaisingSpeaker(), sessions, config)
    st = daemon._stream("s0")
    es = []
    for i in range(4):
        es.append(daemon.history.record("s0", "prose", "p{0}".format(i)))
        daemon.history.end_message("s0")                    # es -> (0,0)..(3,0)
    seen = []
    it = SpeechItem(id=1, session="s0", kind="prose", text="p1",
                    is_decision=False, forward=True)
    daemon._pending_heard[it.id] = es[1]; daemon._current_item = it
    daemon.note_spoken(it, completed=True); seen.append(st.frontier)      # (1,0)
    br = SpeechItem(id=2, session="s0", kind="prose", text="p3",
                    is_decision=False, forward=False)
    daemon._pending_heard[br.id] = es[3]; daemon._current_item = br
    daemon.note_spoken(br, completed=True); seen.append(st.frontier)      # (1,0) — no move
    with daemon._state.transaction():
        daemon.handle_message(_msg(MsgType.SKIP_PILE, "s0"))
    seen.append(st.frontier)                                              # (3,0) — skip to live
    daemon.history.record("s0", "prose", "p4"); seen.append(st.frontier)  # (3,0) — arrival no move
    assert seen == [(1, 0), (1, 0), (3, 0), (3, 0)]
    assert all(b >= a for a, b in zip(seen, seen[1:]))                    # never retreats
```

- [ ] **Step 3: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_concurrency_guards.py -q`
Expected: PASS (all four permanent guards + the two new hammers). The storm test may run ~1s.

- [ ] **Step 4: Full suite green, then commit**

Run: `.venv/bin/python -m pytest -q` → Expected: `1097 passed, 1 skipped` (+2).

```bash
git add tests/test_concurrency_guards.py
git commit -m "test(sp4): permanent frontier provenance + monotonicity hammers; skip-pile in the storm"
```

---

### Task 8: spec-prose reconciliation audit (verify R-1..R-8 are in the oracle)

**Drift correction (do this task with eyes open):** the synthesis §3 anticipated that SP4 would *apply* the R-1..R-8 doc edits to `docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md`. A read of that spec at HEAD shows it has **already been reconciled** — it carries a "Changelog — oracle refresh (2026-07-16)" (lines 17-67) with A1-A7/B1-B7 mapping to R-1..R-8, and the body prose already reflects every refinement. So this task is a **verification** that the oracle is coherent and complete, not a re-edit. Confirm each row; only if a row is genuinely missing or self-contradictory, fix that one line (a targeted edit, not a rewrite) and note it in the commit body.

**Files:**
- Verify (edit only on a confirmed gap): `docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md`

- [ ] **Step 1: Walk the checklist** — for each, confirm the cited spec location says what R-x requires:

  - **R-1** (bounded window + fail-LOUD aged-out cue, not "always recoverable"): §9 `:449-478`, §7 `:398-403`, changelog A3 `:31-33`. ✅ expected present.
  - **R-2** (legacy `catch_up` → SP5 net-new): §8 row `:427`, §8 `:440-443`, §10.1 `:562-563`, D17 `:647`, changelog A1 `:27-28`. ✅
  - **R-3** (unheard reversal cross-turn): behavioral spec does not cite `unheard()` (a mechanism); the reversal is recorded in the synthesis + built in Task 3. Confirm §10 `:502-521` describes forward-from-frontier reading. ✅ (no spec-body edit expected)
  - **R-4** (readout-skipped item advances the frontier; the medium opaque-bash skip): §9 `:462`, `:496-497`; §15 `:669-671`. ✅
  - **R-5** (catch-up read does not clear stopped/muted): §10.1 `:566-568`, changelog A2 `:29-30`. ✅
  - **R-6** (stale cycle/muted-landing RESOLVED via chooser Fork-2): §15 `:682-685`, §6 transition `:366`, changelog A5 `:36-38`. ✅
  - **R-7** (§14 longest-waiting SETTLED, not vetoable; §15 discharged): §14 `:651-655`, §15 `:666-668`, changelog A6 `:39-40`. ✅
  - **R-8** (frontier unit = whole item; barge-in two-rule contract): §7 `:385-392`, §10 `:502-507`, changelog A7 `:41-43`. ✅

- [ ] **Step 2: Confirm the ratified forks are recorded** — Fork A=A1 (changelog B3 `:51-52`, §3 `:126-130`, §9 `:449-453`), Fork B=B1 (B4 `:53-55`, §8 row `:426`, §10 `:512-514`), Fork C=C1 (B5 `:56-57`, §10.1 `:543-546`), Fork D=D2 (B6 `:58-60`, R7 `:258-263`, §6 `:365`). ✅ expected present.

- [ ] **Step 3: Grep for un-swept stale tokens** that would contradict the build:

Run:
```bash
grep -nE "reuse the legacy|always recoverable|never a (permanent )?blind spot|follows the marker" \
  docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md
```
Expected: no matches in normative sections (any hit inside the historical §12/§13 record is acceptable and left as-is). If a normative-section hit is found, fix that one line to the reconciled language and record it.

- [ ] **Step 4: Commit only if an edit was needed**

If Steps 1-3 found and fixed a gap:
```bash
git add docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md
git commit -m "docs(sp4): reconcile residual stale spec phrasing to R-x (audit gap)"
```
If no gap (the expected outcome), record "spec oracle verified coherent against R-1..R-8, no edit needed" in the task's review notes — no commit.

---

## Resolved engineering questions (synthesis §6 — plan-author calls)

- **Q1 — frontier granularity = `HistoryEntry`** (`(msg_id, seq)`), finer than the browse cursor's paragraph/`msg_id` unit. They legitimately differ. *(Task 2.)*
- **Q2 — frontier shape/location:** a single per-session scalar `(msg_id, seq)` tuple (or `None`) on `SessionStream`, plain-JSON-shaped for SP6. A single scalar suffices because `_msg_id` is monotonic across turns (never reset by `start_turn`). *(Task 2.)*
- **Q3 — frontier vs `heard` (B1):** stored scalar advanced by exactly two write paths (forward completion in `note_spoken`; the pile-skip gesture), NEVER derived as `max(heard)`. Read side keys on `(msg_id,seq) > frontier`, never `heard`. *(Tasks 2, 3, 6.)*
- **Q4 — O(1) vs O(n):** frontier compared/advanced O(1) in `note_spoken` (the key is on the entry); the O(n) `unheard_from_frontier` materialization runs only on the human-paced catch-up/skip pull, never in the keep-going lock. *(Tasks 2, 3.)*
- **Q5 — ⌃⌘W "k waiting" count:** KEEP `len(st.queue)` (`control.py:55`) in SP4 — do not flip to a frontier-derived count. Once the pile count becomes user-facing it is an SP5 change (an observable string change to a shipped readout, review-gated). No SP4 code touches `control.py`. *(No task; noted.)*
- **Q6 — submit-lifts-quiet-hold:** DEFER. The frontier work does not force it, and it is largely **already closed**: `on_set_foreground` lifts `quiet-hold` on a Policy-A submit that takes a speakable voice (`lifecycle.py:91-94`, the W5 fix landed after the synthesis baseline). Not SP4's mandate; no action.
- **Q7 — ⌃⌘D vs the frontier:** falls out free from provenance-gating — ⌃⌘D cancels the in-flight item (`completed=False` → no advance) and discards leading queued prose (never completes → never advances). The residual `entry.heard=True` at `playback.py:133` is now frontier-neutral (the frontier is not `heard`-derived); left as a decorative marker (retiring it is optional and out of SP4 scope). *(Verified; no task.)*
- **Q8 — tool `_pending_heard` linkage:** YES — the everything-verbosity tool announce links to its `HistoryEntry` (`entry=`, `forward=True`) so hearing it advances the frontier. *(Task 4.)*
- **Q9 — orphaned-stream gap:** a mid-turn-at-restart session gets a bare `SessionStream` via `_stream()`; the frontier defaults `None` on that stream, so the seam stays clean. Mostly SP6; no SP4 action. *(Verified; no task.)*

## Seams SP4 shapes for SP5/SP6 (do NOT build here)

- **SP5 catch-up action:** consumes `unheard_from_frontier` (Task 3) — a net-new `MsgType` + handler + co-designed chord (legacy `catch_up` deleted `b4b3be1`). SP5 speaks the fail-LOUD "earlier output aged out" cue when the primitive returns `aged_out=True`, and resumes at `entries[0]`. SP5's catch-up read is a **preemption-class** cut (no resume). Catch-up advances the frontier to the live edge but does NOT clear `stopped`/`muted` (R-5).
- **SP5 pile-count on ⌃⌘W:** the frontier-derived "k unheard ahead of frontier" count that Q5 defers.
- **SP6 serialization:** the frontier is a plain `(int, int)` / `None` scalar on `SessionStream` — SP6 snapshots it with the roster/history. **Validity is session-lifetime-bound:** `SESSION_END` resets history (keys restart at 0) and pops the stream, so SP6 must NOT persist a frontier across a `SESSION_END` that re-zeroed the history it indexes (a restored `(5,2)` against re-zeroed history points at the wrong entry).
- **`history_cap` ↔ `backlog_cap` coupling:** both default 200 (`host.py:89-90`). SP4 changes neither. If a future SP raises `history_cap`, it MUST raise `backlog_cap` in tandem (or SP5's catch-up must trickle-feed the queue) — a history pile larger than `backlog_cap` overflows the queue on bulk catch-up enqueue (the refuted silent-front-drop).

---

## Expected test totals

Baseline `1073 passed, 1 skipped`.

| Task | Δ | Running total |
|---|---|---|
| 1 — `forward` flag | +3 | 1076 |
| 2 — frontier + advance | +6 | 1082 |
| 3 — forward-read primitive | +5 | 1087 |
| 4 — tool capture | +3 | 1090 |
| 5 — D2 quiet resume | +1 | 1091 |
| 6 — pile-skip gesture | +4 | 1095 |
| 7 — concurrency hammers | +2 | 1097 |
| 8 — spec audit | +0 | 1097 |

**Expected final: `1097 passed, 1 skipped`.** (Deltas assume no pre-existing test encoded a gap this plan closes; Tasks 4 and 5 flag the two places that could need a one-line reconciliation of an old-behavior assertion — adjust the total by that if hit.)

## Self-review (run against the synthesis + spec)

**1. Spec coverage.** SP4 scope closed: monotonic frontier + provenance-gated advance (T2), cross-turn forward-read primitive with aged-out detection R-1 (T3), tool capture Fork A1 (T4), spec reconciliation (T8). Forks: A1 (T4), B1+C1 (T6), D2 (T5). Foundation: `forward` flag + re-queue preservation (T1). Hammers R-8/B1/monotonicity (T7). Observables checked: monotonic-never-skips (T2/T3), browse-replay-frontier-neutral (T2/T7), frontier-unit-whole-item/barge-in (T2), readout-skipped-advances / new-arrival-never-advances (T7), aged-out fail-loud (T3), tool-at-every-verbosity (T4), deliberate-skip-without-heard (T6). Q1-Q9 resolved above. No SP5/SP6 scope leaked in.

**2. Placeholder scan.** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code step shows complete code; every test shows real assertions. Ear-gate items (T6 chord + string) are explicitly flagged as owner calls, not plan gaps.

**3. Type consistency.** `SpeechItem.forward: bool = False` (T1) — read in `note_spoken` (T2), set at readout sites (T2/T4), preserved at 4 re-queue sites (T1). `_enqueue(..., forward=False)` signature consistent everywhere. `SessionStream.frontier: (int,int)|None` + `advance_frontier(key)` (T2) — called with `history.newest_key()` (T6). `unheard_from_frontier(session, frontier) -> (entries, aged_out)` (T3) — unpacked `entries, _` at all callers (T5/T6). `newest_key(session)` (T3) → used in T6. `MsgType.SKIP_PILE` (T6) → used in T7. Shared test helpers `_cfg`/`_FakeSpeaker` + module imports defined in T1's `tests/test_frontier.py` and inherited by later appends. No signature/name drift found.

## Execution handoff

Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task (T1→T8, linear; T1 gates T2, T2 gates T3/T6/T7, T3 gates T5/T6), review between tasks, per-task + whole-branch Opus review, fix wave. Use superpowers:subagent-driven-development with pipelining. Nima does the live `./bin/sonari install` + listening pass, and holds the two T6 ear-gates (chord + "Skipping N items." string).

**2. Inline Execution** — superpowers:executing-plans, batch with checkpoints.

**Merge gate:** the 4 permanent concurrency guards + the 2 new hammers green at every commit; full suite `1097/1skip`; whole-branch review clean; then Nima's install + ear pass (incl. the pile-skip chord/string ear-gates) before merge.
