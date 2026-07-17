# SP6 — Restart Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serialize the daemon's durable per-session speech state (transcript pile, frontier, folder, stable number, `heard`) to a single atomic JSON snapshot off the speak-loop hot path, and reload it on boot so a restart preserves what you haven't heard and catch-up (⌃⌘L) reaches back into it the instant you return to a session.

**Architecture:** Three pure objects gain `to_state()` / `load_state()` (`SessionHistory`, `SessionStream`, `SessionManager`); a new `daemon/persistence.py` owns a fail-open, concurrent-safe `StateStore` (unique-temp + `os.replace`) and a debounced off-lock `PersistenceWriter` thread driven by a dirty `Event`. The host builds the snapshot dict under `self._lock` and writes it OUTSIDE the lock; it restores single-threaded at boot before any other actor exists; restored sessions are quarantined **provisional** (fail-closed liveness, excluded from ⌃⌘W + chooser) until they re-capture a real identity on their next prompt.

**Tech Stack:** Python 3 / pytest / the existing Sonari daemon (`say` / `afplay` / `sonari-hotkeyd`). No new runtime deps. macOS-only. Test runner: `.venv/bin/python -m pytest`.

**Baseline:** `build/sp6-persistence @ a59fddb`, `1177 passed, 1 skipped` via `.venv/bin/python -m pytest -q`.

---

## Global Constraints

Every task's requirements implicitly include this section (spec §12, verbatim contracts).

- **Off the speak-loop hot path.** Never hold `self._lock` across disk I/O; the only lock hold is the primitive-field snapshot copy. `mark_dirty()` sets an `Event` and returns — no lock, no I/O.
- **The permanent concurrency/monotonicity guards (`tests/test_concurrency_guards.py`) stay green at EVERY commit; assertions are never weakened.** Run them at the end of every task.
- **Conventional commits; NO AI / tool / session mentions** in commit messages, comments, or code. Author email is the noreply `74723240+nimkimi@users.noreply.github.com` (repo-local config is already set; do not add attribution footers).
- **Sacrificial-HOME dogfood.** Any `~/.sonari`-touching behavior is dogfooded under a sacrificial `HOME` before Nima installs to his real `~/.sonari`. Sandboxed `sonari install` silently fails to restart daemons — the final unsandboxed install + live verify is Nima's step, never the engineer's.
- **Worktree / subagent imports need `PYTHONPATH="$PWD/src"`; pytest is safe** (conftest pins `sys.path`).
- **Main pushes are Nima's.** This work lands on `build/sp6-persistence`, whole-branch reviewed, then merged by him. Do NOT push to `main`.
- **Defaults:** history deque cap comes from live config `history_cap` (default 200) and is NOT persisted — restore rebuilds each deque at the *current* cap. `restore_max_age_hours` (default **24**) bounds how old a restored pile may be before it is dropped on load.
- **Never run a live `claude` or a live daemon (`run()` / a real socket) in the suite.** All tests are synchronous units / the sync-harness idiom (direct `daemon._speak_loop_once()`, direct `daemon.handle_message(...)` inside `daemon._state.transaction()`, direct-set `daemon._current_item`).
- **The green gate before EVERY commit is the full suite** (`.venv/bin/python -m pytest -q`, ~14s) — it subsumes `tests/test_concurrency_guards.py`, so the permanent guards are proven green at every commit as the contract requires. The `-k`-filtered runs in each task are only the fast "verify it fails" check.
- **`old_string` anchors in the implementation steps are LOCATORS** (a nearby real line + line number), not necessarily byte-exact `Edit` targets — match on structure and re-read the file if whitespace has drifted; a whitespace mismatch is not a blocker.

---

## File Structure

| File | Create/Modify | One responsibility |
|---|---|---|
| `src/sonari/daemon/persistence.py` | Create (Tasks 1, 6) | `StateStore` (fail-open load / concurrent-safe save) + `PersistenceWriter` (debounced off-lock writer thread) + `STATE_VERSION`. |
| `src/sonari/history.py` | Modify (Task 2) | `SessionHistory.to_state()` / `load_state()` — entries + counters + stamp↔wall-clock normalization; deque rebuilt at live cap. |
| `src/sonari/session_stream.py` | Modify (Task 3) | `SessionStream.to_state()` / `load_state()` — the frontier only (list↔tuple). |
| `src/sonari/sessions.py` | Modify (Task 4) | `SessionManager.to_state()` / `load_state()` — roster + numbers; `_provisional` set; `is_provisional()`; `is_live()` fail-closed for provisional; `set_identity()` clears provisional. |
| `src/sonari/daemon/features/control.py` | Modify (Task 5) | `_also_clause` excludes provisional sessions from the ⌃⌘W Also-map. |
| `src/sonari/config.py` | Modify (Task 7) | Add `restore_max_age_hours: 24` to `DEFAULTS`. |
| `src/sonari/daemon/host.py` | Modify (Tasks 7, 8) | `__init__` constructs the store + writer; `_snapshot_state()` / `_restore_state()`; `mark_dirty()` hooks in `handle_message()` + `note_spoken()`; `run()` restore/start/finally wiring. |
| `tests/test_config.py` | Modify (Task 7) | Extend the pinned `DEFAULTS` key set with `restore_max_age_hours`. |
| `tests/test_persistence.py` | Create (Task 1) / grow (Tasks 2–9) | The whole SP6 suite: store + serialization units + writer + host snapshot/restore + integration. |

---

### Task 1: `StateStore` — fail-open load, concurrent-safe save

Creates `daemon/persistence.py` with `StateStore`: `load()` returns `dict | None` (fail-open on missing / unreadable / invalid-JSON / version-mismatch); `save(data)` serializes under an internal `threading.Lock` and writes to a **unique** temp in the same dir (`tempfile.mkstemp`) then `os.replace` — so the shutdown flush and the writer thread can never collide on one temp path and publish a torn file (§7). `atomicio.atomic_write_json` is deliberately NOT reused: its fixed `path + ".tmp"` is exactly the collision this fixes, so this is a new writer.

**Files:**
- Create: `src/sonari/daemon/persistence.py`
- Test: `tests/test_persistence.py` (new)

**Interfaces:**
- Produces: `STATE_VERSION: int = 1`; `StateStore(path)`; `StateStore.load() -> dict | None`; `StateStore.save(data: dict) -> None`. `save` writes `data` verbatim (the caller — `_snapshot_state`, Task 7 — includes `"version": STATE_VERSION`); `load` rejects any dict whose `version` != `STATE_VERSION`.

- [ ] **Step 1: Write the failing test** — create `tests/test_persistence.py`:

```python
"""SP6 restart-persistence suite: StateStore + serialization units + the writer +
host snapshot/restore + the restore->catch-up integration. Grows task-by-task."""
import json
import os
import threading

from sonari.daemon.persistence import StateStore, STATE_VERSION


def test_store_round_trips_a_versioned_dict(tmp_path):
    store = StateStore(tmp_path / "state.json")
    payload = {"version": STATE_VERSION, "hello": "world", "n": 3}
    store.save(payload)
    assert store.load() == payload


def test_store_load_missing_file_is_none(tmp_path):
    assert StateStore(tmp_path / "nope.json").load() is None


def test_store_load_invalid_json_is_none(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")
    assert StateStore(path).load() is None


def test_store_load_version_mismatch_is_none(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": STATE_VERSION + 999, "x": 1}),
                    encoding="utf-8")
    assert StateStore(path).load() is None


def test_store_load_non_dict_is_none(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert StateStore(path).load() is None


def test_concurrent_saves_never_tear_the_file(tmp_path):
    # Two threads hammering save() with distinct payloads must never leave the
    # file mid-write: every load() sees a complete, valid dict (unique temp +
    # internal lock + os.replace). This is the §7 CRITICAL corruption fix.
    store = StateStore(tmp_path / "state.json")

    def writer(tag):
        for i in range(200):
            store.save({"version": STATE_VERSION, "tag": tag, "i": i})

    a = threading.Thread(target=writer, args=("a",))
    b = threading.Thread(target=writer, args=("b",))
    a.start(); b.start(); a.join(); b.join()
    data = store.load()
    assert isinstance(data, dict) and data["version"] == STATE_VERSION
    # No leftover temp files in the directory (each save cleaned up).
    leftovers = [p for p in os.listdir(tmp_path) if p.endswith(".state.tmp")]
    assert leftovers == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'sonari.daemon.persistence'`.

