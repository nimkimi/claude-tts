The advisor confirms feature-primary and sharpens exactly what the synthesis must fold in. I have all the source grounding and a clear structure. The deliverable is my final message (not a file, per instructions). Writing the complete design now.

---

# Sonari Target Architecture — Recommended Design

## 1. Recommended architecture

**Thesis: `daemon.py` becomes a thin HOST that owns the irreducible concurrency core; the 27-branch ladder dissolves into per-feature handler modules behind a uniform `(ctx, msg) -> reply|None` registry; the boundary between host and features is drawn precisely AT THE LOCK.**

This is **feature-primary** (vertical decomposition along the behavior clusters the test suite already reveals), with three grafts from the runners-up, each justified by a critique in §2:
- the **actor**'s lock-owner naming + an explicit `with state.transaction():` boundary (makes the "callers hold the lock" rule structural, not convention);
- the **pipeline**'s data/control distinction (explains why some handlers are pure and some stateful) and its "speak loop is ONE unit, the existing per-session `SpeechQueue` is the sole producer→consumer hand-off" rule;
- the **layered** axis's clean L0 transport extraction, composition-root framing, and the ladder→table-calling-the-same-private-methods migration step.

Why feature over the others, in one line: it is the only axis that treats the owner's hard perf constraint as load-bearing — it refuses to verticalize the concurrency core and keeps all new indirection on the *already-locked* dispatch path, never on the speak path (verified: the speak loop at 939-1024 calls `pop_next`/`speak`/`note_spoken` directly on host state, and the registry/ctx sit only on the connection thread).

### Target module/package tree

```
src/sonari/daemon/
  host.py            # the SpeechDaemon host: lock, speak loop, stream registry, kernel ops, dispatch entry
  state.py           # SessionState: the lock owner + transaction() boundary + global ledger (GRAFTED from actor)
  server.py          # L0: socket lifecycle, conn concurrency, framing (GRAFTED extraction shape from layered)
  registry.py        # the HANDLERS dict + @handler decorator + registry-completeness guard
  context.py         # Ctx: the facade features receive (documents its REAL surface)
  bootstrap.py       # main(), ensure_running(), _arm_faulthandler(), single-instance guard
  __init__.py        # back-compat re-export shim + side-effect imports that populate the registry
  features/
    prose.py         # PROSE/TOOL/EARCON/FLUSH  (data + turn-boundary)
    decisions.py     # CHOICE/PLAN/PERMISSION/REREAD_OPTIONS + the 5 pure builders
    navigation.py    # NAV (within-turn + cross-turn) + shared _seek_and_play
    playback.py      # PAUSE/MUTE/PIN_TOGGLE/STOP/SKIP/JUMP_DECISION  (per-session steer)
    focus.py         # JUMP_WAITING + the raise (over RaiseBackend seam) + _waiting_target lives here
    control.py       # SET_*/CYCLE/STATUS/PING + setup-health + clamp helper
    lifecycle.py     # SESSION_START/SESSION_END/SET_FOREGROUND
    hotkeys.py       # RELOAD_KEYMAP + hotkey start/stop/reload (off-lock reload preserved)
src/sonari/platform/
  raise_base.py      # RaiseBackend ABC + NoopRaiseBackend  (the ONE kept seam)
  macos.py           # concrete Mac backends, no ABC; make_backend() builds the dataclass
  __init__.py        # get_platform(): the one darwin-assert branch
  transport.py       # UNMOVED (OS-agnostic stdlib; not a backend)
```

### Unit responsibilities, contracts, dependencies

