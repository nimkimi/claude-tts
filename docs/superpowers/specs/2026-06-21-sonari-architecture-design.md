# Sonari Stage 2 — Architecture Redesign (Design Spec)

**Date:** 2026-06-21 · **Status:** approved design, pre-implementation · **Repo:** `claude-tts` (macOS-only)

> This is a self-contained design spec. The supporting evidence (subsystem maps,
> git-churn analysis, the 4-candidate design exploration + adversarial critiques)
> is preserved under `docs/superpowers/research/2026-06-20-sonari-stage2-*` but you
> do **not** need it to read this — the load-bearing facts are carried inline.

---

## 1. Goal

Sonari is now single-platform (macOS-only) after Stage 1 removed Windows. Stage 2
raises code quality by giving Sonari **the structure that actually fits it** —
discovered from the real code, not a pattern picked in advance.

Three explicit goals, in priority order:

1. **Tame the god object.** `daemon.py` (1,236 lines, 35% of all source churn) holds
   ~20 distinct concerns. Break it into small, independently understandable, testable
   units — without regressing behavior or speak-path latency.
2. **Be agentic-development friendly.** Small focused files, explicit contracts,
   localized blast radius, a guard rail that catches missing wiring, and a behavior
   test seam — so Claude/agents (and the idea-pipeline build engine) can add features
   reliably and in parallel.
3. **Stay cheaply portable.** macOS is the only platform *today*, but Windows/Linux
   may be re-added once the product matures. Keep the core OS-agnostic and the
   platform seam a clean one-backend-away from multi-platform — while deleting the
   dead cross-platform ceremony that exists for platforms that don't currently ship.

### Owner decisions (the hard inputs this spec is built to)

- **Scope = open re-architecture.** Internal contracts and some behavior may change
  where it clearly improves the design; every *user-facing* behavior change is listed
  explicitly (§9) and individually approved. The one hard product constraint is
  **runtime performance: the per-utterance speak path must not gain overhead.**
- **Platform = keep a lean contract + seam, delete the dead ceremony.** (Adjusted from
  an initial "macOS-only is permanent → collapse the ABCs" once the owner flagged a
  likely future Windows/Linux re-add. See §6.)
- **Tests = black-box-net-first for *sequencing*, with permanent white-box race
  guards.** (The net is structurally blind to the concurrency races — a correction
  forced by the evidence. See §8.)

---

## 2. Evidence basis (the facts this design answers to)

All independently re-verified against source.

- **`daemon.py` is the center of gravity** by three converging measures: largest file
  (1,236 lines), highest churn (47 of 136 source commits = 35%), and the import +
  execution hub. It holds ~20 concerns (§4 table) including a **403-line / 27-branch
  `handle_message` dispatch ladder** and an **86-line speak loop** that is the tightest
  state-sharing knot in the file (shared `_lock`, `_current_item`, `_last_spoken_session`,
  `_pending_heard` across a server thread, a speak thread, and reload threads).
- **The other modules are clean, stable, well-bounded leaves** — `queue`, `assembler`,
  `cleaner`, `sessions`, `session_stream`, `speaker`, `history`, `protocol`, `config`
  (each 2–3 commits, pure or cohesive). The mess is **concentrated**, not spread.
- **Three change-clusters** (confirmed by imports *and* git co-change):
  (A) the daemon runtime hub (daemon ↔ queue 0.90 / config 0.86 / protocol 0.80 /
  session_stream 0.67); (B) a daemon-independent hotkey/keymap cluster; (C) an
  install/lifecycle cluster on `cli.py`.
- **The platform layer is mostly dead scaffolding.** 4 of 5 backend ABCs have a single
  macOS impl with no-op defaults nothing overrides and Windows rationale still in the
  docstrings; only `RaiseBackend` is genuinely polymorphic (Mac + Noop). Windows was
  added and deleted inside a 2-day window — re-adding a platform is a bounded effort.
- **The "black-box net" is blind to concurrency.** `test_e2e_pipeline.py`'s `drain_queue`
  helper reimplements the speak loop *synchronously* (no threads, no lock, no
  cancel-epoch, no pause-requeue) — so it proves message-in→speech-out *ordering*, not
  thread *interleaving*. The real guards for the M2/L2/M6/M8/H2 races are the white-box
  concurrency tests.
