# SP6 — Restart persistence (design)

**Date:** 2026-07-17 · **Status:** pre-review draft · **Closes:** R11 (the final open item of the voice-arbitration campaign, `docs/superpowers/plans/2026-06-29-sonari-voice-arbitration-campaign.md` §SP6) · **Branch:** `build/sp6-persistence`

## 1. Problem

The daemon holds each session's durable speech state — the rolling transcript pile, the frontier high-water mark, folder labels, stable spoken numbers — entirely in memory. On any daemon restart (`sonari install`, a crash, a sleep/wake edge), all of it is silently dropped: every session must re-register from scratch and loses its accumulated unheard output, its frontier, and its stable number. For an eyes-free daily-driver tool, a restart that erases "what you haven't heard yet" is a real trust gap.

SP6 serializes the durable state to disk, snapshotted off the speak-loop hot path, and reloads it on boot — so a restart preserves the pile and catch-up (⌃⌘L) reaches straight back into it.

## 2. The enabling fact (why this is clean)

Every durable per-session structure is keyed by the **Claude Code session id** — a client-provided string that is stable across resume/clear/compact (see the `set_identity` note in `sessions.py`) and independent of the daemon process. So persistence needs **no re-association logic**: we reload state keyed by session id, and when a session reconnects on its next prompt (same id), its restored pile/frontier/folder/number are already present. Catch-up and the ⌃⌘W counts key off the session id, not the terminal identity, so they work for a restored session **before** it re-registers.

Corollary (settles the "do sessions re-register on restart?" question): sessions do **not** re-register at the instant of restart — only on their next prompt/hook (what `BOOT_CUE` announces). Today that "next prompt" is when a session *comes into existence*. With SP6, a session **already exists on boot** with its pile/folder/number restored; "re-register on next prompt" shrinks to *re-assert its live terminal identity and become the focus/raise target again*.

## 3. Approach

**Single atomic JSON snapshot** at `SONARI_DIR / "state.json"`, written via the existing `atomicio.atomic_write_json` (temp + `fsync` + `os.replace` — torn-write safe). The durable state is small and bounded (≤ `history_cap` × sessions), so a whole-state snapshot is the natural fit.

- **Rejected — SQLite:** schema + migration weight disproportionate to a small bounded blob; still needs the same off-lock discipline; no payoff.
- **Rejected — append-only log:** reintroduces exactly the per-mutation hot-path I/O the campaign forbids, and needs compaction.

## 4. State model — what persists, what doesn't

### 4.1 RESTORE (the durable facts) — decided

| State | Home | Serialized form |
|---|---|---|
| Transcript pile (per-session `HistoryEntry` deque) | `SessionHistory._entries` | list of entry dicts (incl. `heard`; `stamp` → wall-clock, see §5) |
| Per-session message/turn counters | `SessionHistory._msg_id` / `_group_seq` / `_turn_id` | ints per session |
| Frontier high-water mark | `SessionStream.frontier` | `[msg_id, seq]` (JSON list → tuple on load, §6) or `null` |
| Folder labels (the roster) | `SessionManager._sessions` | session → folder string/null |
| Stable spoken numbers | `SessionManager._numbers` | session → int |
| SpeechItem id counter | `SessionState._next_id` | int |

Together these are exactly what catch-up and the ⌃⌘W clauses read. **`heard` flags restore as-is** — a restored entry keeps its heard/unheard truth. The frontier restore is **load-bearing, not optional**: without it, `unheard_from_frontier(session, None)` treats the whole restored pile as un-dealt-with and catch-up would replay items you already handled.

### 4.2 DO NOT RESTORE (transient) — decided