- [ ] **Step 3: Create `src/sonari/daemon/persistence.py`** with `StateStore`:

```python
"""SP6 durable-state persistence: the single atomic JSON snapshot at
SONARI_DIR/state.json.

Two objects: StateStore (this file's load/save) and PersistenceWriter (the
off-lock debounced writer thread, added in Task 6). Both are I/O primitives with
no knowledge of the daemon's state shape — the host builds the dict.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading

# Serialized format version. A mismatch on load() => fail open (§8): the daemon
# boots empty rather than misreading a future/foreign schema. Reserves the
# migration seam without shipping migration.
STATE_VERSION = 1


class StateStore:
    """The durable-state file. load() is fail-open; save() is concurrent-safe.

    NOT a consumer of atomicio.atomic_write_json: that writer uses a FIXED
    `path + ".tmp"`, so the shutdown flush() and the writer thread would race on
    one temp path and publish a torn file, which load() then rejects and
    fail-opens to EMPTY — silently dropping the whole pile on the exact
    `sonari install` path SP6 protects (§7). save() below serializes under an
    internal lock AND writes a UNIQUE temp via tempfile.mkstemp, so overlapping
    saves can never collide.
    """

    def __init__(self, path) -> None:
        self._path = str(path)
        self._lock = threading.Lock()

    def load(self) -> "dict | None":
        """The persisted dict, or None on missing / unreadable / invalid-JSON /
        non-object / version-mismatch (fail-open: the daemon boots empty, §8)."""
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, ValueError, OSError):
            return None
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            return None
        return data

    def save(self, data: dict) -> None:
        """Serialize `data` to the state file atomically. Under an internal lock
        (so two callers serialize) and via a UNIQUE temp in the same dir + fsync
        + os.replace (so a concurrent save can never tear the published file)."""
        with self._lock:
            directory = os.path.dirname(self._path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, suffix=".state.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self._path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
```

- [ ] **Step 4: Run to verify it passes (full suite — the green gate)**

Run: `.venv/bin/python -m pytest -q`
Expected: `~1183 passed, 1 skipped` (1177 baseline + 6 new; no regressions is the bar — the new module has no importers yet).

- [ ] **Step 5: Commit**

`git add -A && git commit -m "feat(persistence): add StateStore with fail-open load and concurrent-safe save"`

---

### Task 2: `SessionHistory.to_state()` / `load_state()` — pile + counters + clock normalization

Serializes each session's entry deque (every `HistoryEntry`: `text`, `kind`, `msg_id`, `seq`, `turn_id`, `heard`, and its `stamp` converted to an absolute wall-clock `wall_stamp` per §5) plus the three load-bearing counters. `load_state` rebuilds each deque as `deque(iterable, maxlen=self._cap)` (keeps the newest cap when the saved pile exceeds the current cap), converts each `wall_stamp` back to a monotonic stamp on the running clock (clamped ≤ now, never future), and rebinds `self._clock` to the injected clock. Both `clock=` (monotonic) and `now=` (wall) are injectable so the stamp math is hermetically testable.

**Files:**
- Modify: `src/sonari/history.py` (add two methods to `SessionHistory`; `time`/`deque` already imported at lines 11–12)
- Test: `tests/test_persistence.py` (append)

**Interfaces:**
- Consumes: `HistoryEntry.__slots__ = ("text","kind","msg_id","seq","turn_id","heard","stamp")` (`history.py:16`); `HistoryEntry(text, kind, msg_id, seq=0, turn_id=0, stamp=0.0)`; `SessionHistory._cap`, `._clock`, `._entries: dict[str, deque]`, `._msg_id`, `._group_seq`, `._turn_id`.
- Produces: `SessionHistory.to_state(*, clock=None, now=None) -> dict` (keyed by session id → `{"msg_id","group_seq","turn_id","entries":[{...}]}`); `SessionHistory.load_state(data, *, clock=None, now=None) -> None`. Both default `clock`→`self._clock`, `now`→`time.time`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_persistence.py`:

```python
def test_history_state_round_trip_including_heard():
    from sonari.history import SessionHistory
    h = SessionHistory(cap=200)
    e0 = h.record("s1", "prose", "one"); h.end_message("s1")
    e1 = h.record("s1", "choice", "two")          # noqa: F841
    e0.heard = True
    state = json.loads(json.dumps(h.to_state()))  # must survive a JSON round-trip
    h2 = SessionHistory(cap=200)
    h2.load_state(state)
    got = list(h2._entries["s1"])
    assert [(e.text, e.kind, e.msg_id, e.seq, e.turn_id, e.heard) for e in got] == [
        ("one", "prose", 0, 0, 0, True),
        ("two", "choice", 1, 0, 0, False),
    ]
    assert h2._msg_id["s1"] == h._msg_id["s1"]
    assert h2._group_seq["s1"] == h._group_seq["s1"]
    assert h2._turn_id["s1"] == h._turn_id["s1"]


def test_history_clock_normalization_spans_downtime_hermetically():
    from sonari.history import SessionHistory
    mono = [1000.0]; wall = [50000.0]
    h = SessionHistory(clock=lambda: mono[0])
    h.record("s1", "prose", "x")                        # stamp == 1000.0
    state = h.to_state(clock=lambda: mono[0], now=lambda: wall[0])
    # Simulate a 1h downtime spanning a fresh, unrelated process monotonic clock:
    # the monotonic seam jumps (5000), wall advances by exactly 3600.
    mono2 = [5000.0]; wall2 = [50000.0 + 3600.0]
    h2 = SessionHistory()
    h2.load_state(state, clock=lambda: mono2[0], now=lambda: wall2[0])
    age = h2.unheard_age("s1")
    assert abs(age - 3600.0) < 1e-6                     # true elapsed incl downtime
    assert age >= 0                                     # never negative


def test_history_shrunk_cap_keeps_newest_and_frontier_ages_out():
    from sonari.history import SessionHistory
    h = SessionHistory(cap=5)
    for i in range(5):
        h.record("s1", "prose", "p{0}".format(i)); h.end_message("s1")  # msg 0..4
    state = h.to_state()
    h2 = SessionHistory(cap=3)                          # SHRUNK cap
    h2.load_state(state)
    assert [e.text for e in h2._entries["s1"]] == ["p2", "p3", "p4"]    # newest 3
    assert len(h2._entries["s1"]) == 3                  # maxlen honored, not unbounded
    entries, aged_out = h2.unheard_from_frontier("s1", (0, 0))
    assert aged_out is True                             # (0,0) behind oldest survivor (2,0)
    assert [e.text for e in entries] == ["p2", "p3", "p4"]  # tuple compare, no TypeError
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence.py -k history -q`
Expected: FAIL — `AttributeError: 'SessionHistory' object has no attribute 'to_state'`.

- [ ] **Step 3: Add the two methods** to `src/sonari/history.py`, immediately after `reset()` (after line 210, at the end of the `SessionHistory` class):

```python
    def to_state(self, *, clock=None, now=None) -> dict:
        """Serialize the durable pile per session: every entry (incl. heard) with
        its monotonic stamp converted to an absolute WALL-clock time, plus the
        three load-bearing counters. PURE (no I/O). `clock`/`now` are the
        monotonic and wall seams (default: this history's own clock + time.time),
        injectable so the stamp<->wall math is hermetically testable (§5)."""
        clk = clock if clock is not None else self._clock
        wall = now if now is not None else time.time
        mono_now = clk()
        wall_now = wall()
        out: dict = {}
        for session, d in self._entries.items():
            out[session] = {
                "msg_id": self._msg_id.get(session, 0),
                "group_seq": self._group_seq.get(session, 0),
                "turn_id": self._turn_id.get(session, 0),
                "entries": [
                    {"text": e.text, "kind": e.kind, "msg_id": e.msg_id,
                     "seq": e.seq, "turn_id": e.turn_id, "heard": e.heard,
                     "wall_stamp": wall_now - (mono_now - e.stamp)}
                    for e in d
                ],
            }
        return out

    def load_state(self, data, *, clock=None, now=None) -> None:
        """Rebuild the pile from to_state() output. Each deque is rebuilt at the
        CURRENT cap (deque(maxlen=self._cap), keeping the newest cap entries when
        the saved pile is larger). Each wall_stamp is converted back to a
        monotonic stamp on the RUNNING clock, CLAMPED <= now so it is never in the
        future, so unheard_age spans the downtime truthfully (§5). Rebinds
        self._clock to the injected clock so the mono_now2 capture here and later
        unheard_age reads use one clock. PURE."""
        clk = clock if clock is not None else self._clock
        wall = now if now is not None else time.time
        mono_now2 = clk()
        wall_now2 = wall()
        for session, sd in data.items():
            entries = deque(maxlen=self._cap)
            for ed in sd.get("entries", []):
                entry = HistoryEntry(
                    ed["text"], ed["kind"], ed["msg_id"], ed.get("seq", 0),
                    ed.get("turn_id", 0),
                    stamp=min(mono_now2,
                              mono_now2 - (wall_now2 - ed["wall_stamp"])))
                entry.heard = ed.get("heard", False)
                entries.append(entry)
            self._entries[session] = entries
            self._msg_id[session] = sd.get("msg_id", 0)
            self._group_seq[session] = sd.get("group_seq", 0)
            self._turn_id[session] = sd.get("turn_id", 0)
        self._clock = clk