- **The test isolation has a known foot-gun.** `conftest.py` monkeypatches by-value
  module globals (`LOCK_PATH`, `SINGLETON_PATH`, `_SINGLETON`, …) *on the daemon module*.
  Moving those bindings to new modules makes the patches hit a dead namespace and the
  relocated code touches the **real `~/.sonari`** under test — the same failure class
  that once wiped a real `~/.claude` in a sibling project. Any module move must
  repoint conftest in the same step.

---

## 3. The chosen shape (and why)

Four decomposition philosophies were explored independently and critiqued across three
adversarial lenses (maintainability, perf/concurrency, migration-risk). **Feature-primary
won**, decisively on the lens that matters most given the perf constraint:

| Approach | Maintainability | Perf/Concurrency | Migration |
|---|---|---|---|
| **Feature-primary** ✅ | **76** | **90** | **74** |
| Dataflow/pipeline | 66 | 82 | 77 |
| Actor/ownership | 61 | 74 | 68 |
| Layered | 62 | 68 | 76 |

**Thesis:** `daemon.py` becomes a **thin host** that owns the irreducible concurrency
core; the 27-branch ladder dissolves into **per-feature handler modules** behind a
uniform `(ctx, msg) → reply|None` **dict registry**; the host/feature boundary is drawn
precisely **at the lock**.

Three grafts from the runners-up (each justified by a critique):
- from **actor**: name the lock owner explicitly and give it a `with state.transaction():`
  boundary, so "callers hold the lock" is *structural*, not convention;
- from **pipeline**: the producer→consumer hand-off stays the existing per-session
  `SpeechQueue` — no new queue, no mailbox, no callback chain on the hot path;
- from **layered**: clean extraction of the L0 transport/server, and a migration step
  that turns the ladder into a table *calling the same private methods* before any body
  moves.

Why not the others (each verified against source):
- **Layered** — its keystone "pure state layer" is *impossible*: `_flush_prose_buffer`
  reaches `config` + `speaker.earcon` + `sessions.foreground` + `_wake`. Flush is
  inherently cross-cutting, so a downward-only state layer can't exist.
- **Pipeline** — its "units hold references, not ownership" claim is false for the two
  *rebindable scalars* (`_current_item`, `_last_spoken_session`) the speak loop
  reassigns and handlers read; Python can't pass a write-through reference to a scalar
  attribute, so the knot is relocated, not isolated.
- **Actor** — its `SessionState` "only object that takes the lock" interface can't
  support the "dispatch runs under the lock" invariant without leaking the raw lock to
  other modules; the `transaction()` graft fixes exactly this.

**Honest, axis-independent weak spot:** the speak loop **+ its shared cross-thread state
is one irreducible unit.** *No* decomposition makes it cleanly separable — all four
candidates hit this same wall. The design does not pretend otherwise: that unit *is* the
host, it moves **verbatim**, lands **last**, and is guarded by **permanent** race tests.

---

## 4. Target architecture