| Unit | One-line responsibility | Public contract | Depends on |
|---|---|---|---|
| `host.py` (`SpeechDaemon`) | Owns the speak loop + stream registry + kernel ops; runs dispatch under the lock | `SpeechDaemon(speaker, sessions, config, raise_service=None)`; `handle_message(msg)->reply\|None` (thin: lookup + call under `state.transaction()`); `run()/stop()`; kernel ops (`_enqueue`, `_stream`, `note_spoken`, `_attributed_text`, `_buffer_prose`, `_flush_prose_buffer`, `_drop_pending`) retained as methods | `state`, `server`, `registry`, `context`, leaves (`queue`, `session_stream`, `sessions`, `speaker`, `history`, `assembler`) |
| `state.py` (`SessionState`) | The lock owner: holds `_lock`, `_streams`, `_pending_heard`, `_current_item`, `_last_spoken_session`, `_next_id`, `_paused`, `_wake`; exposes `transaction()` | `state.transaction()` (context manager = `with _lock`); `state.streams`, `state.current_item`, getters/setters for the rebindable scalars; mutators assume `transaction()` is held | `session_stream`, `queue` |
| `server.py` (`Server`) | Accept thread + bounded conn-handler pool + token/framing; M8 permit-leak recovery | `Server(dispatch_callable, token_provider)`; `run()/stop()`; calls `dispatch` under `host`'s lock (via `_handle_message_guarded`) | `transport`, `protocol`, `paths` |
| `registry.py` | MsgType→handler table; one row per type; completeness guard | `HANDLERS: dict`; `@handler(MsgType.X)`; `dispatch(ctx, msg)` = `HANDLERS.get(t, _ignore)(ctx, msg)`; `assert_complete(known_types)` | `protocol` |
| `context.py` (`Ctx`) | Singleton facade handlers receive; documents its REAL surface | `Ctx(host)` with `.speaker/.sessions/.config/.history/.stream(s)/.enqueue/.flush_prose/.drop_pending/.raise()` AND `.state` for `_paused/_wake/_current_item` access (NOT hidden — see §10) | `host` |
| `bootstrap.py` | Process lifecycle, distinct from running behavior | `main()`; `ensure_running()`; `_arm_faulthandler()` | `host`, `platform`, `paths`, `config`, `speaker`, `sessions` |
| `features/*.py` | One MsgType family each; handler bodies + that family's pure helpers | each `@handler(T) def on_x(ctx, msg) -> reply\|None`; runs UNDER the held lock; never acquires it | `context`; family-specific leaves |
| `platform/raise_base.py` | The one genuinely polymorphic seam | `RaiseBackend` (ABC), `NoopRaiseBackend` | — |
| `platform/macos.py` | Concrete Mac backends, no ABC | `make_backend()->PlatformBackend(tts, earcon, hotkey, supervisor, raise_backend)` | `raise_base` |

Every unit passes the independence test: you can state what it does, how to use it, and what it depends on without reading its internals — **except** the speak-loop / `SessionState` pair, which are jointly the concurrency knot. The design treats them as ONE bounded unit (host + its lock owner) and is honest that the lock discipline crosses that boundary (§5, §10), rather than pretending they are two clean modules.

---

## 2. Why this over the alternatives

**Aggregate critique scores (weighted totals):**

| Axis | Maintainability | Perf/Concurrency | Migration | Verdict pattern |
|---|---|---|---|---|
| **feature** | **76** | **90** | **74** | all recommend-with-changes |
| pipeline | 66 | 82 | 77 | all recommend-with-changes |
| actor | 61 | 74 | 68 | all recommend-with-changes |
| layered | 62 | 68 | 76 | all recommend-with-changes |

Feature wins both maintainability and the perf/concurrency lens — **the lens that matters most given the owner's one hard constraint** — and it wins them for the same structural reason: it draws the host/feature line at the lock and keeps the hot path untouched.

**Fatal flaws of the rejected axes (each verified against source):**

- **Layered (perf 68, lowest):** its keystone L3 `SessionState` self-violates the philosophy's defining "no layer reaches up" rule. I verified `_buffer_prose`/`_flush_prose_buffer` (122-150) read `self.config`, call `self.speaker.earcon("waiting")`, read `self.sessions.foreground()`, and `_enqueue` calls `self._wake.set()`. A pure downward-only state layer is therefore *impossible* — flush is inherently cross-layer. And `_wake` has no home in any layer. The "resolve the SessionStream split into a clean L3" claim is internally contradictory: you cannot have both clean layers and the split resolved.