```

- [ ] **Step 4: Run to verify it passes (full suite — the green gate)**

Run: `.venv/bin/python -m pytest -q`
Expected: `~1186 passed, 1 skipped` (the SP6 history tests pass and `tests/test_history.py` + guards stay green — `history.py` is shared production, so the whole suite gates this commit).

- [ ] **Step 5: Commit**

`git add -A && git commit -m "feat(history): serialize pile and counters with wall-clock stamp normalization"`

---

### Task 3: `SessionStream.to_state()` / `load_state()` — the frontier

Serializes the frontier only (the sole durable field on `SessionStream`; everything else is transient per §4.2). JSON has no tuple, so `to_state` writes a list and `load_state` converts it back to a `tuple` — otherwise `key > self.frontier` (tuple-vs-list) raises `TypeError` on the first frontier compare after restore (§6).

**Files:**
- Modify: `src/sonari/session_stream.py` (add two methods after `advance_frontier`, line 49)
- Test: `tests/test_persistence.py` (append)

**Interfaces:**
- Consumes: `SessionStream.frontier` (a `(msg_id, seq)` tuple or `None`, `session_stream.py:31`); `SessionStream.advance_frontier(key)`.
- Produces: `SessionStream.to_state() -> dict` (`{"frontier": [m, s] | None}`); `SessionStream.load_state(data) -> None`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_persistence.py`:

```python
def test_session_stream_frontier_round_trip_via_json():
    from sonari.session_stream import SessionStream
    st = SessionStream(); st.advance_frontier((3, 0))
    state = json.loads(json.dumps(st.to_state()))       # tuple -> list over JSON
    assert state == {"frontier": [3, 0]}
    st2 = SessionStream(); st2.load_state(state)
    assert st2.frontier == (3, 0) and isinstance(st2.frontier, tuple)
    assert (4, 0) > st2.frontier                         # tuple compare, no TypeError


def test_session_stream_none_frontier_round_trip():
    from sonari.session_stream import SessionStream
    st = SessionStream()                                 # frontier None
    st2 = SessionStream(); st2.load_state(st.to_state())
    assert st2.frontier is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence.py -k session_stream -q`
Expected: FAIL — `AttributeError: 'SessionStream' object has no attribute 'to_state'`.

- [ ] **Step 3: Add the two methods** to `src/sonari/session_stream.py`, at the end of the `SessionStream` class (after `advance_frontier`, line 49):

```python
    def to_state(self) -> dict:
        """Serialize the durable frontier (list form — JSON has no tuple). Only
        the frontier persists; every other field is transient (§4.2). PURE."""
        return {"frontier": list(self.frontier) if self.frontier is not None else None}

    def load_state(self, data) -> None:
        """Rehydrate the frontier, converting JSON's list back to a TUPLE so
        `key > self.frontier` (tuple-vs-tuple) never raises TypeError (§6). PURE."""
        f = data.get("frontier")
        self.frontier = tuple(f) if f is not None else None
```

- [ ] **Step 4: Run to verify it passes (full suite — the green gate)**

Run: `.venv/bin/python -m pytest -q`
Expected: `~1188 passed, 1 skipped` (SP6 stream tests pass; `session_stream.py` is shared production, guards + existing stream/frontier tests green).

- [ ] **Step 5: Commit**

`git add -A && git commit -m "feat(stream): serialize the frontier (list to tuple on load)"`

---

### Task 4: `SessionManager` state + provisional restore quarantine

Serializes the folder map + stable numbers. Adds `_provisional: set`, seeded with the restored ids on `load_state`. Adds `is_provisional(session)`. **`is_live()` fail-CLOSES for a provisional session** (returns `False`) — narrowly, leaving the existing fail-open for a normally-registered no-identity session unchanged. **`set_identity()` discards the session from `_provisional`** — unconditionally, so an empty-identity re-capture still lifts the quarantine (the clear-on-reidentify that ends the quarantine exactly when the pile becomes reachable, §4.4).

**Files:**
- Modify: `src/sonari/sessions.py` (`__init__` adds `_provisional`; `is_live` gate at line 233; `set_identity` first line at 176; add `to_state`/`load_state`/`is_provisional`)
- Test: `tests/test_persistence.py` (append)

**Interfaces:**
- Consumes: `SessionManager._sessions: dict[str, str|None]`, `._numbers: dict[str, int]`, `._identities`, `._tty_evicted`; `folder(session)`, `number(session)`, `identity(session)`, `session_ids()`, `set_identity(session, identity)`, `is_live(session)`; `ttyutil.tty_alive(tty)` (empty tty → True).
- Produces: `SessionManager.to_state() -> dict` (`{session_id: {"folder": str|None, "number": int|None}}`); `SessionManager.load_state(data) -> None`; `SessionManager.is_provisional(session) -> bool`; `SessionManager._provisional: set[str]`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_persistence.py`:

```python
def test_sessions_state_round_trip_and_provisional_seed():
    from sonari.sessions import SessionManager
    sm = SessionManager()
    sm.register("s1", cwd="/x/repo"); sm.register("s2", cwd="/x/other")
    state = json.loads(json.dumps(sm.to_state()))
    sm2 = SessionManager()
    sm2.load_state(state)
    assert sm2.folder("s1") == "repo" and sm2.number("s1") == sm.number("s1")
    assert sm2.folder("s2") == "other" and sm2.number("s2") == sm.number("s2")
    assert sm2.is_provisional("s1") and sm2.is_provisional("s2")
    assert sm2.identity("s1") is None                    # D2: identity NOT restored


def test_provisional_session_is_not_live():
    from sonari.sessions import SessionManager
    sm = SessionManager()
    sm.load_state({"s1": {"folder": "repo", "number": 1}})
    assert sm.is_live("s1") is False                     # fail-CLOSED while provisional


def test_set_identity_clears_provisional_and_restores_liveness():
    from sonari.sessions import SessionManager, Identity
    sm = SessionManager()
    sm.load_state({"s1": {"folder": "repo", "number": 1}})
    assert sm.is_live("s1") is False
    sm.set_identity("s1", Identity())                    # empty identity still clears it
    assert sm.is_provisional("s1") is False
    assert sm.is_live("s1") is True                      # empty tty -> fail-OPEN again
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence.py -k "sessions_state or provisional or set_identity_clears" -q`
Expected: FAIL — `AttributeError: 'SessionManager' object has no attribute 'to_state'`.

- [ ] **Step 3: Initialize `_provisional`** in `SessionManager.__init__` (`src/sonari/sessions.py`), immediately after the `_mru` field (line 72):

```python
        self._mru: "list[str]" = []
        # SP6 (§4.4): sessions restored from disk are QUARANTINED until they
        # re-capture a real identity this lifetime. Seeded by load_state, cleared
        # by set_identity. Empty for every normally-registered session, so the
        # is_live fail-close and the ⌃⌘W/chooser exclusions are no-ops off-restore.
        self._provisional: "set[str]" = set()