```
src/sonari/daemon/
  host.py          # SpeechDaemon: the concurrency core — lock, speak loop,
                   #   stream registry, kernel ops, dispatch entry
  state.py         # SessionState: the lock owner + transaction() boundary +
                   #   the global ledger (_streams, _pending_heard, _current_item,
                   #   _last_spoken_session, _next_id, _paused, _wake)
  server.py        # socket lifecycle, conn concurrency (BoundedSemaphore + M8
                   #   permit-leak recovery), token handshake, framing
  registry.py      # HANDLERS dict + @handler decorator + assert_complete() guard
  context.py       # Ctx: the facade features receive (documents its REAL surface)
  bootstrap.py     # main(), ensure_running(), _arm_faulthandler(), singleton guard
  __init__.py      # back-compat re-exports + side-effect imports that populate
                   #   the registry
  features/
    prose.py       # PROSE, TOOL, EARCON, FLUSH        (data + turn boundary)
    decisions.py   # CHOICE, PLAN, PERMISSION, REREAD_OPTIONS + the 5 pure builders
    navigation.py  # NAV (within-turn + cross-turn) + shared _seek_and_play
    playback.py    # PAUSE, MUTE, PIN_TOGGLE, STOP, SKIP, JUMP_DECISION
    focus.py       # JUMP_WAITING + raise (over RaiseBackend) + _waiting_target
    control.py     # SET_*, CYCLE_VERBOSITY, STATUS, PING + setup-health + clamp helper
    lifecycle.py   # SESSION_START, SESSION_END, SET_FOREGROUND
    hotkeys.py     # RELOAD_KEYMAP + hotkey start/stop/reload (off-lock reload kept)

src/sonari/platform/
  contracts.py     # the 5 backend contracts (Protocol/thin ABC: signatures only)
                   #   + RaiseBackend + NoopRaiseBackend (the one polymorphic seam)
  macos.py         # concrete Mac backends; make_backend() -> PlatformBackend(...)
  __init__.py      # get_platform(): the single darwin-assert branch
  transport.py     # UNMOVED — OS-agnostic stdlib, not a backend
```

### Unit contracts

| Unit | Responsibility | Public contract | Depends on |
|---|---|---|---|
| `host.py` (`SpeechDaemon`) | Owns the speak loop + stream registry + kernel ops; runs dispatch under the lock | `SpeechDaemon(speaker, sessions, config, raise_service=None)`; `handle_message(msg)→reply\|None` (thin: lookup + call under `state.transaction()`); `run()/stop()`; kernel ops (`_enqueue`, `_stream`, `note_spoken`, `_attributed_text`, `_buffer_prose`, `_flush_prose_buffer`, `_drop_pending`) | `state`, `server`, `registry`, `context`, the leaves |
| `state.py` (`SessionState`) | The lock owner + the global ledger | `transaction()` (≡ `with _lock`); typed access to `streams`, `current_item`, the rebindable scalars; mutators assume the transaction is held | `session_stream`, `queue` |
| `server.py` (`Server`) | Accept thread + bounded conn pool + token/framing; M8 recovery | `Server(dispatch, token_provider)`; `run()/stop()` (calls `dispatch` under the host lock) | `transport`, `protocol`, `paths` |
| `registry.py` | MsgType→handler table + completeness guard | `HANDLERS: dict`; `@handler(MsgType.X)`; `dispatch(ctx, msg)`; `assert_complete(known_types)` | `protocol` |
| `context.py` (`Ctx`) | The facade handlers receive | `Ctx(host)` exposing `.speaker/.sessions/.config/.history/.stream(s)/.enqueue/.flush_prose/.drop_pending/.raise()/.session/.verbosity` **and `.state`** for the concurrency fields handlers legitimately need | `host` |
| `bootstrap.py` | Process lifecycle, distinct from running behavior | `main()`, `ensure_running()`, `_arm_faulthandler()` | `host`, `platform`, `paths`, `config`, `speaker`, `sessions` |
| `features/*.py` | One MsgType family each | each `@handler(T) def on_x(ctx, msg)→reply\|None`; runs UNDER the held lock; NEVER acquires a lock | `context` + family-specific leaves |
| `platform/contracts.py` | The backend contracts + the one real seam | 5 `Protocol`s (tts/earcon/hotkey/supervisor) + `RaiseBackend`(ABC) + `NoopRaiseBackend` | — |
| `platform/macos.py` | Concrete Mac backends | `make_backend() → PlatformBackend(tts, earcon, hotkey, supervisor, raise_backend)` | `contracts` |

Every unit passes the independence test (state what it does / how to use it / what it
depends on, without reading internals) **except** the `host` + `state` pair, which is the
one irreducible concurrency unit (§5) — treated and tested as one bounded thing.

---

## 5. Dispatch table & the concurrency model

### Dispatch ladder → dict registry (provably equivalent)

Every branch in the 403-line ladder is a top-level `if t == X: …; return` (not `elif`),
so branches are mutually exclusive and order-independent. `HANDLERS.get(t, _ignore)`
reproduces the exact control flow, including the trailing unknown-type `return None`.

