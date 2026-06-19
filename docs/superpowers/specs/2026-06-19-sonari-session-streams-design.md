# Sonari — Per-Session Streams + Free Session Navigation

- **Date:** 2026-06-19
- **Status:** Design — awaiting review
- **Scope owner:** Nima
- **Supersedes (partial):** the single-stream voice-arbitration model in `daemon.py`
  (the `_voice_owner` / `_captured_msg` / `_open_msg` / sticky-capture guards)

## 1. Problem

The daemon handles multiple concurrent Claude Code sessions on top of a
**single-stream** model: one shared speech queue (`SpeechQueue`), one voice owner
(`_voice_owner`), and a lattice of guards bolted on over time (named H1 / M2 / M4 /
L2 / M6 in the code) to keep two sessions from colliding. There is no first-class
notion of "a session is an independent stream." The symptoms below are edge gaps in
that lattice, not missing individual guards — which is why patching one tends to
surface another.

A second, related limitation: **session history is wiped on every prompt.**
`history.reset(session)` runs on `FLUSH` (`daemon.py:443`), and `FLUSH` is emitted by
the hook on every user prompt (`hooks_entry.py:97`). So navigation
(`nav_next/prev/first/last`, `repeat`) only ever covers the **current turn**; there is
no way to jump back to a response from an earlier prompt. History is also pure
in-memory (no I/O in `history.py`), so it is lost on a daemon restart too.

### 1.1 Reproduced evidence (harness spikes, deterministic)

All three reproduced with the existing test harness (`tests/daemon_helpers.py`,
`FakeSpeaker` + direct `handle_message`). Each was **root-caused to a layer**, because
that determines whether the per-stream redesign fixes it or a separate fix is needed.

| # | Symptom (as Nima hit it) | Reproduced behavior | Root layer | Fixed by redesign? |
|---|---|---|---|---|
| 1 | Voice stolen / sessions interfere | Switch to B while A streams → A finishes, voice frees, B is foreground & streaming, **B stays silent for the rest of its message**, no signal | voice-ownership / capture | **Yes — dissolves** |
| 2a | Control hit the wrong session | `STOP` while focus=B (empty) **cleared A's whole queue**; `PAUSE` routed "Paused." to B while the voice was reading A | voice-ownership / queue scope (`foreground()` ≠ `voice_owner`) | **Yes — dissolves** |
| 2b | Interrupt "didn't cut cleanly" / lag / resumed wrong | Not reproducible in FakeSpeaker (instant); lives in the `Speaker` cancel-epoch / synth-gap mechanism | **Speaker / cancel-epoch** | **No — separate verify (Stage 7)** |
| 3a | Output dropped / misordered across sessions | Background session's prose captured (silently dropped from playback) | voice-ownership / capture | **Yes — dissolves** |
| 3b | Output duplicated | `REPEAT` re-enqueued the last message while it was still queued → `["one","two"]` became `["one","two","one","two"]` | **replay (history+queue)** | **No — separate fix (Stage 6)** |

The honest summary: the redesign **dissolves symptom 1 and the cross-session flavors
of 2 and 3**; the **REPEAT/catch_up replay duplication (3b)** and the **Speaker cancel
timing (2b)** are independent and get their own stages. The redesign is justified on
its own merits as the multi-session architecture + navigation Nima requested — not by
an over-claim that it fixes everything.

## 2. Goals / Non-goals

**Goals**
- A first-class, independent **stream per session**: its own queue, assembler,
  prose-buffer, nav state, mute, and "waiting" flag.
- One clear arbitration rule: **the voice follows the foreground session.**
- Nothing a background session produces is silently lost.
- **Persistent, navigable session transcript**: jump freely to previous responses,
  two-level (within-response + response-to-response).
- Retire the `_voice_owner` / `_captured_msg` / `_open_msg` / sticky-capture lattice and
  the FLUSH-vs-SESSION_END cleanup divergence.
- Fix the replay duplication (3b) and verify the cancel timing (2b).