```

- [ ] **Step 4: Clear provisional in `set_identity`** — add the discard as the FIRST statement of the body (`src/sonari/sessions.py:186`, immediately after the docstring, before `if identity.tty:`):

```python
        First set on an absent session stores it as-is."""
        # SP6 (§4.4): re-capturing a real identity this lifetime lifts the
        # provisional quarantine — UNCONDITIONAL (even an all-empty best-effort
        # identity counts as "this session is back this lifetime"), so quarantine
        # ends exactly when the next prompt makes the pile reachable (§2).
        self._provisional.discard(session)
        if identity.tty:
```

- [ ] **Step 5: Fail-close `is_live` for provisional** — add the gate at the TOP of `is_live` (`src/sonari/sessions.py:237`, before the `_tty_evicted` check):

```python
    def is_live(self, session: str) -> bool:
        """True if *session*'s terminal is still open (its captured tty device node
        exists). Fail-open: an unknown identity or empty tty -> live (never hide a
        live session). Pure read over _identities; writes nothing."""
        if session in self._provisional:
            # SP6 (§4.4): fail-CLOSED while provisional — narrowly here, so a
            # terminal that closed during downtime is never a ghost the chooser or
            # ⌃⌘W could raise. The fail-OPEN below (normally-registered, no
            # identity yet) is unchanged.
            return False
        if session in self._tty_evicted:
```

- [ ] **Step 6: Add `to_state` / `load_state` / `is_provisional`** at the end of the `SessionManager` class (`src/sonari/sessions.py`, after `focused_session()`, line 301):

```python
    def to_state(self) -> dict:
        """Serialize the durable roster: folder label + stable number per
        session. Live pointers, identities, MRU, eviction are all transient
        (§4.2). PURE."""
        return {s: {"folder": self._sessions.get(s), "number": self._numbers.get(s)}
                for s in self._sessions}

    def load_state(self, data) -> None:
        """Rehydrate the roster and seed _provisional with every restored id
        (§4.4). Numbers are restored so digit teleports stay stable across the
        restart; a missing number is left unassigned (re-minted on demand). PURE."""
        for s, sd in data.items():
            self._sessions[s] = sd.get("folder")
            num = sd.get("number")
            if num is not None:
                self._numbers[s] = num
            self._provisional.add(s)

    def is_provisional(self, session: str) -> bool:
        """True for a restored-but-not-yet-reconfirmed session (§4.4). False for
        every session that has re-captured an identity, and for every session that
        was never restored."""
        return session in self._provisional
```

- [ ] **Step 7: Run to verify it passes (full suite — the green gate)**

Run: `.venv/bin/python -m pytest -q`
Expected: `~1191 passed, 1 skipped`. `sessions.py` is heavily shared, so the whole suite gates this: existing sessions/focus/chooser/whereami tests + guards stay green because `_provisional` is empty for every non-restored session, so `is_live`/`set_identity` behave exactly as before.

- [ ] **Step 8: Commit**

`git add -A && git commit -m "feat(sessions): serialize roster and quarantine restored sessions as provisional"`

---

### Task 5: Exclude provisional sessions from the ⌃⌘W Also-map

`control._also_clause` iterates `sessions.session_ids()` with only a `not in exclude` filter — no `is_live` gate — so a provisional restored session with a frozen pile would surface in the Also-map (the phantom §4.4 closes). Add a `not sessions.is_provisional(s)` gate to its `ids` comprehension. The chooser needs NO change: `chooser._snapshot` already filters candidates by `is_live()`, which Task 4 made fail-closed for provisional sessions — a verification test proves it.

**Files:**
- Modify: `src/sonari/daemon/features/control.py:88-89` (`_also_clause` `ids` comprehension)
- Test: `tests/test_persistence.py` (append)

**Interfaces:**
- Consumes: `SessionManager.is_provisional(session)` (Task 4); `SessionManager.session_ids()`, `number(session)`, `folder(session)`; `control._entry_clauses(host, session)`; `chooser._snapshot(sessions) -> (origin, candidates)`.
- Produces: no new symbols — a behavior narrowing of `control._also_clause`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_persistence.py`:

```python
def test_provisional_session_absent_from_where_am_i_also_map():
    from sonari.sessions import Identity
    from sonari.daemon.features import control
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"s1": {"folder": "repo", "number": 1}})  # provisional
    daemon._stream("s1")                                  # create its stream (frontier None)
    daemon.history.record("s1", "prose", "unheard pile")  # non-empty clause otherwise
    assert control._also_clause(daemon) == ""             # provisional -> excluded entirely
    daemon.sessions.set_identity("s1", Identity())        # clears provisional
    out = control._also_clause(daemon)
    assert "repo" in out and "unheard" in out             # now visible, named + counted


def test_provisional_session_absent_from_chooser_snapshot():
    from sonari.daemon.features import chooser
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.load_state({"s1": {"folder": "repo", "number": 1}})
    _origin, candidates = chooser._snapshot(daemon.sessions)
    assert "s1" not in candidates                         # is_live False -> filtered; no chooser edit
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence.py -k "also_map or chooser_snapshot" -q`
Expected: `test_provisional_session_absent_from_where_am_i_also_map` FAILS (the provisional session IS listed, so `_also_clause(daemon) != ""`); `test_provisional_session_absent_from_chooser_snapshot` PASSES already (proving the chooser needs no change).

- [ ] **Step 3: Add the provisional gate** to `_also_clause` (`src/sonari/daemon/features/control.py`), in the `ids` comprehension (lines 87-89):

```python
    sessions = host.sessions
    ids = sorted((s for s in sessions.session_ids()
                  if s not in exclude and not sessions.is_provisional(s)),
                 key=lambda s: sessions.number(s) or 0)
```

- [ ] **Step 4: Run to verify it passes (full suite — the green gate)**

Run: `.venv/bin/python -m pytest -q`
Expected: `~1193 passed, 1 skipped`. `control.py` is shared, so the whole suite gates this: both SP6 tests pass and every `tests/test_whereami_v2.py` case + guards stay green (no registered session is ever provisional in those tests, so the new gate is a no-op there).

- [ ] **Step 5: Commit**

`git add -A && git commit -m "fix(whereami): exclude provisional sessions from the Also-map"`

---

### Task 6: `PersistenceWriter` — debounced, off-lock writer thread

Adds `PersistenceWriter` to `daemon/persistence.py`: a thread + a `threading.Event` dirty flag. `mark_dirty()` = `dirty.set()` (non-blocking, no lock, no I/O). The loop `dirty.wait()` → `sleep(debounce)` to coalesce → `dirty.clear()` → build snapshot **under the passed lock** → `store.save()` **outside** the lock; it never raises out. `flush()` is a synchronous snapshot+save for shutdown. `start()`/`stop()` (stop unblocks + JOINs). `sleep` is injectable for deterministic tests; `clock` is the reserved monotonic seam (the Event+sleep coalesce consumes only `sleep`).

**Files:**
- Modify: `src/sonari/daemon/persistence.py` (add `PersistenceWriter`; add `import threading`/`import time` as needed — `threading` already imported, add `time`)
- Test: `tests/test_persistence.py` (append)

**Interfaces:**
- Consumes: `StateStore.save(data)` (Task 1); a `snapshot_fn` callable that returns a dict and is invoked WITH `lock` held (it must NOT acquire `lock`); a `threading.Lock`.
- Produces: `PersistenceWriter(store, snapshot_fn, lock, *, debounce=1.0, clock=time.monotonic, sleep=time.sleep)`; `.mark_dirty() -> None`; `.start() -> None`; `.stop() -> None` (joins); `.flush() -> None`; internal seam `._run_one_cycle() -> bool` (one debounce+save cycle; unit-testable without a running thread). Contract: `snapshot_fn` runs under the lock; a `store.save` fault never propagates.

- [ ] **Step 1: Write the failing test** — append to `tests/test_persistence.py`:

```python
class _CountingStore:
    def __init__(self):
        self.saves = 0
        self.last = None

    def save(self, data):
        self.saves += 1
        self.last = data


class _RaisingStore:
    def save(self, data):
        raise RuntimeError("disk full")


def test_writer_snapshot_runs_under_the_lock():
    from sonari.daemon.persistence import PersistenceWriter
    store = _CountingStore()
    lock = threading.Lock()

    def snap():
        assert lock.locked() is True            # snapshot MUST be built under the lock
        return {"version": STATE_VERSION, "n": 7}

    writer = PersistenceWriter(store, snap, lock)
    writer.flush()                               # synchronous snapshot + save
    assert store.saves == 1
    assert store.last == {"version": STATE_VERSION, "n": 7}


def test_writer_coalesces_a_burst_into_one_save():
    from sonari.daemon.persistence import PersistenceWriter
    store = _CountingStore()
    lock = threading.Lock()
    writer = PersistenceWriter(store, lambda: {"version": STATE_VERSION},
                               lock, debounce=0.0, sleep=lambda _s: None)
    writer._running.set()                        # arm without launching the thread
    for _ in range(5):
        writer.mark_dirty()                      # 5 marks -> one set Event
    assert writer._run_one_cycle() is True
    assert store.saves == 1                      # coalesced: 5 marks -> 1 save
    assert not writer._dirty.is_set()            # drained


def test_writer_flush_swallows_a_raising_save():
    from sonari.daemon.persistence import PersistenceWriter
    writer = PersistenceWriter(_RaisingStore(), lambda: {"version": STATE_VERSION},
                               threading.Lock())
    writer.flush()                               # must NOT raise


def test_mark_dirty_acquires_no_lock():
    from sonari.daemon.persistence import PersistenceWriter
    lock = threading.Lock()
    writer = PersistenceWriter(_CountingStore(), lambda: {}, lock)
    with lock:                                   # hold the lock the writer was given
        writer.mark_dirty()                      # must not block/deadlock on it
    assert writer._dirty.is_set()


def test_writer_stop_joins_a_started_thread():
    from sonari.daemon.persistence import PersistenceWriter
    store = _CountingStore()
    writer = PersistenceWriter(store, lambda: {"version": STATE_VERSION},
                               threading.Lock(), debounce=0.0)
    writer.start()
    writer.mark_dirty()
    writer.stop()                                # unblocks the wait + joins
    assert writer._thread is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence.py -k writer -q` (plus `-k mark_dirty`)
Expected: FAIL — `ImportError: cannot import name 'PersistenceWriter' from 'sonari.daemon.persistence'`.

- [ ] **Step 3: Add `import time`** to `src/sonari/daemon/persistence.py` (after `import tempfile`, line ~12):

```python
import tempfile
import threading
import time
```

- [ ] **Step 4: Add `PersistenceWriter`** at the end of `src/sonari/daemon/persistence.py`:

```python
class PersistenceWriter:
    """Off-lock, debounced state writer. A dirty Event coalesces bursts of
    mark_dirty() into a small bounded number of saves. The snapshot is built
    UNDER the daemon lock (passed in) and the disk write happens OUTSIDE it, so no
    I/O ever runs under self._lock (the load-bearing perf rule, §7/§12).

    snapshot_fn is invoked with `lock` held and MUST NOT acquire `lock` itself
    (threading.Lock is non-reentrant) — it is the host's _snapshot_state, a
    lock-free builder that reads state under the lock the writer holds.
    """

    def __init__(self, store, snapshot_fn, lock, *, debounce: float = 1.0,
                 clock=time.monotonic, sleep=time.sleep) -> None:
        self._store = store
        self._snapshot_fn = snapshot_fn
        self._lock = lock
        self._debounce = debounce
        self._clock = clock          # reserved monotonic seam (§9); coalesce uses _sleep
        self._sleep = sleep
        self._dirty = threading.Event()
        self._running = threading.Event()
        self._thread = None

    def mark_dirty(self) -> None:
        """Arm a save. NON-BLOCKING: sets an Event and returns. Acquires no lock
        and does no I/O, so it is safe on the hot path and under self._lock."""
        self._dirty.set()

    def start(self) -> None:
        self._running.set()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running.is_set():
            self._run_one_cycle()

    def _run_one_cycle(self) -> bool:
        """One debounce+save cycle: block for dirty, coalesce a burst, clear,
        snapshot, save. Returns False when woken for shutdown (nothing saved),
        else True. A late mark_dirty landing after clear() re-arms the Event for a
        redundant next cycle, so no steady-state mutation is dropped (§7)."""
        self._dirty.wait()
        if not self._running.is_set():
            return False
        self._sleep(self._debounce)          # coalesce a burst into one save
        self._dirty.clear()
        self._save_once()
        return True

    def _save_once(self) -> None:
        """Build the snapshot UNDER the lock, write OUTSIDE it. Never propagates a
        fault — a failed save must not kill the writer or the shutdown flush."""
        try:
            with self._lock:
                data = self._snapshot_fn()
            self._store.save(data)
        except Exception:  # noqa: BLE001 - a save fault must never propagate
            pass

    def flush(self) -> None:
        """Synchronous snapshot + save for shutdown (§7). Same off-lock
        discipline as the loop; used after the writer thread is joined."""
        self._save_once()

    def stop(self) -> None:
        """Stop the loop and JOIN the thread (§7 shutdown contract). Idempotent:
        a never-started writer just clears the flags."""
        self._running.clear()
        self._dirty.set()                    # unblock a waiting _run_one_cycle
        if self._thread is not None:
            self._thread.join()
            self._thread = None
```

- [ ] **Step 5: Run to verify it passes (full suite — the green gate)**

Run: `.venv/bin/python -m pytest -q`
Expected: `~1198 passed, 1 skipped` (all SP6 tests to date pass; the new class has no production importers yet, so no regression is possible, but run the whole suite to keep the every-commit gate uniform).

- [ ] **Step 6: Commit**

`git add -A && git commit -m "feat(persistence): add debounced off-lock PersistenceWriter"`

---

### Task 7: Host `_snapshot_state()` + `_restore_state()`

Adds the store to `SpeechDaemon.__init__` (path read live so conftest redirects it), plus `_snapshot_state()` (a lock-free builder invoked under `self._lock` by the writer/flush — reads every `HistoryEntry` field under the lock so a concurrent heard-flip can't tear the read) and `_restore_state()` (`store.load()` → `None` no-op; else drop stale sessions, apply `history.load_state`, create a `SessionStream` per restored frontier, `sessions.load_state`, set `_next_id`; fail-open to empty; NEVER touches `self.speaker`). Adds `restore_max_age_hours: 24` to `DEFAULTS` and updates the pinned key-set test.

**Files:**
- Modify: `src/sonari/config.py:9-23` (`DEFAULTS`) and `tests/test_config.py:6-20` (pinned key set)
- Modify: `src/sonari/daemon/host.py` (add the persistence import; `__init__` constructs `self._store`; add `_snapshot_state` + `_restore_state`)
- Test: `tests/test_persistence.py` (append)

**Interfaces:**
- Consumes: `StateStore` / `STATE_VERSION` (Task 1); `SessionHistory.to_state`/`load_state` (Task 2); `SessionStream.to_state`/`load_state` (Task 3); `SessionManager.to_state`/`load_state` (Task 4); `self._state._streams`, `self._state._next_id`, `self._backlog_cap`, `self.history`, `self.sessions`, `self.config`; `SessionStream(queue_cap=...)` (imported at `host.py:11`).
- Produces: `SpeechDaemon._store: StateStore`; `SpeechDaemon._snapshot_state() -> dict` (caller holds `self._lock`; no I/O, no lock acquired); `SpeechDaemon._restore_state() -> None` (fail-open). Snapshot dict shape: `{"version","saved_wall","next_id","sessions","streams","history"}`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_persistence.py`:

```python
def test_snapshot_restore_round_trip_and_behavior_decisions():
    from tests.daemon_helpers import make_daemon
    # Source daemon: a real pile, a partway frontier, a bumped id counter, and
    # TRANSIENT state (a held stop + a global voice-state) that must NOT survive.
    src, *_ = make_daemon(foreground=None)
    src.sessions.register("s1", cwd="/x/repo")
    src.history.record("s1", "prose", "one"); src.history.end_message("s1")
    src.history.record("s1", "prose", "two")
    src._stream("s1").advance_frontier((0, 0))
    src._stream("s1").stopped = True             # D1: a held stop
    src.voice_state = "stopped-all"              # D1: a global hold
    src._state._next_id = 41
    with src._lock:
        data = src._snapshot_state()
    src._store.save(data)

    # Fresh daemon restores from the same (isolated) SONARI_DIR/state.json.
    dst, _q, speaker, sessions, _c = make_daemon(foreground=None)
    dst._restore_state()

    assert [e.text for e in dst.history.unheard("s1")] == ["one", "two"]
    assert dst._streams["s1"].frontier == (0, 0)
    assert dst._state._next_id == 41
    assert sessions.folder("s1") == "repo" and sessions.number("s1") == 1
    # D1: a held stop does NOT survive restart.
    assert dst.voice_state == "flowing"
    assert dst._streams["s1"].stopped is False
    # D2 + §4.4: identity NOT restored; the session is provisional.
    assert sessions.identity("s1") is None
    assert sessions.is_provisional("s1") is True
    # BOOT_CUE safety: restore never touched the speaker (can't swallow/dup the cue).
    assert speaker.spoken == [] and speaker.earcons == [] and speaker.cancels == 0


def test_restore_fail_open_on_corrupt_and_version_mismatch():
    from tests.daemon_helpers import make_daemon
    for content in ("{ not json", json.dumps({"version": 999, "sessions": {}})):
        daemon, _q, speaker, sessions, _c = make_daemon(foreground=None)
        os.makedirs(os.path.dirname(daemon._store._path), exist_ok=True)
        with open(daemon._store._path, "w", encoding="utf-8") as fh:
            fh.write(content)
        daemon._restore_state()                  # must NOT raise
        assert sessions.session_ids() == []      # booted empty
        assert dict(daemon._streams) == {}
        assert speaker.spoken == [] and speaker.earcons == []


def test_restore_missing_file_is_a_noop():
    from tests.daemon_helpers import make_daemon
    daemon, _q, _sp, sessions, _c = make_daemon(foreground=None)
    daemon._restore_state()                      # no state.json exists
    assert sessions.session_ids() == []


def test_restore_drops_a_stale_pile_and_keeps_a_fresh_one():
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground=None)
    saved = 1_000_000.0
    stale = {
        "version": STATE_VERSION, "saved_wall": saved, "next_id": 3,
        "sessions": {"old": {"folder": "old", "number": 1},
                     "fresh": {"folder": "fresh", "number": 2}},
        "streams": {},
        "history": {
            "old": {"msg_id": 0, "group_seq": 1, "turn_id": 0, "entries": [
                {"text": "x", "kind": "prose", "msg_id": 0, "seq": 0, "turn_id": 0,
                 "heard": False, "wall_stamp": saved - 25 * 3600}]},   # 25h > 24h
            "fresh": {"msg_id": 0, "group_seq": 1, "turn_id": 0, "entries": [
                {"text": "y", "kind": "prose", "msg_id": 0, "seq": 0, "turn_id": 0,
                 "heard": False, "wall_stamp": saved - 1 * 3600}]},     # 1h < 24h
        },
    }
    daemon._store.save(stale)
    daemon._restore_state()
    assert "old" not in daemon.sessions.session_ids()          # dropped
    assert "fresh" in daemon.sessions.session_ids()            # kept
    assert daemon.history.unheard("old") == []
    assert [e.text for e in daemon.history.unheard("fresh")] == ["y"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence.py -k "snapshot_restore or fail_open or missing_file or stale_pile" -q`
Expected: FAIL — `AttributeError: 'SpeechDaemon' object has no attribute '_store'` (and `_snapshot_state`/`_restore_state`).

- [ ] **Step 3: Add `restore_max_age_hours` to `DEFAULTS`** (`src/sonari/config.py`), after the last key (line 22):

```python
    "summary_model": "haiku",    # claude -p --model for the summary (owner override)
    "restore_max_age_hours": 24, # SP6: max age (h) of a restored pile before drop-on-load (§4.4)
}
```

- [ ] **Step 4: Update the pinned `DEFAULTS` key set** (`tests/test_config.py:19`), add the key inside the asserted set:

```python
        "summary_model",
        "restore_max_age_hours",
    }
```

- [ ] **Step 5: Add the persistence import** to `src/sonari/daemon/host.py`, after line 18:

```python
from sonari.daemon.limits import RATE_MIN, RATE_MAX, MINQUEUE_MIN, MINQUEUE_MAX
from sonari.daemon.persistence import StateStore, STATE_VERSION
```

- [ ] **Step 6: Construct the store** in `SpeechDaemon.__init__` (`src/sonari/daemon/host.py`), after `self._voices_cache = None` (line 120):

```python
        self._voices_cache = None
        # SP6 persistence store: the durable-state file. Path read LIVE (import
        # SONARI_DIR here, not at module top) so conftest's per-test redirect and
        # any SONARI_DIR override take effect — matches _arm_faulthandler's pattern.
        from sonari.paths import SONARI_DIR
        self._store = StateStore(SONARI_DIR / "state.json")
```

- [ ] **Step 7: Add `_snapshot_state` and `_restore_state`** to `SpeechDaemon` (`src/sonari/daemon/host.py`), immediately before `def run(self)` (line 743):

```python
    def _snapshot_state(self) -> dict:
        """Build the JSON-shaped durable-state dict for persistence. The CALLER
        HOLDS self._lock (the PersistenceWriter loop / flush() wraps this call),
        so every HistoryEntry field is read under the lock and a concurrent
        heard-flip can't tear the read (§7). Does NO I/O and acquires NO lock
        itself. Only streams with a live frontier are serialized (the frontier is
        a stream's sole durable field)."""
        streams = {sid: st.to_state()
                   for sid, st in self._state._streams.items()
                   if st.frontier is not None}
        return {
            "version": STATE_VERSION,
            "saved_wall": time.time(),         # bounded-staleness reference (§4.4)
            "next_id": self._state._next_id,   # continuity nicety (§4.1)
            "sessions": self.sessions.to_state(),
            "streams": streams,
            "history": self.history.to_state(),
        }

    def _restore_state(self) -> None:
        """Re-hydrate history / streams / roster from self._store, single-threaded
        at boot BEFORE any other actor exists (§8). Fail-OPEN: a missing / corrupt
        / version-mismatched file (load() -> None) or ANY exception leaves the
        daemon empty. NEVER touches self.speaker, so it can neither swallow nor
        duplicate BOOT_CUE (emitted separately in bootstrap.main())."""
        try:
            data = self._store.load()
            if data is None:
                return
            hist = dict(data.get("history", {}))
            streams = dict(data.get("streams", {}))
            roster = dict(data.get("sessions", {}))
            # Bounded-staleness drop-on-load (§4.4): a pile whose newest entry was
            # older than restore_max_age_hours AT THE LAST SAVE (saved_wall
            # advances every save, so a pile untouched across restarts eventually
            # trips this) is a long-dead ghost — drop it from every map so it
            # never resurrects and never inflates the provisional set / numbers.
            saved_wall = data.get("saved_wall")
            max_age_s = float(self.config.get("restore_max_age_hours", 24)) * 3600.0
            if saved_wall is not None:
                stale = set()
                for sid, sd in hist.items():
                    ents = sd.get("entries") or []
                    newest = ents[-1].get("wall_stamp", saved_wall) if ents else saved_wall
                    if (saved_wall - newest) > max_age_s:
                        stale.add(sid)
                for sid in stale:
                    hist.pop(sid, None)
                    streams.pop(sid, None)
                    roster.pop(sid, None)
            # History rebuilt at the LIVE cap; clock/now default to this history's
            # own monotonic clock + time.time (production normalization, §5).
            self.history.load_state(hist)
            # One SessionStream per restored frontier, set directly on the ledger.
            for sid, sd in streams.items():
                st = SessionStream(queue_cap=self._backlog_cap)
                st.load_state(sd)
                self._state._streams[sid] = st
            # Roster (folder + number), seeding the provisional quarantine (§4.4).
            self.sessions.load_state(roster)
            # SpeechItem id continuity nicety (§4.1); fail-open to 0.
            self._state._next_id = data.get("next_id", 0)
        except Exception:  # noqa: BLE001 - fail-open to empty state (§8)
            pass
```

- [ ] **Step 8: Run to verify it passes (full suite — the green gate)**

Run: `.venv/bin/python -m pytest -q`
Expected: `~1202 passed, 1 skipped`. `host.py` + `config.py` are shared, so the whole suite gates this: SP6 host-restore tests pass, `tests/test_config.py` is green with the extended key set, and the guards stay green (`__init__` now builds a `StateStore` but starts no thread and does no I/O).

- [ ] **Step 9: Commit**

`git add -A && git commit -m "feat(daemon): snapshot and restore durable state"`

---

### Task 8: Host wiring — boot restore, dispatch + speak-loop hooks, shutdown flush

Constructs the `PersistenceWriter` in `__init__` (thread NOT started). Calls `mark_dirty()` at the two hook points: inside `handle_message()` (the single dispatch chokepoint funnelling socket, hotkey, and catch-up messages — so hotkey-only durable mutations like SKIP_PILE's frontier advance and FLUSH's counter bumps still persist) and at the `note_spoken()` completion (where `heard` flips and the frontier advances). Calls `_restore_state()` in `run()` BEFORE any other actor, then `persistence.start()`; the shutdown `finally` does `persistence.stop()` (join the writer) → `speak_thread.join()` (quiesce) → `persistence.flush()` (final synchronous save).

**Files:**
- Modify: `src/sonari/daemon/host.py` (import; `__init__` constructs `self._persistence`; `handle_message` line 442; `note_spoken` line 377; `run()` lines 743-767)
- Test: `tests/test_persistence.py` (append)

**Interfaces:**
- Consumes: `PersistenceWriter` (Task 6); `self._store` + `_snapshot_state` (Task 7); `self._lock`; existing `handle_message`, `note_spoken`, `run`, `stop`.
- Produces: `SpeechDaemon._persistence: PersistenceWriter`; `handle_message` and `note_spoken` mark dirty; `run()` restores-before-serve, starts the writer, and stops+joins+flushes on shutdown.

- [ ] **Step 1: Write the failing test** — append to `tests/test_persistence.py`:

```python
def _hotkey(daemon, mtype, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    daemon._dispatch_hotkey({"v": PROTOCOL_VERSION, "type": mtype,
                             "session": session, **kw})


def test_store_targets_the_isolated_sonari_dir():
    from sonari.paths import SONARI_DIR
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground=None)
    assert daemon._store._path == str(SONARI_DIR / "state.json")
    assert daemon._persistence is not None            # writer constructed, not started


def test_hotkey_only_skip_pile_marks_dirty():
    from sonari.protocol import MsgType
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground="s0")
    daemon.sessions.register("s0", cwd="/x/s0")
    daemon.history.record("s0", "prose", "a pile")    # something to skip
    daemon._persistence._dirty.clear()
    _hotkey(daemon, MsgType.SKIP_PILE, "s0")
    assert daemon._persistence._dirty.is_set()        # frontier advance persisted


