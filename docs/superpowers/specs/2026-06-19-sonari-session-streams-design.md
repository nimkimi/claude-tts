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
| 2b | Interrupt "didn't cut cleanly" / lag / resumed wrong | Not reproducible in FakeSpeaker (instant); lives in the `Speaker` cancel-epoch / synth-gap mechanism | **Speaker / cancel-epoch** | **No — separate verify (Stage 6)** |
| 3a | Output dropped / misordered across sessions | Background session's prose captured (silently dropped from playback) | voice-ownership / capture | **Yes — dissolves** |
| 3b | Output duplicated | `REPEAT` re-enqueued the last message while it was still queued → `["one","two"]` became `["one","two","one","two"]` | **replay (history+queue)** | **Yes — dissolves: `REPEAT`/`catch_up` are retired entirely (§6, Stage 3); with no replay command there is nothing to re-enqueue** |

The honest summary: the redesign **dissolves symptom 1 and the cross-session flavors
of 2 and 3**, and **3b dissolves once `REPEAT`/`catch_up` are retired** (Stage 3 —
single-queue-era replay commands the per-stream model makes redundant: see §6). The only
independent item left is the **Speaker cancel timing (2b)**, which gets its own verify
stage. The redesign is justified on its own merits as the multi-session architecture +
navigation Nima requested — not by an over-claim that it fixes everything.

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
- Retire the single-queue-era replay commands `catch_up` / `REPEAT` (the per-stream
  model subsumes them — see §6), which makes the 3b duplication impossible (nothing to
  re-enqueue), and verify the cancel timing (2b).

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
**jump-to-waiting-session hotkey**) repoints the loop and cuts the current sentence.

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
| Background **block** (permission/choice/plan) | **Distinct alert earcon, voice NOT hijacked**; the jump-to-waiting-session hotkey switches to that stream and plays its queue (incl. the options) when the user chooses | Nima |
| Switching | foreground = last prompt (unchanged) **+** a dedicated **jump-to-waiting-session hotkey** (NEW binding — not a `catch_up` repurpose) that switches foreground to a stream with backlog and plays it | Nima |
| Controls (STOP/PAUSE/SKIP/NAV/MUTE) | Act on the **foreground stream** (fixes global-STOP clobber 2a) | recommendation |
| `catch_up` / `REPEAT` | **Retired entirely** (handlers + CLI removed) — single-queue-era replay workarounds the per-stream model subsumes: backlog accumulates + plays on switch, pause/resume continues, nav-from-start (`Ctrl+Cmd+↑`) re-reads. The default keymap already drops them as hotkeys. | Nima |
| Hotkey surface (current default, `Ctrl+Cmd`+key) | `nav_prev/next`=←/→, `nav_first`=↑ (read response from start), `nav_last`=↓, `pause`=s (play / resume-from-stopped), `mute`=m, `pin_toggle`=p; Stage 3 adds the jump-to-waiting key | reference |
| Backlog bound | Per-stream queue capped like history; switching reads what remains, oldest-first | recommendation, adjustable |
| Navigation granularity | **Two-level** (response + within) | Nima |
| Durable on-disk transcript | Out of scope for now | recommendation |

## 7. Known seam to resolve during implementation

Persisting history across turns interacts with everything built on the per-session
deque: `last_message`, `nth_last_message`, `message_ids`, `unheard`,
`other_session_with_unheard`, and the heard/unheard voice-continuity capture. Two
points to settle in Stage 4/5 with tests:
- **`unheard` semantics** once history spans the whole session. With `catch_up` retired
  (§6) `unheard` no longer drives replay; it now only feeds the **waiting earcon** and
  heard-marking. Live/backlog playback is driven entirely by the per-stream **queue**,
  and `heard` is marked when a queued item is spoken — so `unheard` must stay bounded to
  *recent* backlog, never the whole transcript.
- **`message_ids` must group by turn** so the two-level nav can address "response N"
  vs "item within response N."
This is internal consistency, resolved during implementation — not a blocker on the
design.

## 8. Staging (incremental, shippable, test-first; full suite green at each step)