**Non-goals (deliberate, may revisit)**
- Durable on-disk transcript surviving a daemon restart. Stays in-memory + capped.
- Speaking two sessions simultaneously (physically unintelligible eyes-free).
- A full threading/locking rewrite. The concurrency model (one speak thread, one lock,
  per-connection handlers) is preserved.

## 3. The model — one rule

**The voice plays the foreground session's stream; every session is an independent
stream. Background streams accumulate; switching foreground switches what is voiced.**

This single rule replaces the entire arbitration lattice. Because the voice is *defined
as* the foreground stream, `foreground()` and "what you hear" can no longer diverge —
which is the root of symptoms 1 and 2a.

## 4. Architecture

### 4.1 `SessionStream` (new unit, one per session)
Owns the complete per-session state currently fragmented across ~10 dicts/sets in
`SpeechDaemon.__init__` (`daemon.py:43–72`):

- `queue: SpeechQueue` — **its own** pending-speech deque (capped; see §6)
- `assembler: ProseAssembler`
- `prose_buffer: list` — minqueue batching
- `nav_cursor`, `options` (last decision text), `muted: bool`
- `warned_immediate: bool`, `guided: bool`
- `has_waiting` — derived: queue non-empty / unheard backlog (drives the waiting earcon)

History stays in `SessionHistory` (already per-session, `history.py`) but is reached via
the stream and **persists across turns** (see §5).

### 4.2 Foreground scheduler
The speak loop drains **`streams[foreground].queue`** instead of one global queue.
`foreground` comes from `SessionManager` (unchanged: last session to prompt/start, or
the pinned one). If the foreground stream's queue is empty, the loop idles — it does
**not** pull from background streams. Switching foreground (a new prompt, or the
switch-&-read hotkey) repoints the loop and cuts the current sentence.

### 4.3 Retired by construction
`_voice_owner`, `_may_speak`, `_claim_for_decision`, `_owner_mid_reply`,
`_captured_msg`, `_open_msg`, sticky-capture. Background output is never "captured" — it
enqueues into its own stream. Session cleanup becomes a single `streams.pop(session)`,
which **eliminates the FLUSH-vs-SESSION_END divergence** (the confirmed `_assemblers` /
`_nav_cursor` leak) for free.

## 5. Persistent transcript + two-level navigation

- **History is no longer wiped on `FLUSH`.** A new prompt resets *live playback* (queue,
  assembler, pause, and snaps the nav cursor to the fresh response) but **keeps the
  transcript**. `SESSION_END` still clears it.
- **Turn grouping.** Each user prompt opens a new turn. Within a turn, message groups
  (`msg_id`) work as today. Navigation is **two-level**:
  - *within-response* — existing `nav_next/prev/first/last` over the anchored response.
  - *response-to-response* — new `nav_prev_response` / `nav_next_response` hotkeys jump a
    whole turn at a time.
- **Replaying a past response only reads stored text** — it never re-triggers the agent.
- **Capped, in-memory.** Oldest turns drop past the cap (mirrors history's current
  rolling `deque(maxlen=cap)`); `nav first` lands on the oldest *retained* response.

## 6. Behavior / policy decisions

| Decision | Choice | Who |
|---|---|---|
| Scope of the work | Per-session-stream redesign (not targeted patching) | Nima |
| Background prose | Accumulates in its own stream; **one soft, debounced "waiting" earcon** (on empty→waiting and on a new turn, not per sentence) | recommendation |
| Background **block** (permission/choice/plan) | **Distinct alert earcon, voice NOT hijacked**; a switch-&-read hotkey reads its options when the user chooses | Nima |
| Switching | foreground = last prompt (unchanged) **+** switch-&-read hotkey (repurpose `catch_up`) to jump to a waiting stream | recommendation |
| Controls (STOP/PAUSE/SKIP/NAV/REPEAT/MUTE) | Act on the **foreground stream** (fixes global-STOP clobber 2a) | recommendation |
| Backlog bound | Per-stream queue capped like history; switching reads what remains, oldest-first | recommendation, adjustable |
| Navigation granularity | **Two-level** (response + within) | Nima |
| Durable on-disk transcript | Out of scope for now | recommendation |