def test_hotkey_only_flush_marks_dirty():
    from sonari.protocol import MsgType
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground="s0")
    daemon._persistence._dirty.clear()
    _hotkey(daemon, MsgType.FLUSH, "s0")
    assert daemon._persistence._dirty.is_set()        # turn/msg counter bump persisted


def test_note_spoken_marks_dirty():
    from sonari.queue import SpeechItem
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground="s0")
    it = SpeechItem(id=1, session="s0", kind="prose", text="x", is_decision=False)
    daemon._current_item = it
    daemon._persistence._dirty.clear()
    daemon.note_spoken(it, completed=True)
    assert daemon._persistence._dirty.is_set()


def test_flush_persists_the_last_delta():
    from sonari.daemon.persistence import StateStore
    from tests.daemon_helpers import make_daemon
    daemon, *_ = make_daemon(foreground=None)
    daemon.sessions.register("s1", cwd="/x/repo")
    daemon.history.record("s1", "prose", "hello")
    daemon._persistence.flush()                        # the shutdown write
    data = StateStore(daemon._store._path).load()      # reload via a fresh store
    assert data is not None
    assert data["sessions"]["s1"]["folder"] == "repo"
    assert data["history"]["s1"]["entries"][0]["text"] == "hello"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_persistence.py -k "targets_the_isolated or marks_dirty or last_delta" -q`
Expected: FAIL — `AttributeError: 'SpeechDaemon' object has no attribute '_persistence'`.

- [ ] **Step 3: Extend the persistence import** (`src/sonari/daemon/host.py`, the line added in Task 7):

```python
from sonari.daemon.persistence import StateStore, PersistenceWriter, STATE_VERSION
```

- [ ] **Step 4: Construct the writer** in `__init__` (`src/sonari/daemon/host.py`), immediately after the `self._store = ...` line added in Task 7:

```python
        self._store = StateStore(SONARI_DIR / "state.json")
        # SP6 off-lock writer: snapshots under self._lock, writes outside it. The
        # thread is NOT started here — run() starts it AFTER restore (§8).
        self._persistence = PersistenceWriter(
            self._store, self._snapshot_state, self._lock)