The interrupted readout (`_current_item` — ratified campaign rule: *restore the frontier, don't re-enqueue*), the pending `SpeechQueue`, in-flight heard markers (`_pending_heard`), mid-assembly state (`ProseAssembler`, `prose_buffer`), browse cursors (`nav_cursor`/`nav_turn`), the per-stream one-shot flags (`warned_immediate`/`guided` — reset; worst case one extra guidance cue post-restart, which is benign), repeat-last (`_last_utterance` — its audio temp file is already gone), the attribution cursor (`_last_spoken_session`), recency (`_mru`), and the live pointers (`_foreground` / `_speaker` / `_os_focused_session` / `_os_focus_raw`). All re-establish on the next prompt/click, exactly as today.

### 4.3 The two behavior calls — RATIFIED (Nima, 2026-07-17)

- **D1 — held "stop" does NOT survive restart.** `SessionState._voice_state` resets to `"flowing"`; per-session `SessionStream.stopped` resets to `False`. Rationale: for an eyes-free tool, a daemon that boots, says "restarted," then sits silently is indistinguishable from *broken*; re-stopping is one keypress, but silence-that-looks-like-breakage is the worse failure. (This is the field `state.py` explicitly tagged "→ SP6".)
- **D2 — terminal identities (`_identities`, tty/iTerm ids) are NOT restored.** Rationale: the restart gap is exactly when a saved tty pin is most likely stale — a terminal may have closed, or macOS may have recycled the tty number to a *different* terminal — so a restored pin can silently raise the wrong window (the phantom class SP3.2 fought). Focus-follow self-heals when each session re-captures its tty on its next prompt (W4), a brief human-timescale-empty window the boot cue already relies on.
  - **Rejected-for-now — validated-restore:** restore identities but drop any whose tty fails `ttyutil.tty_alive` at load and treat the rest as provisional. Recovers immediate focus-follow for still-open terminals but cannot catch the tty-*recycle* case. **Reopen condition:** if the post-restart focus-follow-blind window proves annoying in daily use.

## 5. Clock / staleness normalization

`HistoryEntry.stamp` is a `time.monotonic()` value — **meaningless across a process restart**. `unheard_age()` (which the ⌃⌘W grammar-v2 "stale" word reads) computes `clock() - stamp`; a blindly-restored monotonic stamp yields garbage or a negative age.

- **On save:** capture `mono_now = clock()` and `wall_now = time.time()` once. For each entry, store `wall_stamp = wall_now - (mono_now - entry.stamp)` — the entry's absolute wall-clock record time.
- **On load:** capture `mono_now2 = clock()` and `wall_now2 = time.time()` once. Set `entry.stamp = mono_now2 - (wall_now2 - wall_stamp)`, clamped to `≤ mono_now2` (never future). Then `unheard_age = clock() - stamp` reports the **true elapsed age spanning the downtime** — so the "stale" cue tells the truth after a restart, not a reset-to-zero.

This keeps the monotonic clock encapsulated in `SessionHistory` (its existing `clock=` injection point) — no clock leaks to callers.

## 6. Serialization format

```jsonc
{
  "version": 1,                       // reserved for future migrations; mismatch => fail open (§8)
  "next_id": 42,                      // SessionState._next_id
  "sessions": {                       // SessionManager durable
    "<session_id>": { "folder": "myrepo", "number": 1 }
  },
  "streams": {                        // per-session frontier
    "<session_id>": { "frontier": [3, 0] }   // or null
  },
  "history": {                        // SessionHistory durable
    "<session_id>": {
      "msg_id": 5, "group_seq": 0, "turn_id": 2,
      "entries": [
        { "text": "…", "kind": "prose", "msg_id": 3, "seq": 0,
          "turn_id": 2, "heard": true, "wall_stamp": 1721260790.0 }
      ]
    }
  }
}
```

**Frontier rehydration:** JSON round-trips tuples as lists; the loader converts each non-null `frontier` back to a `tuple`, else `key > self.frontier` (tuple-vs-list) raises `TypeError`.

## 7. Persistence mechanism (respecting the one hard perf rule)

A dedicated **writer thread**, debounced and off-lock.

- **`mark_dirty()`** = `self._dirty.set()` on a `threading.Event` — atomic, non-blocking, **no lock, no I/O**. Called from (a) the message-dispatch chokepoint after each handled message (over-marking on read-only messages is harmless — the writer coalesces), and (b) the speak loop's completion hook where `heard` flips and the frontier advances (`note_spoken`). On the speak thread this is a single `Event.set()` — negligible hot-path cost.
- **Writer loop:** `dirty.wait()` → `sleep(debounce)` to coalesce a burst → `dirty.clear()` → build snapshot → `store.save(data)`. Wrapped so it never raises (a persistence fault must never wedge the daemon).
- **Snapshot build (`_snapshot_state`)** holds the existing daemon lock **only long enough to copy primitive fields** — each entry's `(text, kind, msg_id, seq, turn_id, heard, stamp)`, the counter dicts, frontiers, folder/number maps, `next_id`, and `mono_now`/`wall_now`. This is a fast, fully-consistent shallow copy (no torn `heard` reads), never taken during an utterance. The lock is then released and **all JSON-shaping + clock normalization + the disk write happen off-lock.**
- **Shutdown flush:** a best-effort synchronous `flush()` (snapshot + save) in `host.run()`'s existing `finally`. The debounced writer is the crash-safety net for hard kills that skip `finally`.

**Acceptance bar for "off the hot path":** the permanent concurrency/monotonicity guards (`tests/test_concurrency_guards.py`) stay green at every commit, assertions never weakened.

## 8. Boot / restore + fail-open

In `host.run()`, after `ensure_sonari_dir()` and **before** the server serves: call `_restore_state()`, then `persistence.start()`.

- `_restore_state()`: `data = store.load()`. On **missing / unreadable / invalid-JSON / version-mismatch**, `load()` returns `None` → restore is a no-op → the daemon boots with empty state and `BOOT_CUE` still fires. Otherwise apply: `history.load_state(...)`, create a `SessionStream` per session with a restored frontier and set it, `sessions.load_state(...)`, `_next_id = data["next_id"]`.
- **Fail-open is mandatory** (matches the codebase's "never crash startup" pattern in `_arm_faulthandler`/`_start_boot_cue`): any restore exception is swallowed to empty state; a persistence bug must never swallow or duplicate the boot cue, and must never wedge boot.

## 9. Unit boundaries (for isolation + testability)

- **`SessionHistory.to_state()` / `load_state(data, clock=…)`** — owns its own serialization (entries + counters + the stamp↔wall-clock conversion). Pure, no I/O.
- **`SessionStream.to_state()` / `load_state(data)`** — frontier only (list↔tuple). Pure.
- **`SessionManager.to_state()` / `load_state(data)`** — folder + number maps only. Pure.
- **`daemon/persistence.py`**: `StateStore(path)` (`load()` → dict|None fail-open; `save(dict)` → atomic write) and `PersistenceWriter(store, snapshot_fn, lock, *, debounce, clock, sleep)` (thread + dirty Event; `mark_dirty` / `start` / `stop` / `flush`). `clock`/`sleep` injectable for tests.
- **Host** wires them: constructs `StateStore(SONARI_DIR / "state.json")` (SONARI_DIR read live so conftest's monkeypatch redirects it), owns `_snapshot_state()` and `_restore_state()`, calls `mark_dirty()` at the two hook points.

Each object knows how to (de)serialize only its own durable fields; `persistence.py` orchestrates and does the only I/O.

## 10. Testing strategy (TDD)

- **Round-trip units:** history entries + counters + `heard` reproduce exactly; frontier `[m,s]` → tuple and compares without `TypeError`; folder + number maps reproduce.
- **Clock normalization:** record → save → inject a simulated downtime via the clock seam → load → `unheard_age` reflects the true elapsed age (including downtime), never negative/garbage.
- **Fail-open:** missing file, corrupt JSON, and version-mismatch each → empty state + daemon boots + `BOOT_CUE` fires exactly once.
- **Writer:** N `mark_dirty()` in a burst coalesce into a small bounded number of `save()`s (inject `clock`/`sleep`); `flush()` writes synchronously; a `store.save` that raises does not propagate out of the writer.
- **Behavior decisions:** post-restore `voice_state == "flowing"` and every `stream.stopped is False` (D1); restored roster has folder + number but `identity(session) is None` until re-register (D2).
- **Integration:** restore a pile → ⌃⌘L catch-up reads the frontier'd tail (not the whole pile); ⌃⌘W unheard count reflects the restored state. Use the established sync-harness idiom (direct-set `daemon._current_item`) for barge-in-adjacent scenarios.
- **Guards:** the permanent concurrency/monotonicity guards stay green at every commit; `mark_dirty` proven non-blocking (no lock acquired on the calling thread).
- Never run a live `claude` or a live daemon in the suite.

## 11. Out of scope

Restoring identities / live pointers / the interrupted readout / the queue / repeat-last / a held stop (all §4.2–4.3). Cross-machine sync, encryption, schema migration (v1 is the first format; the `version` key only reserves the seam). The BOOT_CUE wording is unchanged and still truthful; whether it should advertise that state now survives is a later ear-pass, not gated here.

## 12. Contracts this must not violate (HANDOFF gotchas)

- **Off the speak-loop hot path.** Never hold `self._lock` across disk I/O; the only lock hold is the primitive-field snapshot copy.
- The permanent concurrency/monotonicity guards stay green at every commit; assertions never weakened.
- Conventional commits; **no AI/tool/session mentions**; noreply email `74723240+nimkimi@users.noreply.github.com`.
- Any `~/.sonari`-touching behavior is dogfooded under a **sacrificial HOME** before Nima installs to his real `~/.sonari` (campaign per-sub-project protocol). Sandboxed `sonari install` silently fails to restart daemons — final install is Nima's, unsandboxed, verified via fresh `ps -o lstart` + `diff -rq`.
- Worktree/subagent imports need `PYTHONPATH="$PWD/src"`; pytest is safe (conftest pins).
- **Main pushes are Nima's.** This work lands on `build/sp6-persistence`, whole-branch reviewed, then merged by him.

## 13. Ratified decisions log

1. **Format:** single atomic JSON snapshot (rejected SQLite, append-log). — §3
2. **Restore surface:** durable facts only (pile + frontier + folder + number + id + heard), not live cursors/queue/readout. — §4.1/4.2
3. **D1:** held-stop resets to flowing on restart. — §4.3
4. **D2:** identities not restored; validated-restore documented as rejected-for-now with a reopen condition. — §4.3
5. **Clock:** stamps persisted as wall-clock, re-normalized to the running monotonic clock on load so age survives downtime truthfully. — §5
