# Sonari Voice-Arbitration Rebuild — Campaign Plan (the plan of plans)

> **For agentic workers:** this is the **decomposition + sequencing** for the decision-layer rebuild. Each sub-project below gets its **own** detailed TDD plan (`superpowers:writing-plans`) written just-in-time, then built via `superpowers:subagent-driven-development` with per-task + whole-branch review — the same A/B/C/D pattern the cockpit grammar shipped on. Do **not** build from this file directly; it sets the boundaries and order.

**Goal:** Rebuild Sonari's voice-arbitration decision layer to the behavioral spec, fixing CONC-1/FOCUS-1/B5-1/voice-monopoly at the root, without rewriting the daemon machinery.

**Oracle:** `docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md` (the WHAT). **Gap-check + decisions:** `docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-reconciliation.md` (the keep/rebuild map; read both before each sub-project plan).

**Baseline:** `836 passed, 1 skipped` on `design/voice-arbitration`. **Branch root:** `2a1af7f`.

## Global Constraints (every sub-project inherits these)

- **Keep the machinery, rebuild only the decision layer.** Do NOT touch the mechanism of: per-session streams (`session_stream.py`), `SpeechQueue` (`queue.py`), `ProseAssembler`, the speak-loop **pop+claim+speak+note_spoken** core and cancel-epoch/barge-in (`host.py`/`speaker.py`), `SessionHistory` **storage** (extend, never replace), the dispatch/registry/server/Ctx glue.
- **The two concurrency guards (`tests/test_concurrency_guards.py`) stay green at every commit.** They are PERMANENT. Any speak-loop change adds itself to their hammer set.
- **TDD, spec as the test oracle.** Every observable in §5–§11 of the spec becomes a test. Red → green → commit, bite-sized.
- **macOS-only.** Python 3 / `say` / `afplay` / the `sonari-hotkeyd` Swift binary. No new runtime deps.
- **Decisions ratified 2026-06-29 (binding — don't re-open):** R6 = **Policy A** (a submit takes the voice iff the voice is idle OR the submit is from the *speaker*; else it dings + accrues, reached via ⌃⌘J/⌃⌘Tab — focus is NOT used on the preempt path). Verbosity = **global**. Cycle-onto-muted = **lift the hold + keep-go to another active session** (navigating never un-mutes). Catch-up chord = **co-design with Nima at SP5 build time**.
- **Deploy is Nima's step** (`./bin/sonari install` from a real GUI Terminal). Live audio feel is his ears — never use him as a mechanical-repro harness.

## The conceptual spine (what the whole campaign does)

Split the one overloaded `foreground` pointer into three independent concepts the spec names: **speaker** (the session the voice is reading — set by selection + deliberate overrides), **workspace** (the front terminal + keyboard — `focused_session()` with a `foreground` fallback), and the per-session **frontier** (a monotonic "furthest heard" mark, separate from the browse cursor). Keep-going moves only the speaker; deliberate actions move both; the workspace is authoritative for *answering* and *raising*. Everything else is a corollary.

---

## Sub-projects (each = one writing-plan + one subagent-driven build + review + merge)

### SP1 — Speaker / workspace split + deliberate-action coupling  *(foundation; behavior-preserving + the raise/target fixes)*
**Closes:** R5 (deliberate half), R10, R12 (window rule), C2 (⌃⌘D must raise), the cycle-raise CHANGE, R8 (kept).
**Scope:**
- Introduce an explicit **`speaker`** pointer the speak loop reads (`host.py:382,413` stop reading `foreground()` directly) and an explicit **`workspace`** resolver = `focused_session()` else `foreground()` (`sessions.py`).
- Make the three deliberate actions set the speaker **and** move the workspace: **cycle (⌃⌘Tab)** adds a window raise (today `focus.py:91` is "soft, no raise"); **⌃⌘D (`jump_decision`)** adds a window raise (today `playback.py:85-114` never calls `_raise()`); **jump (⌃⌘J)** already raises (keep).
- Repoint **`answer_permission`** (`decisions.py:185`) to the **workspace**, with a fallback that is the last deliberately-acted session — **never** the auto-advancing speaker (M3). Fail-closed unchanged.
- No keep-going yet: the speaker still changes only on a deliberate action (same observable behavior as today), so this lands as a clean refactor that the guards + existing tests still pass.
**Depends on:** nothing. **Risk:** low-moderate (refactor; guards green). **Why first:** every later SP assumes speaker ≠ foreground.

### SP2 — Keep-going + Policy-A preempt  *(the CONC-1 fix; riskiest)*
**Closes:** R4, R6 (Policy A), R2 (now for the right reason), §7 cross-session ordering, §14 default.
**Scope:**
- Cross-session **keep-going selection**: when the speaker hits its live edge and is idle, the speak loop selects the next session with unheard output (default: longest-waiting-first, §14) and makes it the speaker — **without moving the workspace** (D10). The selection **scan must be atomic with the pop+claim under `self._lock`** (M1) — scan + pop + claim + cancel-epoch in one locked block.
- **Idle predicate:** extract the live-edge/idle check out of `_voice_busy_elsewhere` (`host.py:139-147`) and keep it (M2); it powers both keep-going and Policy-A.
- **Policy-A preempt:** a submit (`SET_FOREGROUND` from `UserPromptSubmit`) takes the voice immediately iff the voice is **idle** OR the submitting session **is the speaker**; otherwise it dings + accrues. **Cut the #65 seize-gate policy** (`lifecycle.py:66`), keep the idle predicate. Specify the **SESSION_START** path (M4): a fresh session takes the voice only if idle (never cuts a live readout).
- Add the cross-session selector to the concurrency-guard hammer set.
**Depends on:** SP1 (speaker pointer + idle). **Risk:** HIGH (speak-loop selection + the guards).

### SP3 — Voice-state machine + ⌃⌘W + sound rewire  *(R7)*
**Closes:** R7, the §6 voice-global states (flowing / quiet-hold / stopped-all), the §8 ⌃⌘S/⌃⌘M/⌃⌘W CHANGEs, §11 ding.
**Scope:**
- A **voice-global state** (flowing / quiet-hold / stopped-all). **⌃⌘S** enters quiet-hold (suppresses keep-going for everyone; new output only dings) on top of its existing per-session freeze; **re-engage** (submit/jump/cycle) lifts it. **Cycle-onto-muted** = lift the hold + keep-go to another *active* session while the workspace sits on the muted one (ratified). **⌃⌘M** = stopped-all.
- **⌃⌘W** reports the voice state (`control.py:120-163` adds flowing/quiet-hold/stopped-all to the spoken status).
- **Sound rewire (§11):** the background "something landed" ding becomes the **turn-completion** earcon for **non-speaking** sessions, and **suppress `turn_done` for the speaker** (`prose.py:53-60` fires it for all today); retire the `waiting` earcon.
**Depends on:** SP2 (keep-going to suppress). **Risk:** moderate.

### SP4 — Transcript frontier + two-position marker + tool fidelity  *(R3/§9/§10)*
**Closes:** R3, §9 (verbosity-as-readout-filter + tool fidelity), §10 (frontier + browse cursor).
**Scope:**
- A per-session **monotonic frontier** (advances only on hear/skip, never on re-read or new content) distinct from the **browse cursor** (`nav_cursor`/`nav_turn`). Nav moves only the browse cursor; keep-going/forward-read advance the frontier. Revive `history.unheard` as the forward-from-frontier read (un-bound it from the current-turn limit where catch-up needs it).
- **Tool fidelity (§9):** record tool uses to the transcript in **every** verbosity (today `on_tool` records none — `prose.py:39-50`); render per verbosity (everything = full; **medium** = a short summary templated from the tool's structured input, LLM/skip fallback for opaque bash; quiet = none spoken). Verbosity stays **global** (ratified).
**Depends on:** SP1 (keep-going reads the frontier — coordinate with SP2). **Risk:** moderate (data-model heavy).

### SP5 — Catch-up + the sweet-spot  *(§10.1)*
**Closes:** §10.1 (auto-flow vs navigable), the catch-up ADD.
**Scope:**
- A **new** catch-up action + MsgType + handler + a **co-designed chord** (legacy `catch_up` retired — genuine ADD). Reads forward from the focused session's frontier through its pile to live.
- **Sweet-spot gating:** stopped/quiet sessions are **navigable** (not auto-drained — keep-going skips them); active sessions keep-go. "Left" = stopped/quiet, not merely switched focus.
**Depends on:** SP3 (stop/quiet) + SP4 (frontier). **Risk:** low-moderate.

### SP6 — Restart persistence + cue  *(R11)*
**Closes:** R11.
**Scope:**
- Serialize the durable state (sessions + identities + per-session transcript + frontier + folder labels) to `~/.sonari/` via `atomicio`, snapshotted at safe boundaries (off the speak-loop hot path), reloaded on `run()`. A **"Sonari restarted" cue**; the interrupted readout does **not** auto-resume (restore the frontier, don't re-enqueue).
**Depends on:** SP4 (frontier/transcript) + SP1 (identities/workspace). **Risk:** moderate (disk I/O; atomicity; off-hot-path).

---

## Sequencing

`SP1 → SP2 → SP3 → SP4 → SP5 → SP6` (linear is simplest; SP4 may begin once SP1 lands and run alongside SP2/SP3, but coordinate the frontier with SP2's keep-going). The CONC-1 headline fix ships at **SP2** (second), behind only the foundational split.

## Predecessor work to fold (per the handoff)

- **Phase-0 diagnosability — KEEP / re-apply** into the rebuild (DIAG-1 `PYTHONUNBUFFERED`, DIAG-2 `faulthandler` SIGUSR1, DIAG-3 richer STATUS heartbeat, the FOCUS-1 jump diagnostic). It pinned every root cause live. Cherry-pick from `fix/cockpit-phase0-diagnosability` (`056be83`) — most naturally in SP1.
- **Phase-1 (4 fixes) — fold the INTENT, not the patches** (`f3b9257` B5-2, `a6a1671` CONC-2, `113fd3a` NEW-PIN-1, `aeb4ca9` WEDGE-NEW-3): carry their intent into the new spec-driven tests; do not merge the patches (they patch the model we're replacing).

## Per-sub-project protocol (each one)

1. `superpowers:writing-plans` → a detailed bite-sized-TDD plan in `docs/superpowers/plans/2026-06-29-sonari-va-spN-<name>.md`, spec + reconciliation as oracle.
2. `superpowers:subagent-driven-development` → fresh subagent per task (TDD), per-task review, then an Opus whole-branch review; fix wave.
3. Concurrency guards green at every commit; full suite green before merge (baseline 836/1skip + new tests).
4. Sacrificial-HOME dogfood where it touches `~/.sonari`; **Nima** does the live `./bin/sonari install` + the listening pass.
5. Merge (Nima's gate) → next sub-project off the merged base.