```

- [ ] **Step 5: Hook `mark_dirty` into `handle_message`** (`src/sonari/daemon/host.py:442-444`) — mark on EVERY dispatched message via `finally` (over-marking read-only messages is harmless; the writer coalesces), so hotkey-only durable mutations are never missed:

```python
    def handle_message(self, msg):
        self._ctx.bind(msg)
        try:
            return dispatch(self._ctx, msg)
        finally:
            # SP6: the single dispatch chokepoint (socket / hotkey / catch-up all
            # funnel here). mark_dirty is a non-blocking Event.set — safe under the
            # transaction lock the three callers hold (§7).
            self._persistence.mark_dirty()
```

- [ ] **Step 6: Hook `mark_dirty` into `note_spoken`** (`src/sonari/daemon/host.py`) — add as the last statement of the method, AFTER the `with self._lock:` block closes (after line 377):

```python
                    if not cu.get("ended"):
                        self._burn_catchup(cu)
                    self._catchup = None
        # SP6: the speak-loop completion hook — heard flipped and/or the frontier
        # advanced above. Outside the lock (mark_dirty takes none).
        self._persistence.mark_dirty()
```

- [ ] **Step 7: Wire `run()`** (`src/sonari/daemon/host.py:743-767`) — restore before any actor, start the writer, and stop+join+flush on shutdown:

```python
    def run(self) -> None:
        ensure_sonari_dir()
        # SP6: restore is single-threaded, BEFORE the daemon is discoverable and
        # before any speak/accept/hotkey thread can touch state (§8). Takes no
        # lock; this ordering is what keeps it torn-state-free.
        self._restore_state()
        self._persistence.start()
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
            # SP6 shutdown contract (§7): join the writer, then quiesce the speak
            # thread (so a final note_spoken can't land after the snapshot), THEN
            # the final synchronous flush.
            self._persistence.stop()
            speak_thread.join(timeout=5.0)
            self._persistence.flush()
            try:
                os.unlink(LOCK_PATH)
            except FileNotFoundError:
                pass