```python
# registry.py
HANDLERS = {}
def handler(t):
    def deco(fn): HANDLERS[t] = fn; return fn
    return deco
def dispatch(ctx, msg):
    return HANDLERS.get(msg.get("type"), _ignore)(ctx, msg)

# host.py
def handle_message(self, msg):
    return dispatch(self._ctx, msg)   # caller already holds state.transaction()
```

- **Heterogeneity preserved, not flattened.** `PING` (`return {"ok": True}`) and
  `JUMP_WAITING` (~40 lines) are both just rows; body size lives in the feature module.
- **Shared preamble computed once.** `session` and `verbosity` are derived once on `Ctx`
  per message and read as `ctx.session` / `ctx.verbosity` — handlers don't re-derive them.
- **Three exceptions encoded explicitly, never normalized:**
  1. handlers that return `None` and mutate (PROSE/EARCON) — ordinary rows;
  2. the only reply-producing rows (STATUS/PING) — `handle_message` returns the value;
  3. **RELOAD_KEYMAP runs off-lock** — its handler returns fast under the lock but
     delegates real work to a thread serialized by `_reload_lock` (the H2 dark-hotkey
     race fix). A test pins that the reload work runs off the main lock.
- **Completeness guard.** `assert_complete()` checks every `MsgType` has a handler. A
  dropped `@handler` registration would otherwise be a *silent* no-op; this makes it a
  red test. (This is the agent guard rail from goal #2.)

### Speak loop & lock model — the hard rule

- **Exactly ONE lock**, owned by `SessionState`. No per-feature / fine-grained locks.
- **`with state.transaction():` is the only way to hold the lock.** The server and the
  hotkey dispatch open the transaction around `dispatch`; the speak loop opens it for its
  own regions. **Features never open a transaction and never acquire a lock** — exactly
  today's invariant (verified: handlers never touch `_lock`; only `note_spoken` and
  `_raise_failed`, both *off* the handler path, self-acquire — they keep doing so).
- **Keep the non-reentrant `Lock` (not `RLock`).** RLock would mask "called the wrong
  way" bugs; the `transaction()` boundary makes the discipline visible instead.

**Speak-path latency is unchanged — the explicit argument.** The loop moves into
`host.py` as ONE verbatim unit, its three lock regions byte-for-byte:
- **Region A** (under lock): `pop_next()` the foreground stream, claim `_current_item`,
  capture `cancel_epoch` (M2 — the pop→speak gap), compute mute, compute
  `_attributed_text` (commits `_last_spoken_session`), snapshot `prev`.
- **`speaker.speak()`** — OUTSIDE the lock (synthesis + afplay never hold the lock).
- **Region B** (under lock): re-check `_paused` (L2 — a FLUSH can't resurrect a flushed
  item), re-queue-at-front + roll back `_last_spoken_session`, else `note_spoken`.

The producer→consumer hand-off stays the existing per-session `SpeechQueue`. The only
added indirection — the `dict.get` dispatch and `Ctx` attribute hops — lands exclusively
on the **connection thread**, never between pop and speak. `pop_next` /
`_attributed_text` / `note_spoken` stay direct calls, not cross-module hops under the
lock. Net: the per-utterance critical section is identical in lock-held time and
indirection. Gated by a **measured** before/after micro-benchmark (§8), not an ear test.

> **Phase-2 measured correction (2026-06-21).** Relocating the ledger to
> `SessionState` behind *byte-identical property shims* measured **+10%** on the
> `enqueue+pop` hot path (884 vs 805 ns) — over the perf constraint. The
> shipped approach keeps the ledger on `SessionState` but re-sources the host's
> hot path (speak loop + kernel ops) to `self._state._X` (one attribute load,
> not a descriptor call); property shims remain only for cold-path callers.
> Measured perf-neutral (~794 ns). The loop's lock regions are therefore
> logically identical, not literally byte-identical — verified by the permanent
> concurrency guards + this measured gate.

---

## 6. Platform stance (adjusted)

The pluggability *machinery* is **kept**; the dead *ceremony* is deleted; the backend
*contracts* are kept lean and explicit so a future port is a clean drop-in.