- **Pipeline (Maintainability 66):** its central isolation mechanism is a fiction. It claims `SpeakConsumer` and handlers hold "references" to daemon-global state — but `_current_item` and `_last_spoken_session` are **rebindable scalars** the loop *reassigns* (verified: 974 `_current_item = item`, 987/1018 `= None`, 1020 `_last_spoken_session = prev`) and handlers *read* (441 FLUSH reads `_current_item`). Python cannot pass a value-reference to a scalar attribute such that the consumer's reassignment propagates. The unit is neither independently constructible nor testable; the pipeline metaphor also overfits (only ~6 of 27 branches are real dataflow). I keep its honest *parts* (data/control split, loop-as-one-unit) without the broken framing.

- **Actor (Maintainability 61, lowest):** spec-level contradiction in the keystone — it asserts `SessionState` is "the ONLY object whose methods take the lock," yet requires `server.py` and `hotkeys.py` to hold that lock across the entire `dispatch.handle` call, with no lock/transaction primitive in the listed interface to do it with. Unrealizable as written; the split-personality (some methods acquire, some assume-held, indistinguishable by signature) is a deadlock footgun on a daily driver.

**What I grafted, and from where:**
- From **actor**: the single best idea any critic surfaced for the deadlock ding — *name* the lock owner (`SessionState`) and give it an explicit `with state.transaction():` boundary, so "callers hold the lock" is structural, visible at every call site, and zero-cost on the hot path. Plus the monitor-not-actor hard rule (reject any command-queue that adds a per-utterance thread hop).
- From **pipeline**: the data/control distinction (it explains *why* `decisions.py` builders are pure and `playback.py` handlers are stateful) and the rule that the speak loop stays ONE unit with the existing `SpeechQueue` as the sole producer→consumer hand-off — no second queue, no callback chain.
- From **layered**: the clean L0 `Server` extraction (the socket cluster #1-3 is genuinely separable, touching daemon state only via the one `dispatch` call), the composition-root framing for `bootstrap.py`, and the ladder→table-calling-the-same-private-methods migration step (kills the if-chain shape *before* any code physically moves).

---

## 3. daemon.py's ~20 concerns → new units

| # | Concern | New home |
|---|---|---|
| 1 | TCP socket server lifecycle | `daemon/server.py` |
| 2 | Per-connection protocol handling (handshake/framing/timeout) | `daemon/server.py` |
| 3 | Connection concurrency (BoundedSemaphore, M8 permit-leak) | `daemon/server.py` |
| 4 | Central message dispatch (27-branch ladder) | `daemon/registry.py` (table) + `features/*` (bodies); `host.handle_message` thin delegate |
| 5 | Prose assembly + minqueue batching | PROSE handler → `features/prose.py`; `_buffer_prose`/`_flush_prose_buffer` stay **host kernel ops** (they read config + call speaker.earcon + sessions.foreground — NOT pure state) |
| 6 | Decision text construction (5 pure builders) | `features/decisions.py` (module-level pure fns) |
| 7 | The speak loop (the concurrency knot) | `host.py` — relocated VERBATIM, lock regions byte-for-byte, never decomposed |
| 8 | Per-session stream registry (`_stream`/`_streams`) | `host.py` kernel op; the dict lives in `state.py` |
| 9 | Within-turn navigation (`_nav`) | `features/navigation.py` (shared `_seek_and_play`) |
| 10 | Cross-turn navigation (`_nav_response`) | `features/navigation.py` (dedup'd tail) |
| 11 | Play/pause + resume | `features/playback.py` (PAUSE) + `state` (`_paused`/`_wake`) + host loop (paused branch) |
| 12 | Mute + pin toggles | `features/playback.py` |
| 13 | Jump-to-waiting + raise | `features/focus.py`; **`_waiting_target` lives HERE** (it scans `_streams`, called only at JUMP_WAITING — NOT loop-coupled, verified 173-186) |
| 14 | Global hotkey lifecycle (start/stop/reload + kill-switch) | `features/hotkeys.py`; `_dispatch_hotkey` stays on host (holds the lock) |
| 15 | Live config mutation + persistence (SET_*) | `features/control.py` (unified clamp helper) |
| 16 | Setup-health guidance | `features/control.py` / `lifecycle.py`; `_setup_health` retained on host (test-monkeypatched) |
| 17 | Status/ping reporting | `features/control.py` |
| 18 | Single-instance guard + bootstrap (`main`) | `daemon/bootstrap.py` |
| 19 | Lazy daemon start (`ensure_running`) | `daemon/bootstrap.py` |
| 20 | Native-crash diagnostics (`_arm_faulthandler`) | `daemon/bootstrap.py` |
| **+** | **EARCON (line 426)** — the feature candidate DROPPED this | `features/prose.py`: plays `speaker.earcon(kind)`; on `turn_done` calls `_flush_prose_buffer` (the sub-threshold flush — easy to regress, gets its own §9 test) |

---

## 4. Dispatch ladder — the concrete new shape

The 403-line flat `if t == X: ...; return` chain becomes a **dict registry**. This is provably isomorphic to the ladder because (verified at 340-460) every branch is a top-level `if` (not `elif`) that `return`s, so branches are mutually exclusive and order-independent; `HANDLERS.get(t, _ignore)` reproduces the exact control flow including the trailing `return None` unknown-type fall-through.

```python
# registry.py
HANDLERS: dict[str, Callable[[Ctx, dict], dict | None]] = {}
def handler(t):
    def deco(fn): HANDLERS[t] = fn; return fn
    return deco
def dispatch(ctx, msg):
    return HANDLERS.get(msg.get("type"), _ignore)(ctx, msg)

# host.py
def handle_message(self, msg):
    return dispatch(self._ctx, msg)   # caller already holds state.transaction()
```

**Heterogeneity is preserved, not flattened:** PING (`return {"ok": True}`) and JUMP_WAITING (~40 lines) are both just rows; body size lives in the feature module. The shared preamble (`session`, `verbosity` at 337-338) is computed once inside `Ctx` per message (singleton ctx, no per-message allocation) and read via `ctx.session`/`ctx.verbosity` so handlers don't re-derive it.

**Three exceptions are encoded explicitly, never normalized:**
1. **PROSE/EARCON return None + mutate** — ordinary rows; the registry returns whatever the handler returns.
2. **STATUS/PING are the only reply-producing rows** — no special casing needed; `handle_message` returns the handler's value.
3. **RELOAD_KEYMAP runs off-lock** — its handler returns immediately under the lock but delegates real work to `hotkeys.reload()`, which spawns a thread under the separate `_reload_lock` (the H2 dark-hotkey race fix, verified at 890-905 the spawn touches zero lock-protected state). A test pins that the reload work runs off the main lock.

**Completeness guard:** `assert_complete()` enumerates every `MsgType` against `HANDLERS` keys (run in CI). On a daily driver, a missing `@handler` registration would otherwise fail *silently* (handle_message returns None for the unknown type) — this guard makes a dropped registration a red test.

---

## 5. Speak loop & concurrency

**The ownership/lock model, stated as the hard rule:**

- There is exactly **ONE** lock, owned by `SessionState`. No fine-grained / per-feature locks (introducing one would break the M2/L2/M6/M8 fixes and the perf constraint).
- `SessionState` exposes `with state.transaction():` (≡ `with _lock:`). This is the **only** way any code holds the lock — a structural answer to the actor critique's deadlock-footgun ding. Server's `_handle_message_guarded` and `_dispatch_hotkey` open the transaction around `dispatch`; the speak loop opens it for its own regions. Features NEVER open a transaction (they run inside the held one) and NEVER acquire a lock — exactly today's invariant (verified: handlers never touch `_lock`; only `note_spoken` (212) and `_raise_failed` (76), both OFF the handler path, self-acquire).
- The non-reentrant `Lock` is kept as-is (NOT switched to RLock): RLock would mask the very "called the wrong way" bugs we want surfaced. The `transaction()` boundary makes the discipline visible instead.

**Speak-path latency is unchanged — the explicit argument:**

The loop (`_speak_loop` / `_speak_loop_once`, 939-1024) moves into `host.py` as ONE verbatim unit. Its THREE lock regions are byte-for-byte:
- **Region A** (949 or 970): pop foreground stream's `pop_next()`, claim `_current_item`, capture `cancel_epoch` (M2 — the pop→speak gap), compute mute, compute `_attributed_text` (commits `_last_spoken_session`), snapshot `prev`.
- **blocking `speaker.speak()`** (1002): OUTSIDE the lock — synthesis + afplay never holds the lock.
- **Region B** (1007): re-check `_paused` under the lock (L2 — a FLUSH can't resurrect a flushed item), re-queue-at-front + roll back `_last_spoken_session` (1011-1020), else `note_spoken`.

The producer→consumer hand-off remains the existing per-session `SpeechQueue` (graft from pipeline). There is **no new queue, no actor mailbox, no callback chain** between "pop the item" and "speak it." The only added indirection — the `dict.get` dispatch and the `Ctx` attribute hops — lands exclusively on the **connection thread** (PROSE is dispatched there), never inside the loop or between pop and speak. `pop_next()` stays a direct call on the stream's `SpeechQueue`. `_attributed_text`/`note_spoken` stay direct host methods, not cross-module calls under the lock.

**Net effect:** the per-utterance critical section is identical in lock-held time and identical in indirection. Zero manufactured regression. (Measured perf gate is in §9 — not "sounded fine on-Mac.")

---

## 6. Platform collapse

**Deleted/merged** (verified against `base.py`):
- The 4 single-impl ABCs — `TtsBackend`, `EarconBackend`, `HotkeyBackend`, `SupervisorBackend` — and their dead defaults: `HotkeyBackend.start/stop` no-ops (77-84, which `MacHotkeyBackend` never overrides), `key_codes/mod_masks/default_mods/extra_default_bindings` `return {}` ceremony (57-74), the `reload()` in-process default (86-94), the 3-shape `install()` signature (40), all Windows-rationale prose (2, 76, 89-92, 121).
- The `PlatformBackend` ABC composite collapses to a plain dataclass aggregator of 5 fields.

**What the macOS modules look like:** the existing `platform/macos/{tts,earcon,hotkeys,supervisor}.py` classes lose `(abc.ABC)` and `@abstractmethod` and become plain classes with identical bodies. Optionally flatten the 5-file package to one `macos.py` (dissolving the `hotkeys`↔`supervisor` circular import via shared `_xml_escape`/plist helpers) — *optional cleanup, the ABC deletion is the substance.*

**Zero call-site churn:** every consumer uses `get_platform().<field>.<method>()` (verified across daemon, cli, keymap), so collapsing the ABCs touches no call site.

**How the RaiseBackend seam survives:** `RaiseBackend` (ABC) + `NoopRaiseBackend` move to `platform/raise_base.py` intact — the one genuinely polymorphic abstraction (Mac + Noop, exercised by `test_platform_raise_seam`). The `daemon._raise` lazy-build + `daemon.raise_service` injection point (verified 61-68) are unchanged.

**`get_platform()`** keeps its single `sys.platform == 'darwin'` branch (raise on non-darwin) — that IS the macOS-only seam, so `test_only_factory_branches_on_platform` and `test_no_os_branch_in_core` stay green. `transport.py` is UNMOVED (OS-agnostic stdlib, not a backend — `base.py`'s own docstring already flags it branches separately).

The no-op `start()` becomes an explicit documented no-op on `MacHotkeyBackend` (hotkeyd is a separate process) — by design, now legible at the definition instead of hidden in an ABC default.

---

## 7. State ownership

**Resolved by split-by-locality — NOT a SessionStore.** I verified the pure-state-layer is impossible: `_flush_prose_buffer` reaches config + `speaker.earcon` + `sessions.foreground` + `_wake`. So:

- **Stream-local state stays on `SessionStream`** (the stable leaf), and its *transitions* co-locate there where they touch only that one session: `muted`, `warned_immediate`, `guided`, `waiting_signaled` flags; `options`; `nav_cursor`/`nav_turn` cursor math (the `_nav` index arithmetic becomes `SessionStream.advance_cursor(...)`); `prose_buffer` as data. `reset_for_new_prompt` stays as-is (verified pure field-reset, 27-37).
- **The global ledger + the lock stay host-owned in `SessionState`:** `_lock`, `_streams` registry, `_pending_heard` (id→HistoryEntry, read on the speak thread), `_current_item`, `_last_spoken_session` (cross-session attribution), `_next_id`, `_paused`, `_wake`. These are read/written across threads under the lock; pushing them onto a per-session object would change lock granularity and drag global state into a per-session bag — the exact anti-pattern.
- **The minqueue split is unified by HOST kernel ops, not by the bag:** `_buffer_prose`/`_flush_prose_buffer`/`_drop_pending`/`_enqueue` stay host methods (they need config + speaker + sessions + `_wake`). The fragile straddle (`prose_buffer` on the stream, logic in daemon) resolves to: data on the stream, *all* mutation through host kernel ops under `state.transaction()`. Features call `ctx.flush_prose(session)`; the host op does the lock-correct work.

This is the only consistent resolution given the verified cross-cutting reads — it keeps the leaf a leaf and gives every mutator one home.

---

## 8. User-facing behavior changes (per-item owner approval)

Play `afplay /System/Library/Sounds/Glass.aiff` is not needed here (no hands required) — these are decisions, listed for sign-off:

1. **[NEEDS APPROVAL — user-facing] SET_VOICE / SET_VERBOSITY validation.** Today SET_RATE and SET_MINQUEUE clamp; SET_VOICE and SET_VERBOSITY persist the raw payload unchecked (verified 662-707). Routing all four through one clamp helper means a malformed voice or out-of-vocabulary verbosity is **rejected (no-op)** instead of written to disk. This rejects previously-accepted bad input. **Recommend yes, your call.** Gated separately in Step 6 — until approved, keep current behavior to stay net-green.

2. **[NOT user-facing — defect fix, recommend include] `_signal_speak_failure` traceback.** Verified: it calls `traceback.print_exc` (935) but `traceback`/`sys` are not in scope there, inside `try/except: pass` — so the promised daemon-log traceback is silently lost on every inner speak-loop failure. The error earcon already fires either way, so the **eyes-free experience is unchanged**; the fix only restores a log line. Applied when the loop moves (its own commit, NOT folded into the structural move).

3. **[NOT user-facing] CYCLE_VERBOSITY out-of-range fallback** — reviewed; the unified clamp helper KEEPS the existing reset-to-'everything' behavior. Flagged only to confirm it was checked and left unchanged.

Everything else — the registry, ctx facade, feature extraction, decision/nav dedup, platform collapse, state split — is **behavior-preserving by design** and must produce byte-identical speech/earcon output and ordering (the black-box net is the instrument that proves this).

---

## 9. Migration sequence

Black-box-net-FIRST. Each step ends green; risk increases monotonically; the speak loop and state relocation land LAST.

**STEP 0 — Build the net (no production code moves; load-bearing safety bet).**
- Lock the baseline: add `[tool.pytest.ini_options]` so the unguarded `import numpy` in `test_kokoro.py` stops aborting collection (verified: 650 green with kokoro ignored).
- Grow `test_e2e_pipeline.py` (already real `handle_event → handle_message → FakeSpeaker`) into a per-FAMILY behavior net asserting ONLY on the FakeSpeaker log + STATUS/PING replies. Cover EVERY family the white-box tests own behaviorally: prose ordering, **EARCON turn_done sub-threshold flush**, minqueue batching, decision FIFO + cue, foreground gating, background earcon-only, pause/resume re-queue, mute, pin, 2-level nav seek-and-play, jump-waiting target order, FLUSH cut-on-switch, config STATUS snapshot. Replace `drain_queue`'s reach into `_streams` with a non-blocking `drain_once()` seam on the CURRENT daemon.
- **Critical reconciliation (the cross-cutting insight every critic circled):** the synchronous black-box net is *structurally blind* to the M2/L2/M6/M8/H2 races — it proves message-in→speech-out LOGIC, not thread interleaving. Net-first is about *sequence*, not sufficiency. So Step 0 ALSO adds, and the migration **never retires**, two concurrency guards:
  - a **real-threaded stress test**: threads hammering PAUSE/FLUSH/SET_FOREGROUND/JUMP_WAITING while the *real blocking* loop runs against a fake `say_runner`, asserting no lost/duplicated/resurrected item and no "list changed size";
  - a **deterministic re-entrant FakeSpeaker** whose `speak()` fires PAUSE/FLUSH before returning, asserting the re-queue + `_last_spoken_session` rollback (1011-1020).
- **Bank a measured perf baseline:** micro-benchmark the `enqueue→speak()` critical-section time on the current daemon. The owner's one hard constraint is perf and he can't watch output — this is a measured before/after gate, NOT a listen.

**STEP 1 — `daemon/` package + bootstrap split.** Make `daemon/` a package; move `main`/`ensure_running`/`_arm_faulthandler` to `bootstrap.py`; re-export from `__init__.py`. **Repoint conftest patch targets:** `daemon.py` binds `LOCK_PATH`/`SINGLETON_PATH` as module globals and owns `_SINGLETON`, which conftest patches *on the daemon module* (verified failure class — same as the claude-everywhere `~/.claude` wipe in memory). Moving server/bootstrap makes those patches hit a dead namespace and the relocated code touches the REAL `~/.sonari` lock/singleton under test. This step is NOT a free "pure move" — it requires synchronized conftest edits. Both nets + 650 white-box green.

**STEP 2 — Platform collapse (independent, mechanical, low-risk; proves the net under change).** Delete 4 ABCs + dead defaults; move RaiseBackend+Noop to `raise_base.py`; concrete Mac classes; `get_platform` darwin-assert. Rewrite `test_platform_base`; keep `test_platform_raise_seam`/`test_no_os_branch_in_core` green.

**STEP 3 — Ladder→table calling the SAME private methods (graft from layered).** Introduce `registry.py` + `context.py` + `SessionState` wrapping the existing fields with `transaction()`. `handle_message` delegates to the registry; handlers are thin closures still calling `self._nav`, `self._choice_text`, etc. No body moves. Pure dispatch-shape transform; a mis-routed MsgType shows up immediately in the net.

**STEP 4 — Extract L0 `Server`.** Carve socket/accept/conn/semaphore out; host passes its locked dispatch entry as the callback. `test_daemon_conn` green.

**STEP 5 — Extract lock-INDEPENDENT features first, one family at a time.** `control` (with the unified clamp), `decisions`, `lifecycle`, `navigation`, `playback`, `focus` (with `_waiting_target`), `prose` (with EARCON), `hotkeys` (off-lock reload preserved). After each: delete only the white-box tests whose internal moved AND that have a proven net-equivalent; keep host-pinned methods (`note_spoken`, `_setup_health`, `_dispatch_hotkey`, `_speak_loop_once`, `_stream`, `_enqueue`) as forwarding shims so the ~250 callers stay green. Both nets green after each family.

**STEP 6 — Apply the gated behavior change** (SET_VOICE/VERBOSITY validation) ONLY after owner sign-off, as its own commit. The `_signal_speak_failure` fix is its own separate commit too — never folded into a structural move.

**STEP 7 — Relocate state + speak loop LAST (riskiest, isolated).** Finalize `SessionState` ownership of the ledger; move the loop verbatim into `host.py` with lock regions byte-for-byte. Run the FULL suite + the retained concurrency guards + the perf micro-benchmark (compare to the Step-0 baseline — measured, not heard) + on-Mac `sonari:doctor` smoke.

**STEP 8 — Collapse host to its floor + retire duplicative white-box tests.** Retire ONLY white-box tests with a proven behavior- or concurrency-test equivalent. **Do NOT retire the M2/L2/M6/M8/H2 concurrency tests — they are the permanent race guard the net cannot replace.**

Test blast-radius is handled by: (a) over-building the net + concurrency guards in Step 0 before any deletion; (b) forwarding shims keeping ~250 white-box callers green through Step 7; (c) per-test net-equivalence review before deletion, never blind.

---

## 10. Open risks & honest weak spots

1. **The host/feature boundary is a documentation boundary over shared state for the loop, not a true isolation boundary.** `SessionState` + the speak loop are joined by one non-reentrant lock and shared rebindable scalars. The "8 clean features + a thin host" headline undersells that the riskiest ~20% is still one tightly-coupled vertical unit. I keep it honest: that unit is the host, and its lock discipline (`transaction()` + "features never lock") is its contract. The `transaction()` boundary makes the discipline structural and visible, but does not make the loop independently testable from the state — they are tested together by the retained concurrency guards.

2. **`Ctx` is wider than a "narrow facade."** PAUSE/FLUSH need `_paused`/`_wake`/`_current_item`. I do NOT hide this behind a "no lock surface" story (the feature candidate's defect): `Ctx` exposes `.state` for those, documented in §1. The honest claim is "features reach state only through `ctx.state` under the held transaction," not "features can't see concurrency state."

3. **The concurrency net is probabilistic, not exhaustive.** The real-threaded stress test catches races by interleaving pressure, not by proving a specific schedule; the deterministic re-entrant FakeSpeaker pins L2/the rollback deterministically but is one scenario. A novel interleaving could still slip through. Mitigation: move the loop verbatim (don't rewrite it), land it last, keep the guards permanent, and gate Step 7 on the on-Mac smoke + measured perf.

4. **`cli.py` (the repo's #2 churn source, 40 commits) is untouched.** This axis addresses `daemon.py` and the platform ceremony only. The install/lifecycle cluster (C) and the assembler↔cleaner contract fragility (§8.4 of the digest) are out of scope — a real limitation, stated openly.

5. **The `registry.py` table centralizes routing knowledge** (the new hot-edit surface). Better than a 400-line if-chain (adding a MsgType is one row + one handler), but it is "one file every control feature edits." I accept this as a feature, not a flaw — it is where future control-surface churn *should* land, behind a stable interface.

6. **Forwarding shims keep dead method names alive on the host through Step 7**, which is mildly ugly mid-migration. They are deleted in Step 8 once the net + per-feature tests are the standing suite; the alternative (rewriting 250 white-box tests in lockstep) is worse and violates the net-first mandate.

---

**Relevant absolute paths:** source under `/Users/Nima.Hakimi/Projects/private/claude-tts/src/sonari/` (`daemon.py` 1236, `session_stream.py`, `platform/base.py`); tests under `/Users/Nima.Hakimi/Projects/private/claude-tts/tests/` (`test_e2e_pipeline.py` is the net seed). The proposed tree adds `src/sonari/daemon/` (package) and `src/sonari/platform/raise_base.py` + `macos.py`.