```

- [ ] **Step 8: Run to verify it passes, then the full suite + guards**

Run: `.venv/bin/python -m pytest tests/test_persistence.py -q`
Expected: all SP6 tests pass.
Run: `.venv/bin/python -m pytest tests/test_concurrency_guards.py -q`
Expected: `4 passed` (guards green — `mark_dirty` is a no-op Event set while the writer is unstarted; it acquires no `self._lock`).
Run: `.venv/bin/python -m pytest -q`
Expected: `1207 passed, 1 skipped` (1177 baseline + 30 new SP6 tests through Task 8; adjust to the actual count — no regressions is the bar). This task is the ONLY one touching the shared dispatch/speak-loop paths, so the full suite runs here.

- [ ] **Step 9: Commit**

`git add -A && git commit -m "feat(daemon): wire persistence into boot, dispatch, and shutdown"`

---

### Task 9: Integration — restore → catch-up + provisional visibility

Proves the whole loop with the real handlers and the sync-harness idiom: restore a pile with a partway frontier, confirm the restored session is invisible to ⌃⌘W while provisional, simulate the session's next prompt (a SET_FOREGROUND carrying a tty — the ONLY provisional-clear trigger — which sets the workspace pointer AND re-captures identity), then ⌃⌘L catch-up reads the frontier'd TAIL (not the whole pile) and ⌃⌘W reflects the restored unheard.

**Files:**
- Test: `tests/test_persistence.py` (append) — no source changes.

**Interfaces:**
- Consumes: `SpeechDaemon._restore_state` / `_snapshot_state` / `_store` (Task 7); `control._also_clause` / `_entry_clauses` (Task 5); the real `on_set_foreground` (SET_FOREGROUND, `lifecycle.py:56`) and `on_catch_up` (CATCH_UP, `catchup.py:25`); `make_daemon` (config `summarizer="off"` → catch-up floors to the ack without a real `claude`).

- [ ] **Step 1: Write the failing test** — append to `tests/test_persistence.py`:

```python
def test_restore_pile_becomes_catchable_and_provisional_until_reidentified():
    from sonari.protocol import MsgType, PROTOCOL_VERSION
    from sonari.daemon.features import control
    from tests.daemon_helpers import make_daemon

    # Source: session s1 with a 4-message pile; frontier dealt-with through msg 1,
    # so the catch-up tail is msg 2 + msg 3 (2 items), never the whole 4.
    src, *_ = make_daemon(foreground=None)
    src.sessions.register("s1", cwd="/x/repo")
    for i in range(4):
        src.history.record("s1", "prose", "p{0}".format(i)); src.history.end_message("s1")
    src._stream("s1").advance_frontier((1, 0))
    src._state._next_id = 9
    with src._lock:
        data = src._snapshot_state()
    src._store.save(data)

    # Restore into a fresh daemon (same isolated SONARI_DIR/state.json).
    dst, _q, _sp, sessions, _c = make_daemon(foreground=None)
    dst._restore_state()
    assert sessions.is_provisional("s1") is True
    assert sessions.identity("s1") is None                 # D2

    # Provisional => invisible to the ⌃⌘W Also-map.
    assert "repo" not in control._also_clause(dst)

    # The session's next prompt: SET_FOREGROUND WITH a tty (the provisional-clear
    # trigger) — sets the workspace pointer AND re-captures identity.
    with dst._state.transaction():
        dst.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.SET_FOREGROUND,
                            "session": "s1", "cwd": "/x/repo", "tty": "/dev/ttys404"})
    assert sessions.is_provisional("s1") is False
    assert sessions.workspace() == "s1"

    # ⌃⌘L catch-up reads the FRONTIER'd tail (2 items), not the whole restored pile.
    with dst._state.transaction():
        dst.handle_message({"v": PROTOCOL_VERSION, "type": MsgType.CATCH_UP,
                            "session": "s1"})
    acks = [it.text for it in dst._stream("s1").queue._items if "Catching up" in it.text]
    assert acks == ["Catching up 2 items in repo."]

    # ⌃⌘W now reflects the restored unheard for the (now non-provisional) session.
    assert "unheard" in control._entry_clauses(dst, "s1")