1. **Extract `SessionStream` container.** Move the per-session dicts into it.
   Pure refactor, behavior-preserving; characterization tests guard it.
2. **Per-stream queues + foreground-driven speak loop.** The multi-session policy flip:
   background accumulates instead of being captured. Also **deletes the retired
   guards** (`_voice_owner` / `_may_speak` / `_claim_for_decision` / `_owner_open` /
   `_owner_mid_reply` and the `captured` / `open_msg` fields) — they are dead by
   construction once selection is foreground-driven, and leaving inert arbitration in
   the speak-loop file is a correctness hazard. (Moved up from Stage 3: a PR-boundary
   call, no runtime-behavior delta — the flip is the behavior change either way.)
   *Dissolves symptom 1 + 3a.*
3. **Multi-session UX + per-stream controls.** Waiting earcon; a dedicated
   **jump-to-waiting-session hotkey** (new binding); scope STOP/PAUSE/etc. to the
   foreground stream; the **cut-on-switch refinement** (§4.2 — Stage 2 lets the current
   sentence finish on a switch, this stage cuts it); and **retire `catch_up` + `REPEAT`
   entirely** (handlers + CLI). Retiring `catch_up` *here* — together with the jump
   hotkey, the first non-FLUSH switch — is what resolves the §11 tripwire; removing
   `REPEAT` dissolves symptom 3b. *Dissolves symptoms 2a + 3b.*
4. **Persistent transcript.** Stop reset-on-FLUSH; add turn grouping; snap cursor to
   live edge on a new prompt; keep `SESSION_END` clearing. Resolve the §7 seam.
5. **Two-level navigation.** `nav_prev_response` / `nav_next_response` + within-response
   nav over persisted turns. (No `repeat`/`catch_up` reconciliation — both retired in
   Stage 3.)
6. **Speaker cancel verification (symptom 2b).** Audit and harden the cancel-epoch /
   synth-gap path with the real `Speaker` + a slow fake `say_runner`; fix only a
   demonstrated defect, otherwise document it as solid.
7. **Backlog bounds, caps, cleanup, dead-code removal.**

(The former Stage 6 "replay duplication fix" is **removed** — retiring `REPEAT`/`catch_up`
in Stage 3 makes symptom 3b impossible by construction.)

Each stage is its own PR. Stages 1 and 7 are pure-internal; 2–6 change behavior and
each carries its own tests, including the spike scenarios promoted to regressions.

## 9. Testing

- **Characterization first** on Stage 1 to lock preserved behavior.
- **Spikes become regressions:** the B-goes-silent, STOP-clobber, and PAUSE-misroute
  scenarios become permanent failing-then-passing tests. (The REPEAT-dup spike is moot —
  `REPEAT` is retired in Stage 3.)
- **New policy tests:** background accumulates and is never dropped; jump-to-waiting-
  session; alert-no-hijack; `catch_up`/`REPEAT` fully removed (no handler, no CLI);
  cross-turn navigation; cursor-snaps-to-live on new prompt.
- Tests that encode the **old capture policy** are updated (not deleted) with the why
  documented — they assert behavior we are deliberately changing.
- The full suite (after Stage 1: **698 passed, 2 skipped**; the 2 need the
  `[kokoro]`/numpy extra) stays green at every stage.

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
- **✅ catch_up cross-session double-speak — RESOLVED BY DESIGN (was a Stage 2 final-review tripwire).**
  Stage 2 left `catch_up` routing a cross-session replay into the foreground stream
  without flushing the *other* session's own queued copy — a latent double-speak that
  would surface on a later *non-FLUSH* switch to that session. It is unreachable in
  Stage 2 (every foreground switch today is a `FLUSH`/UserPromptSubmit or a brand-new
  `SessionStart` id, both of which clear the stale state). **Stage 3 retires `catch_up`
  entirely, in the same stage as the jump-to-waiting-session hotkey** (the first
  non-FLUSH switch) — so the hazard never becomes reachable. The binding constraint
  (retire `catch_up` no later than the jump hotkey) is satisfied by construction.
