# SP6 — Restart persistence (design)

**Date:** 2026-07-17 · **Status:** pre-review draft (rev 2, post adversarial review) · **Closes:** R11 (the final open item of the voice-arbitration campaign, `docs/superpowers/plans/2026-06-29-sonari-voice-arbitration-campaign.md` §SP6) · **Branch:** `build/sp6-persistence`

> Rev 2 folds in a three-lens adversarial review (2026-07-17): a critical concurrent-save corruption fix (§7/§9), the restored-session **phantom** fix (§4.4, the biggest design change), a corrected reachability model (§2), the mark_dirty chokepoint (§7), a wall-clock test seam (§5), `deque` maxlen on restore (§9), and single-threaded restore ordering (§8).

## 1. Problem

The daemon holds each session's durable speech state — the rolling transcript pile, the frontier high-water mark, folder labels, stable spoken numbers — entirely in memory. On any daemon restart (`sonari install`, a crash, a sleep/wake edge), all of it is silently dropped: every session loses its accumulated unheard output, its frontier, and its stable number. For an eyes-free daily-driver tool, a restart that erases "what you haven't heard yet" is a real trust gap.

SP6 serializes the durable state to disk, snapshotted off the speak-loop hot path, and reloads it on boot — so a restart preserves the pile and catch-up (⌃⌘L) reaches back into it the moment you return to a session.

## 2. The enabling fact — and the honest reachability model

Every durable per-session structure is keyed by the **Claude Code session id** — a client-provided string stable across resume/clear/compact (see the `set_identity` note in `sessions.py`) and independent of the daemon process. So persistence needs **no re-association logic**: we reload state keyed by session id, and when a session reconnects, its restored pile/frontier/folder/number are already present under the same key.

**When the pile becomes reachable (corrected in rev 2):** catch-up (`on_catch_up`) and ⌃⌘W (`on_where_am_i`) key their target off the *live* pointers `sessions.workspace()` / `sessions.speaker()` (= `_foreground` / `_os_focused_session` / `_speaker`). Those are **not** restored (§4.2), so immediately after boot — before you touch anything — they are `None` and ⌃⌘L/⌃⌘W hit their error-earcon branch. A restored session's pile becomes reachable **on its next prompt/interaction**, which fires `on_set_foreground` → `set_foreground` → sets the workspace pointer *and* re-captures identity. In practice this is the natural flow: after a restart you return to a session and type; from that instant its restored pile is there for catch-up.

Sessions do **not** re-register at the instant of restart — only on their next prompt/hook (what `BOOT_CUE` announces). Today that "next prompt" is when a session *comes into existence*; with SP6 the session already *exists on boot* (pile/folder/number restored) but stays **provisional** (§4.4) until that first interaction confirms it.

## 3. Approach

**Single atomic JSON snapshot** at `SONARI_DIR / "state.json"`, written via `atomicio.atomic_write_json` (temp + `fsync` + `os.replace`). The durable state is small and bounded (≤ `history_cap` × sessions).

- **Rejected — SQLite:** schema + migration weight disproportionate to a small bounded blob; still needs the same off-lock discipline; no payoff.
- **Rejected — append-only log:** reintroduces the per-mutation hot-path I/O the campaign forbids, plus compaction.

## 4. State model — what persists, what doesn't

### 4.1 RESTORE (the durable facts) — decided

| State | Home | Serialized form |
|---|---|---|
| Transcript pile (per-session `HistoryEntry` deque) | `SessionHistory._entries` | list of entry dicts (incl. `heard`; `stamp` → wall-clock, §5) |
| Message/turn counters (load-bearing) | `SessionHistory._msg_id` / `_group_seq` / `_turn_id` | ints per session |
| Frontier high-water mark (load-bearing) | `SessionStream.frontier` | `[msg_id, seq]` (JSON list → tuple on load, §6) or `null` |
| Folder labels (the roster) | `SessionManager._sessions` | session → folder string/null |
| Stable spoken numbers | `SessionManager._numbers` | session → int |
| SpeechItem id counter (continuity nicety) | `SessionState._next_id` | int |