```

- [ ] **Step 2: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_persistence.py -k restore_pile_becomes -q`
Expected: `1 passed` (all the machinery exists after Tasks 1–8; this is a pure integration assertion, so it passes on first run — that is the point of the task).

- [ ] **Step 3: Run the full suite + guards a final time**

Run: `.venv/bin/python -m pytest -q`
Expected: `1208 passed, 1 skipped` (baseline 1177 + 31 SP6 tests; adjust to the actual count — no regressions is the bar).
Run: `.venv/bin/python -m pytest tests/test_concurrency_guards.py -q`
Expected: `4 passed`.

- [ ] **Step 4: Commit**

`git add -A && git commit -m "test(persistence): restore-to-catch-up integration and provisional visibility"`

---

## Self-review (writing-plans checklist — done by the author, fixes applied inline)

- **Spec-section coverage:** §3 single JSON snapshot → StateStore (T1). §4.1 durable facts → history/stream/sessions serialization (T2/T3/T4) + `_snapshot_state` (T7). §4.2 transient-not-restored → T7 restores only history/frontier/roster/next_id (nothing else). §4.3 D1 (held stop → flowing) + D2 (identity not restored) → asserted in T7 + T4. §4.4 provisional + drop-on-load → T4/T5/T7. §5 clock normalization → T2 (both seams injectable, hermetic downtime test). §6 format incl. frontier list↔tuple → T3/T7. §7 mechanism (mark_dirty chokepoint, off-lock snapshot, unique-temp, stop+join+quiesce+flush) → T1/T6/T8. §8 boot/restore ordering + fail-open → T7/T8. §9 unit boundaries → the task split itself. §10 testing strategy → every listed test is assigned. §12 contracts → Global Constraints + guards run every task.
- **Placeholder scan:** no `...`, no "similar to Task N", no "add error handling" — every code block is complete and paste-ready.
- **Type/name consistency:** `to_state`/`load_state` identical across T2–T4 and T7; `_snapshot_state`/`_restore_state`/`_store`/`_persistence`/`mark_dirty`/`is_provisional`/`_provisional`/`STATE_VERSION` used verbatim everywhere; snapshot dict keys (`version`/`saved_wall`/`next_id`/`sessions`/`streams`/`history`) match §6 and the hand-built test fixtures.
- **Lock discipline pinned:** T6's fake `snapshot_fn` asserts `lock.locked() is True`, forcing T7's `_snapshot_state` to stay lock-free (a later hand adding `with self._lock` inside it would deadlock the non-reentrant lock — and this test would catch the contract drift first).

---

## PLAN AUTHOR NOTES

Assumptions, real-code surprises, and resolved ambiguities (none change a locked decision):

1. **Lock placement (spec §7 vs §9 reconciliation).** §9's `PersistenceWriter(store, snapshot_fn, lock, ...)` lists `lock`; since `threading.Lock` is non-reentrant, the only deadlock-free way to *use* it is: the writer does `with lock: data = snapshot_fn()` then `store.save(data)` outside — which forces `_snapshot_state` to be **lock-free** (assumes the lock is held). This satisfies §7 literally ("built under self._lock", "released before any write") and keeps `lock` a real parameter (the self-locking reading would make `lock` vestigial). T6 pins the contract with a `lock.locked()` assertion. No decision changed.

2. **Staleness formula = `saved_wall − newest_entry.wall_stamp`.** Honors §6's "saved_wall: bounded-staleness reference" and the controller's "vs saved_wall". Both operands are same-machine `time.time()` values captured pre-load (no load-time-clock dependency), and because `saved_wall` advances on every save, a pile untouched across restarts trips the 24h bound and drops — while an overnight pile that was fresh at the last save survives. (`now() − wall_stamp` was considered and rejected: it depends on the load-time clock and contradicts the locked "vs saved_wall".)

3. **Store constructed in Task 7, writer in Task 8** (the controller's Task 8 text says "construct StateStore … + PersistenceWriter"). `_restore_state` (Task 7) needs the store to exist, so `self._store` is built in Task 7's `__init__` edit and `self._persistence` in Task 8's. Same file, adjacent lines — no behavior difference, just task ordering.

4. **`_snapshot_state` serializes only streams whose `frontier is not None`** (DRY/YAGNI): the frontier is a stream's sole durable field, so an empty stream carries nothing to persist and is re-created lazily on the session's next interaction — identical to a fresh session. Restore therefore creates "one SessionStream per restored frontier" exactly as §8 says.

5. **`clock` is a reserved param on `PersistenceWriter`.** §9 lists `clock` in the signature; the Event+`sleep` coalesce genuinely needs only `sleep`. I keep `clock` (locked signature) stored and documented as the reserved monotonic seam rather than dropping it. The only injected seam the tests exercise is `sleep`.

6. **Staleness drop lives once, in Task 7** (the controller lists it under both Task 7 and Task 9). It is a single code path (`_restore_state`); DRY says test it once. Task 7's `test_restore_drops_a_stale_pile_and_keeps_a_fresh_one` is the hand-built, precise home; Task 9 focuses on the catch-up + provisional-visibility integration and does not re-assert staleness.

7. **`test_config.py` pins the exact `DEFAULTS` key set** (`test_config.py:6-20`), so adding `restore_max_age_hours` REQUIRES updating that test in the same task (Task 7, Step 4) — otherwise the suite reddens the moment the key lands. Flagged because it is an easy miss.

8. **Real-code confirmations that shaped the tests:** `note_spoken` flips `heard` and calls `st.advance_frontier((entry.msg_id, entry.seq))` only for `item.forward and completed` (host.py:352-362) — the `mark_dirty` hook sits after that whole `with self._lock:` block. `on_catch_up` reads the tail via `history.unheard_from_frontier(target, st.frontier)` keyed off the LIVE `workspace()` pointer, so the integration must set the pointer first. `atomic_write_json` is **superseded, not reused** (its fixed `path + ".tmp"` is the exact concurrent-save collision §7 fixes). `set_identity`'s existing don't-clobber-with-empties path is untouched; the only change is the unconditional `_provisional.discard(session)` at the top. The **chooser needed no change** — `chooser._snapshot` already filters candidates by `is_live()`, which Task 4 makes fail-closed for provisional sessions (Task 5 ships a verification test proving this).