**Kept (this is what makes Sonari portable):**
- `get_platform()` with its single `sys.platform == 'darwin'` branch — the one OS
  dispatch point. (`test_no_os_branch_in_core` keeps the core OS-agnostic — gold for a
  future port.)
- `PlatformBackend` as a plain dataclass aggregator of 5 fields; `make_backend()` factory.
- The 5 backend **contracts** in `platform/contracts.py` — as `typing.Protocol` (or thin
  ABCs), **signatures only**, no bodies — a type-checkable checklist of what any platform
  must implement.
- `RaiseBackend` (ABC) + `NoopRaiseBackend` — the one genuinely polymorphic seam (Mac +
  Noop), exercised by `test_platform_raise_seam`.

**Deleted (dead speculation for platforms that don't ship today):**
- The Windows-rationale docstrings (`base.py:2,76,89-92,121`).
- The no-op `start/stop` defaults that `MacHotkeyBackend` never overrides.
- The `key_codes/mod_masks/default_mods/extra_default_bindings → {}` stubs (for absent
  platforms).
- The in-process `reload()` default (written for Windows; macOS overrides it).
- The 3-shape `install()` signature ceremony.

**Rationale:** the contract is nearly free (~40 lines of signatures, zero runtime cost)
and is exactly what makes re-adding Windows/Linux a clean, type-checked drop-in. YAGNI
correctly kills the *speculative stubs* (which you'd rewrite fresh against the real OS
API anyway), not the *contract*. The macOS module becomes a complete reference
implementation — the best template for a future backend. Call sites are unchanged
(everyone uses `get_platform().<field>.<method>()`), so this touches no consumer.

Optional cleanup (not the substance): flatten `platform/macos/{tts,earcon,hotkeys,
supervisor}.py` into one `macos.py`, dissolving the `hotkeys`↔`supervisor` circular
import via shared `_xml_escape`/plist helpers.

---

## 7. State ownership (split-by-locality)

A single "SessionStore" is impossible (flush is cross-cutting, §3). Resolution:

- **Stream-local state + its transitions stay on `SessionStream`** (the stable leaf)
  where they touch only one session: the `muted` / `warned_immediate` / `guided` /
  `waiting_signaled` flags; `options`; the `nav_cursor` / `nav_turn` cursor math (the
  `_nav` index arithmetic becomes `SessionStream.advance_cursor(...)`); `prose_buffer`
  as data. `reset_for_new_prompt` stays as-is.
- **The global ledger + the lock stay host-owned in `SessionState`:** `_lock`, `_streams`,
  `_pending_heard` (read on the speak thread), `_current_item`, `_last_spoken_session`,
  `_next_id`, `_paused`, `_wake` — all read/written across threads under the lock.
- **The minqueue straddle resolves to: data on the stream, all mutation through host
  kernel ops.** `_buffer_prose` / `_flush_prose_buffer` / `_enqueue` / `_drop_pending`
  stay host methods (they need config + speaker + sessions + `_wake`); features call
  `ctx.flush_prose(session)`.

This keeps the leaf a leaf and gives every mutator one home.

---

## 8. Test strategy

Net-first for **sequencing**, with **permanent** concurrency guards because the net is
structurally blind to races (§2).

- **Grow the black-box net.** Extend `test_e2e_pipeline.py` (real
  `handle_event → handle_message → FakeSpeaker`) into a per-FAMILY behavior net asserting
  only on the FakeSpeaker log + STATUS/PING replies, covering every family the white-box
  tests own behaviorally (prose ordering, EARCON turn_done sub-threshold flush, minqueue
  batching, decision FIFO + cue, foreground gating, background earcon-only, pause/resume
  re-queue, mute, pin, 2-level nav seek-and-play, jump-waiting order, FLUSH cut-on-switch,
  config STATUS snapshot). Replace `drain_queue`'s reach into `_streams` with a
  non-blocking `drain_once()` seam.
- **Add two PERMANENT concurrency guards (never retired):**
  - a **real-threaded stress test** — threads hammering PAUSE/FLUSH/SET_FOREGROUND/
    JUMP_WAITING while the *real blocking* loop runs against a fake `say_runner`,
    asserting no lost/duplicated/resurrected item and no "list changed size";
  - a **deterministic re-entrant FakeSpeaker** whose `speak()` fires PAUSE/FLUSH before
    returning, pinning the re-queue + `_last_spoken_session` rollback.
- **Bank a measured perf baseline** — micro-benchmark the `enqueue→speak()`
  critical-section time on the *current* daemon, before any risky move; compare after.
- **Forwarding shims keep the ~250 white-box callers green** through the migration.
  ⚠️ **Verify before betting on it:** some daemon tests *set or assert on* rebindable
  scalars (`_current_item`, `_last_spoken_session`), not just *call* methods — those need
  read/write **property** shims, not plain forwards. Spot-check 2–3 representative daemon
  tests during Phase-1 planning to size the shim layer correctly.
- **Fix the suite-abort foot-gun** — a bare `pytest` aborts at collection because
  `test_kokoro.py`'s top-level `import numpy` is unguarded. The working green baseline is
  **682** (`pytest --ignore=tests/test_kokoro.py`); add `[tool.pytest.ini_options]` (or a
  skip guard) so collection no longer aborts without the manual ignore.
- **Repoint conftest in the same step as any module move** (the `~/.sonari` foot-gun, §2).

---

## 9. User-facing behavior changes

1. **[APPROVED — user-facing] `SET_VOICE` / `SET_VERBOSITY` validation.** Today
   `SET_RATE`/`SET_MINQUEUE` clamp but `SET_VOICE`/`SET_VERBOSITY` persist raw payload
   unchecked. Routing all four through one clamp/validate helper means malformed input is
   **rejected (no-op)** instead of written to disk. Applied as its own commit, gated until
   it lands so the suite stays net-green before then.
2. **[NOT user-facing — log-only fix, included] `_signal_speak_failure` traceback.** It
   calls `traceback.print_exc` where `traceback`/`sys` aren't in scope, inside
   `try/except: pass` — so the promised daemon-log traceback is silently lost on every
   inner speak-loop failure (the error earcon still fires, so the eyes-free experience is
   unchanged). Fix restores the log line, as **its own commit**, never folded into a
   structural move.
3. **[unchanged, checked] `CYCLE_VERBOSITY` out-of-range fallback** keeps its reset-to-
   `everything` behavior under the unified clamp helper.

Everything else is **behavior-preserving by design** — byte-identical speech/earcon
output and ordering, proven by the black-box net.

---

## 10. Migration sequence — phased

Net-first; risk increases monotonically; the speak loop + state relocation land **last**.
**This spec authorizes writing the Phase-1 plan only.** Phase 2 (the dangerous carve) is
planned *after* Phase 1's net + guards exist and are green — because what that plan can
safely assume depends on what the net actually proves.

### Phase 1 — safe, mechanical, high-value

- **Step 0 — Build the safety net** (no production code moves): grow the black-box net,
  add the two permanent concurrency guards, bank the perf baseline, fix the pytest
  collection abort. *Load-bearing — everything downstream rests on this.*
- **Step 1 — `daemon/` package + bootstrap split.** Move `main`/`ensure_running`/
  `_arm_faulthandler` to `bootstrap.py`; re-export from `__init__.py`. **Repoint conftest
  patch targets in the same commit** (the `~/.sonari` foot-gun). Not a free "pure move."
- **Step 2 — Platform contract + collapse** (independent, mechanical): create
  `platform/contracts.py` (lean Protocols + RaiseBackend/Noop), delete the dead defaults
  + Windows prose, make the Mac classes concrete, keep `get_platform()`'s darwin branch.
  Rewrite `test_platform_base`; keep `test_platform_raise_seam` / `test_no_os_branch_in_core`.
- **Step 3 — Ladder→table calling the SAME private methods.** Introduce `registry.py` +
  `context.py` + `SessionState` (wrapping today's fields with `transaction()`).
  `handle_message` delegates to the registry; handlers are thin closures still calling
  `self._nav`, `self._choice_text`, etc. No bodies move — pure dispatch-shape transform.
- **Step 4 — Extract `Server` (L0).** Carve out socket/accept/conn/semaphore; the host
  passes its locked dispatch entry as the callback.
- **Step 5 — Extract the lock-independent features, one family at a time:** `control`
  (with the unified clamp), `decisions`, `lifecycle`, `navigation`, `playback`, `focus`
  (with `_waiting_target`), `prose` (with EARCON), `hotkeys` (off-lock reload preserved).
  After each: delete only the white-box tests with a proven net-equivalent; keep
  host-pinned methods as forwarding shims so callers stay green.
- **Step 6 — Apply the gated behavior change** (`SET_VOICE`/`SET_VERBOSITY` validation,
  owner-approved) and the `_signal_speak_failure` fix — each as its own commit.

### Phase 2 — the riskiest carve (planned separately, after Phase 1 is green)

- **Step 7 — Relocate state + speak loop LAST.** Finalize `SessionState` ownership; move
  the loop into `host.py` verbatim, lock regions byte-for-byte. Gate on: full suite + the
  permanent concurrency guards + the perf micro-benchmark vs the Step-0 baseline + an
  on-Mac `sonari:doctor` smoke (driven by a fabricated fresh session_id — never the owner
  as a test harness).
- **Step 8 — Collapse the host to its floor; retire only duplicative white-box tests**
  with a proven behavior- or concurrency-test equivalent. **Never retire the M2/L2/M6/M8/H2
  concurrency tests** — they are the permanent race guard the net cannot replace.

---

## 11. Out of scope (explicit)

- **`cli.py` + the install/lifecycle cluster (C)** — the repo's #2 churn source (40
  commits) is **not** restructured here. It's lower-risk, separable, and the install path
  is the one that can wipe real state — kept out of this blast radius. Targeted for a
  fast-follow **Stage 3** (its real duplication — atomic-write ×4, `_read_install_record`
  in both cli + daemon, Swift-compile ×2, `_xml_escape` ×2 — is captured for then).
- **The assembler↔cleaner implicit contract fragility** (the cleaned-buffer re-slice) —
  real but the assembler is stable; not churned here without cause.
- **Re-adding Windows/Linux** — out of scope now; the design keeps it *cheap* (§6), not
  *done*.

---

## 12. Open risks & honest weak spots

1. **The host/feature boundary is a documentation boundary over shared state for the
   loop**, not true isolation. The riskiest ~20% is still one tightly-coupled vertical
   unit (host + state, one non-reentrant lock, shared rebindable scalars). Mitigation:
   `transaction()` makes the discipline visible; the loop moves verbatim; permanent race
   guards test host + state together.
2. **`Ctx` is wider than a "narrow facade"** — PAUSE/FLUSH need `_paused`/`_wake`/
   `_current_item`, exposed honestly via `ctx.state` (not hidden behind a false "no lock
   surface" story).
3. **The concurrency net is probabilistic, not exhaustive** — the stress test catches
   races by interleaving pressure; a novel schedule could still slip. Mitigation: move
   verbatim, land last, keep guards permanent, gate on the on-Mac smoke + measured perf.
4. **`registry.py` centralizes routing** — the new hot-edit surface. Accepted as a
   *feature*: it's where control-surface churn *should* land, behind a stable interface
   (one row + one handler beats a 400-line ladder edit).
5. **Forwarding shims keep dead method names alive on the host through Phase 1** — mildly
   ugly mid-migration; deleted in Step 8. The alternative (rewriting 250 tests in
   lockstep) is worse and violates net-first.

---

## 13. Definition of done (Stage 2)

- `daemon.py` is decomposed into the §4 units; no single new file approaches the old
  1,236-line / 403-branch scale; each unit passes the independence test.
- The platform layer is honest about being macOS-only today while keeping the §6 contract
  + seam; `test_no_os_branch_in_core` and `test_platform_raise_seam` green.
- The black-box net + the two permanent concurrency guards exist and are green; the perf
  micro-benchmark shows no speak-path regression vs the Step-0 baseline.
- Full suite green (682 baseline) on the real macOS runtime; `sonari:doctor` green; the
  daemon speaks (verified via a fabricated fresh session, not the owner).
- The one approved behavior change + the log-only fix landed as isolated commits.
- Work done on a Stage-2 branch, merged to **local** `main` (never pushed; no remote PR).