- **`heard` restores as-is** — a restored entry keeps its heard/unheard truth.
- **Frontier + counters are load-bearing, not optional.** Without the frontier, `unheard_from_frontier(session, None)` treats the whole restored pile as un-dealt-with and catch-up replays items you already handled. The three counters keep a post-restore `record()` minting keys strictly *ahead* of any restored frontier (so the frontier never retreats and no key collides — verified in review).
- **`_next_id` is a best-effort continuity nicety, NOT load-bearing** (rev 2): every id consumer (queues, `_pending_heard`) is transient and empty on boot, and the frontier keys off `(msg_id, seq)`, not `SpeechItem.id`. Restoring it only keeps globally-monotonic ids for tidy diagnostics; the fail-open path (missing → `0`) is explicitly safe and violates no guard.
- **`SessionHistory._cap`** is **reconstructed from the live config, not persisted** — restore rebuilds each deque at the *current* cap (§9).

### 4.2 DO NOT RESTORE (transient) — decided

The interrupted readout (`_current_item` — ratified: *restore the frontier, don't re-enqueue*); the pending `SpeechQueue`; in-flight heard markers (`_pending_heard`); mid-assembly state (`ProseAssembler`, `prose_buffer`); the last decision/options text (`SessionStream.options` — re-established on the next decision); browse cursors (`nav_cursor`/`nav_turn`); the per-stream one-shot flags (`warned_immediate`/`guided` — reset; worst case one benign extra guidance cue post-restart); repeat-last (`_last_utterance` — its audio temp file is gone); the attribution cursor (`_last_spoken_session`); recency (`_mru`); the tty-eviction set (`_tty_evicted` — reconstructs empty, consistent with D2: no restored identities ⇒ no evictions); and the **live pointers** (`_foreground` / `_speaker` / `_os_focused_session` / `_os_focus_raw`). Also reconstructed, not persisted: `_wake` (Event), `SessionHistory._clock` (§5 seam), `SessionManager.background_policy` (config). All re-establish on the next prompt/click, exactly as today.

### 4.3 The two behavior calls — RATIFIED (Nima, 2026-07-17)

- **D1 — a held "stop" does NOT survive restart.** `_voice_state` resets to `"flowing"`; per-session `SessionStream.stopped` resets to `False`. Rationale: for an eyes-free tool, a daemon that boots, says "restarted," then sits silently is indistinguishable from *broken*; re-stopping is one keypress, but silence-that-looks-like-breakage is the worse failure. (The field `state.py` explicitly tagged "→ SP6".)
- **D2 — terminal identities (`_identities`, tty/iTerm ids) are NOT restored.** Rationale: the restart gap is exactly when a saved tty pin is most likely stale — a terminal may have closed, or macOS may have recycled the tty number — so a restored pin can silently raise the *wrong* window (the phantom class SP3.2 fought). Focus-follow self-heals when each session re-captures its tty on its next prompt (W4).
  - **D2 has an is_live consequence** that §4.4 fixes: with no restored identity, `is_live()` fail-*opens* (`tty_alive("") == True`), so a restored session would look alive to the chooser and ⌃⌘W even after its terminal died. §4.4 closes this without reintroducing the wrong-window raise.
  - **Rejected-for-now — validated-restore** (restore identities, drop dead ttys at load, treat as provisional): recovers immediate focus-follow for still-open terminals but can't catch the tty-*recycle* case. **Reopen condition:** if the post-restart focus-follow-blind window proves annoying in daily use.

### 4.4 Provisional restore + roster hygiene (rev 2 — closes the phantom) — decided

D2's `is_live` fail-open (§4.3) means a restored session whose terminal *closed during downtime* would otherwise become a **permanent ghost**: surfaced in the ⌃⌘W Also-map with a frozen "N unheard, stale", browsable in the chooser, and — worst — committing to it in the chooser passes the fail-open `is_live` guard and *raises a dead terminal* while telling you it succeeded. No `SESSION_END` ever arrives for a force-closed terminal and there is no liveness reaper, so ghosts accumulate across every `sonari install`, holding the low spoken numbers (number inflation).

**Fix — a provisional flag, D2-compatible (needs no restored identity):**

- Restore adds every restored session id to a new `SessionManager._provisional: set`.
- **`is_live()` fail-CLOSES for a provisional session** (returns `False`) — narrowly, only for provisional sessions; the existing fail-*open* for a normally-registered, not-yet-identified session is unchanged.
- **The ⌃⌘W Also-map (`control._also_clause`) and the chooser snapshot exclude provisional sessions** (the chooser already filters by `is_live`, so fail-closed handles it; ⌃⌘W does not filter by `is_live`, so it gets an explicit `not is_provisional` gate). A provisional session is thus invisible to every surface that could raise or announce it, until confirmed.
- **A session clears provisional when it re-captures a real identity this lifetime** (`set_identity`, fired by its next `SESSION_START`). That same event sets the workspace pointer that makes its pile reachable (§2) — so quarantine ends exactly when the pile becomes usable. Cost to the happy path: zero.

**Bounded-staleness drop-on-load (accumulation guard):** on load, drop any restored session whose newest entry's wall-age exceeds a threshold (default **24h**, config `restore_max_age_hours`). This kills long-dead ghosts, bounds `state.json` and the provisional set, and reflects that a pile you haven't returned to in a day is not worth resurrecting. The 24h default covers the common overnight sleep/wake case; it is a tunable behavior number (see the open question in §14).

## 5. Clock / staleness normalization

`HistoryEntry.stamp` is a `time.monotonic()` value — meaningless across a restart. `unheard_age()` (which the ⌃⌘W "stale" word reads) computes `clock() - stamp`; a blindly-restored monotonic stamp yields garbage/negative age.

- **On save:** capture `mono_now = clock()` and `wall_now = now()` once. Per entry, store `wall_stamp = wall_now - (mono_now - entry.stamp)` — its absolute wall-clock record time.
- **On load:** capture `mono_now2 = clock()` and `wall_now2 = now()` once. Set `entry.stamp = min(mono_now2, mono_now2 - (wall_now2 - wall_stamp))` (clamped, never future). Then `unheard_age = clock() - stamp` reports the **true elapsed age spanning the downtime** — the "stale" cue stays truthful after a restart.

**Both clocks are injectable (rev 2):** `to_state()` / `load_state()` take **both** `clock=` (monotonic seam, already on `SessionHistory`) **and** `now=time.time` (wall seam), so the save-side `wall_stamp` math and the downtime-spanning age are unit-testable hermetically end-to-end (advancing only the monotonic seam can't move wall time). `load_state()` rebinds `self._clock` to the injected `clock` so the `mono_now2` capture and later `unheard_age()` reads use one clock.

## 6. Serialization format

```jsonc
{
  "version": 1,                       // mismatch => fail open (§8)
  "saved_wall": 1721260800.0,         // time.time() at snapshot (bounded-staleness reference, §4.4)
  "next_id": 42,                      // continuity nicety (§4.1)
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

- **`mark_dirty()`** = `self._dirty.set()` on a `threading.Event` — atomic, non-blocking, **no lock, no I/O**. **Hook point (rev 2):** inside **`host.handle_message()`** — the single chokepoint through which all three dispatch entrypoints funnel (socket via `_handle_message_guarded`, hotkeys via `_dispatch_hotkey`, catch-up results via `_drain_catchup_inbox`) — plus the speak-loop completion hook (`note_spoken`, where `heard` flips and the frontier advances). Hooking a single caller (e.g. the socket path) would silently never persist hotkey-only durable mutations: SKIP_PILE advances the frontier and FLUSH bumps the turn/msg counters, both arriving *only* via the hotkey path. Over-marking on read-only messages is harmless (the writer coalesces).
- **Writer loop:** `dirty.wait()` → `sleep(debounce)` to coalesce → `dirty.clear()` → build snapshot → `store.save(data)`. Ordering is correct: a `mark_dirty` landing between `clear()` and the snapshot is still captured (the snapshot reads live state) *and* re-arms the Event for a redundant next cycle — no steady-state mutation is dropped (only a hard-kill within the debounce window loses the last delta — the accepted crash-net tradeoff). Wrapped so a `store.save` fault never propagates out of the writer.
- **Snapshot build (`_snapshot_state`)** holds `self._lock` **only long enough to copy primitive fields** — each entry's `(text, kind, msg_id, seq, turn_id, heard, stamp)` tuple (NOT `HistoryEntry` references — primitives are extracted under the lock so a post-release `heard` flip can't tear the read), the counter dicts, frontiers, folder/number maps, `next_id`, and `mono_now`/`wall_now`. Then the lock is released and **all JSON-shaping + clock normalization + the disk write happen off-lock.** No nested lock is taken, so no deadlock (verified in review).

**Concurrent-save safety (rev 2, CRITICAL fix):** the shutdown flush and the writer thread must never share `atomic_write_json`'s fixed `path + ".tmp"` — interleaved `json.dump`s publish a torn temp, which `load()` then rejects and fail-opens to **empty**, silently dropping the whole pile on the exact `sonari install` path SP6 protects. Two mandated guards, both:

1. **`StateStore.save()` serializes writes** under an internal `threading.Lock` **and** writes to a **unique** temp in the same dir (`tempfile.mkstemp` + `os.replace`), so concurrent saves can never collide on a temp file.
2. **Shutdown order is a contract:** `host.run()`'s `finally` does `persistence.stop()` and **joins the writer thread**, and **quiesces the speak thread** (so a final `note_spoken` heard-flip/frontier-advance can't land after the snapshot), **before** the final synchronous `flush()`. Today `stop()` only clears `_running`/sets `_wake` and never joins the speak thread — SP6 adds the join/quiesce.

**Acceptance bar for "off the hot path":** the permanent concurrency/monotonicity guards (`tests/test_concurrency_guards.py`) stay green at every commit, assertions never weakened.

## 8. Boot / restore + fail-open

**Restore runs single-threaded, before any other actor exists (rev 2).** In `host.run()`, call `_restore_state()` **after `ensure_sonari_dir()` and before `write_lockfile()` / `speak_thread.start()` / `server.serve()`** — i.e. before the daemon is discoverable and before any speak/accept/hotkey thread can touch state. Restore takes no lock; this ordering is what keeps it torn-state-free (the surviving hotkeyd is a separate process that reconnects the instant the socket is advertised, so "before serve()" alone is too loose). Then `persistence.start()`.

- `_restore_state()`: `data = store.load()`. On **missing / unreadable / invalid-JSON / version-mismatch**, `load()` returns `None` → restore is a no-op → the daemon boots empty. Otherwise: drop stale sessions (§4.4), then `history.load_state(...)`, create a `SessionStream` per restored frontier and set it, `sessions.load_state(...)` (populating `_sessions`, `_numbers`, and `_provisional`), `_next_id = data["next_id"]`.
- **Fail-open is mandatory** (matches `_arm_faulthandler`/`_start_boot_cue`): any restore exception is swallowed to empty state. `BOOT_CUE` is emitted in `bootstrap.main()` *before* `daemon.run()`, in a different function from restore — the plan must keep them separated so restore can never swallow or duplicate the cue.

## 9. Unit boundaries (for isolation + testability)

- **`SessionHistory.to_state()` / `load_state(data, *, clock, now)`** — owns entries + counters + the stamp↔wall-clock conversion; **rebuilds each deque as `deque(iterable, maxlen=self._cap)`** keeping the newest `cap` entries when the saved pile exceeds the current cap (rev 2 — else `maxlen=None` grows unbounded, or a silent drop leaves the frontier behind the oldest survivor). Pure, no I/O.
- **`SessionStream.to_state()` / `load_state(data)`** — frontier only (list↔tuple). Pure.
- **`SessionManager.to_state()` / `load_state(data)`** — folder + number maps; on load, seeds `_provisional` with the restored ids. `set_identity` clears a session from `_provisional`. Pure.
- **`daemon/persistence.py`**: `StateStore(path)` (`load()` → dict|None fail-open; `save(dict)` → serialized unique-temp atomic write) and `PersistenceWriter(store, snapshot_fn, lock, *, debounce, clock, sleep)` (thread + dirty Event; `mark_dirty` / `start` / `stop`(join) / `flush`). `clock`/`sleep` injectable for tests.
- **Host** wires them: constructs `StateStore(SONARI_DIR / "state.json")` (SONARI_DIR read live so conftest's monkeypatch redirects it), owns `_snapshot_state()` / `_restore_state()`, calls `mark_dirty()` at the two hook points.

## 10. Testing strategy (TDD)

- **Round-trip units:** history entries + counters + `heard` reproduce exactly; frontier `[m,s]` → tuple and compares without `TypeError`; folder + number maps reproduce.
- **Shrunk-cap round-trip (rev 2):** save with `cap=N`, load with `cap<N` → deque honors the new `maxlen` (no unbounded growth), keeps newest entries, and the restored frontier's aged-out cue fires for the dropped gap (no `TypeError`).
- **Clock normalization (rev 2, hermetic):** inject both `clock` and `now`; record → save → advance both seams by a simulated downtime → load → `unheard_age` reflects the true elapsed age including downtime, never negative; a save-side sign error is caught end-to-end.
- **Concurrent-save safety (rev 2):** two overlapping `save()`s never produce invalid JSON (unique temp + internal lock); a subsequent `load()` returns a valid snapshot, never empty-on-corruption.
- **mark_dirty completeness (rev 2):** a hotkey-only SKIP_PILE and a hotkey-only FLUSH each produce a `mark_dirty` (guards the chokepoint placement).
- **Fail-open:** missing file, corrupt JSON, and version-mismatch each → empty state + daemon boots + `BOOT_CUE` fires exactly once.
- **Writer:** N `mark_dirty()` in a burst coalesce into a small bounded number of `save()`s; `flush()` writes synchronously; a raising `store.save` does not propagate; `mark_dirty` proven non-blocking (acquires no lock on the calling thread).
- **Behavior decisions:** post-restore `voice_state == "flowing"` and every `stream.stopped is False` (D1); restored roster has folder + number but `identity(session) is None` and the session is **provisional** (D2/§4.4).
- **Provisional/phantom (rev 2):** a restored session is `is_live()==False`, absent from the chooser snapshot, and absent from the ⌃⌘W Also-map until `set_identity` clears it; then it appears normally. A restored session older than `restore_max_age_hours` is dropped at load.
- **Integration:** restore a pile → **first set a workspace pointer** (simulate the session's next prompt) → ⌃⌘L catch-up reads the frontier'd tail (not the whole pile); ⌃⌘W unheard count reflects the restored state. Use the sync-harness idiom (direct-set `daemon._current_item`) for barge-in-adjacent scenarios.
- **Guards:** the permanent concurrency/monotonicity guards stay green at every commit. Never run a live `claude` or a live daemon in the suite.

## 11. Out of scope

Restoring identities / live pointers / the interrupted readout / the queue / repeat-last / a held stop (§4.2–4.3). A background liveness reaper (the provisional flag + bounded-staleness drop-on-load cover roster hygiene without one). Cross-machine sync, encryption, schema migration (v1 is the first format; the `version` key reserves the seam). The `BOOT_CUE` wording is unchanged and still truthful; whether it should advertise that state now survives is a later ear-pass, not gated here.

## 12. Contracts this must not violate (HANDOFF gotchas)

- **Off the speak-loop hot path.** Never hold `self._lock` across disk I/O; the only lock hold is the primitive-field snapshot copy.
- The permanent concurrency/monotonicity guards stay green at every commit; assertions never weakened.
- Conventional commits; **no AI/tool/session mentions**; noreply email `74723240+nimkimi@users.noreply.github.com`.
- Any `~/.sonari`-touching behavior is dogfooded under a **sacrificial HOME** before Nima installs to his real `~/.sonari`. Sandboxed `sonari install` silently fails to restart daemons — final install is Nima's, unsandboxed, verified via fresh `ps -o lstart` + `diff -rq`.
- Worktree/subagent imports need `PYTHONPATH="$PWD/src"`; pytest is safe (conftest pins).
- **Main pushes are Nima's.** This work lands on `build/sp6-persistence`, whole-branch reviewed, then merged by him.

## 13. Decisions log

1. **Format:** single atomic JSON snapshot (rejected SQLite, append-log). — §3
2. **Restore surface:** durable facts only (pile + frontier + folder + number + heard; `_next_id` as a nicety), not live cursors/queue/readout. — §4.1/4.2
3. **D1:** held-stop resets to flowing on restart. — §4.3
4. **D2:** identities not restored; validated-restore rejected-for-now with a reopen condition. — §4.3
5. **Provisional restore (rev 2):** restored sessions are quarantined (is_live fail-closed, excluded from chooser + ⌃⌘W) until they re-capture identity; bounded-staleness drop-on-load at 24h. Closes the phantom D2 would otherwise open. — §4.4
6. **Reachability (rev 2):** a restored pile is reachable on the session's next interaction (which sets the workspace pointer), not instantly at boot. — §2
7. **Clock:** stamps persisted as wall-clock, re-normalized to the running monotonic clock on load; both clocks injectable. — §5
8. **Concurrent-save safety (rev 2):** unique temp + internal save lock, and a stop()+join+quiesce-before-flush shutdown contract. — §7

## 14. Open question for Nima (one behavior number)

**`restore_max_age_hours`** (§4.4) — how old can a restored pile be and still come back? Defaulted to **24h** (covers overnight sleep/wake; a day-old unheard pile you've likely moved on from). Options if you want it different: shorter (e.g. 4–8h — only resurrect same-working-session piles) or longer/off (keep everything, rely purely on the provisional quarantine + deque cap). This is the one knob that changes what you actually experience after a long downtime.