## 7. Known seam to resolve during implementation

Persisting history across turns interacts with everything built on the per-session
deque: `last_message`, `nth_last_message`, `message_ids`, `unheard`,
`other_session_with_unheard`, and the heard/unheard voice-continuity capture. Two
points to settle in Stage 4/5 with tests:
- **`unheard` semantics** once history spans the whole session — it must stay bounded
  and not let `catch_up` replay the entire transcript. In the new model, live/backlog
  playback is driven by the per-stream **queue**, and `heard` is marked when a queued
  item is spoken; `unheard` should reflect *recent* backlog, not all history.
- **`message_ids` must group by turn** so the two-level nav can address "response N"
  vs "item within response N."
This is internal consistency, resolved during implementation — not a blocker on the
design.

## 8. Staging (incremental, shippable, test-first; full suite green at each step)

1. **Extract `SessionStream` container.** Move the per-session dicts into it.
   Pure refactor, behavior-preserving; characterization tests guard it.
2. **Per-stream queues + foreground-driven speak loop.** The multi-session policy flip:
   background accumulates instead of being captured. *Dissolves symptom 1 + 3a.*
3. **Multi-session UX + per-stream controls.** Waiting earcon, switch-&-read hotkey,
   scope STOP/PAUSE/etc. to the foreground stream; delete the retired guards.
   *Dissolves symptom 2a.*
4. **Persistent transcript.** Stop reset-on-FLUSH; add turn grouping; snap cursor to
   live edge on a new prompt; keep `SESSION_END` clearing. Resolve the §7 seam.
5. **Two-level navigation.** `nav_prev_response` / `nav_next_response` + within-response
   nav over persisted turns; reconcile `repeat` / `catch_up`.
6. **Replay duplication fix (symptom 3b).** `REPEAT` / `catch_up` must not re-enqueue an
   entry already pending in the stream's queue. Dedicated failing-then-passing test from
   the spike.
7. **Speaker cancel verification (symptom 2b).** Audit and harden the cancel-epoch /
   synth-gap path with the real `Speaker` + a slow fake `say_runner`; fix only a
   demonstrated defect, otherwise document it as solid.
8. **Backlog bounds, caps, cleanup, dead-code removal.**

Each stage is its own PR. Stages 1 and 8 are pure-internal; 2–7 change behavior and
each carries its own tests, including the spike scenarios promoted to regressions.

## 9. Testing

- **Characterization first** on Stage 1 to lock preserved behavior.
- **Spikes become regressions:** the B-goes-silent, STOP-clobber, PAUSE-misroute, and
  REPEAT-dup scenarios become permanent failing-then-passing tests.
- **New policy tests:** background accumulates and is never dropped; switch-&-read;
  alert-no-hijack; cross-turn navigation; cursor-snaps-to-live on new prompt.
- Tests that encode the **old capture policy** are updated (not deleted) with the why
  documented — they assert behavior we are deliberately changing.
- The full suite (currently **693 passed, 2 skipped**; the 2 need the `[kokoro]`/numpy
  extra) stays green at every stage.

## 10. Risks & constraints

- **Runtime performance** (Nima's one hard constraint): unaffected — per-session dict
  lookups, still one speak thread and one lock, memory bounded by per-stream caps.
- **Shipped daily-use daemon:** behavior is preserved until the Stage-2 flip; each stage
  ships independently; spikes guard against regressions.
- **Largest risk** is the Stage-4 history-lifecycle change (touches repeat/catch_up/nav);
  mitigated by the §7 analysis + tests and by sequencing it after the multi-session core
  is stable.

## 11. Open / deferred
- Backlog cap value (start ≈ history cap; tune by feel).
- Waiting-earcon sound design (must be subtle, distinct from the decision alert).
- Durable on-disk transcript (non-goal now; revisit if restarts lose useful history).
