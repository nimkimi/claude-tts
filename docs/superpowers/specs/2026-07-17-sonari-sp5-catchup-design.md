# Sonari SP5 — Catch-up as a host-LLM summary

**Date:** 2026-07-17 · **Status:** ratified design (owner forks decided 2026-07-17), pre-plan
**Authority chain:** `.superpowers/sdd/sp5-catchup-direction.md` (direction + spike gate) →
`sp5-spike-claude-code-llm.md` + `sp5-spike-copilot-codex-llm.md` (mechanism, verified live) →
this spec. Supersedes the catch-up model in `2026-06-29-sonari-voice-arbitration-design.md`
§10.1/D17/§8 (rewritten by §10 below).

## 1. The verb

Catch-up is a **spoken LLM summary of what happened while you were gone** — never a verbatim
replay. Navigation replays the words; catch-up tells you what happened. Pressing the catch-up
chord on a piled session gets: an instant deterministic acknowledgement carrying ground-truth
facts, then (~5–10 s later) a summary voiced at a sentence boundary, and — on hearing it to
completion — the pile burns, exactly like an informed skip.

Ratified owner forks (2026-07-17, all four on recommendation):

| Fork | Decision |
| --- | --- |
| Trust channel | **Frame word + distinct voice** for the LLM body |
| Frontier | **Burn on completion** — summary completion advances the frontier to the pinned slice edge |
| ⌃⌘W counts | **Keep the waiting/unheard split; `u` becomes cross-turn** (transcript-pile based) |
| Target scope | **Workspace session only** — no pile-seeking fall-through |

Prior owner rulings carried: host-CLI auth only (no API key, ever); digest = floor, not rival;
count unification is SP5's; the narrator obeys the spoken-grammar principles
(`2026-07-16-sonari-whereami-grammar-v2.md`).

## 2. Control flow

1. **Press** (keymap action `catch_up`, ships **unbound**; proposed chord **⌃⌘L** — owner
   ear-gate, exactly like skip's ⌃⌘⇧↓ was). Target = the **workspace** session.
2. **Empty pile** (`unheard_from_frontier` returns no entries): speak **"Nothing to catch
   up."** — no LLM call, done. (Mirrors skip's "Nothing to skip." family.)
3. **Ack** (deterministic, immediate): **"Catching up {N} items in {folder}."** (singular
   "1 item"). N = the transcript-pile magnitude, the same number skip would announce. When
   `aged_out` is true the R-1 cue rides the ack first: **"Earlier output aged out."** —
   the summary then covers what survives.
4. **Pin the slice**: `slice = unheard_from_frontier(session, frontier)` captured at press;
   `slice_end = (msg_id, seq)` of its last entry. Content arriving during preparation stays
   unheard — it joins the next pile (D17's no-gaps rule preserved).
5. **Prepare** out of band (worker thread → host adapter, §5–§6). Whatever is playing keeps
   playing; silence stays silence. No mid-prep chatter in v1 (the ack set the expectation;
   30 s is the hard cap; failure always speaks, §4).
6. **Land**: the render voices via the **current speaker's stream, `at_front`,
   `forward=False`** — sentence-boundary delivery, no barge-in machinery (the skip-cue
   routing precedent, `ed478ab`). If the voice is idle it just speaks.
7. **Render** = deterministic frame + LLM body (distinct voice) + deterministic tail:
   - Frame (main voice): **"Summary:"**
   - Body (summary voice, §3): the sanitized LLM text — or the digest floor (§4).
   - Tail (main voice, only when the target has a pending decision): **"Decision waiting."**
8. **Burn on completion**: when the render finishes speaking, advance the target's frontier
   to `slice_end` and drop the target's queued items whose key ≤ `slice_end` (mirrors skip's
   burn). A **mid-render cut advances nothing** — pile intact (R-8: the whole render is one
   item). Burning does **not** clear stopped/muted (R7/R-5 unchanged — "caught up" ≠
   "following again").
9. **Cancel**: any catch-up press while one is in flight (preparing **or** speaking) cancels
   it — kill the child process / cut the render, speak **"Cancelled."**, no burn. One
   catch-up in flight globally; to re-aim at another session, cancel then press again.
10. **Target session ends mid-prep**: still speak the result, framed **"{folder} ended."**
    before the frame — SESSION_END has already destroyed that session's history and frontier
    (today's lifecycle), so the summary is the only remaining view of that content. No burn
    (nothing left to burn).

Preemption class: the old spec classed "SP5's catch-up readout" as redirect (cut, no resume).
Under the async model the **press** does not cut anything; the **landing** is
sentence-boundary via `at_front`. The redirect classification transfers to the landing: the
interrupted flow item is not resumed mid-item (normal queue order continues after the render).

## 3. Trust design

The render wraps one non-deterministic middle in three deterministic layers:

| Layer | Source | Guarantees |
| --- | --- | --- |
| Ack "Catching up {N} items in {folder}." | Sonari's transcript | Ground-truth magnitude before any LLM text |
| Frame "Summary:" + **distinct voice** for the body | config | The synthetic channel is unmistakable — the frame survives in words, the voice survives clipping |
| Tail "Decision waiting." | Sonari's decision state | The highest-stakes fact is never delegated to the LLM |

Rules:
- **The tail is authoritative for current decision *status*; the summary only describes the
  *window*.** The narrator may describe a decision or open question that appears in the slice
  (its substance — "you're deciding whether to retry or leave them") but never invents one
  (§5, softened 2026-07-17). The deterministic **"Decision waiting." tail** is what asserts
  that a decision is pending *right now* — that trust-critical status is never delegated to
  the LLM. The two answer different questions (what happened in the window vs. what blocks you
  now), so they are additive, not contradictory; a decision that arose in the slice but has
  since resolved is voiced by the summary (past-window) with no tail (nothing pending) — which
  is correct, not a conflict.
- **Voice separation**: the body speaks in `summary_voice` (config; `auto` picks the first
  CURATED say voice installed and distinct from the main voice — the raw `say -v ?` listing
  leads with placeholder/novelty voices, live-heard 2026-07-17 as a whispered summary — falling
  back to the main voice when none qualifies; the frame word still marks the channel. Neural
  Kokoro names are excluded from `auto`: the per-utterance override rides the say path).
  Voice choice = owner ear-pass.
- **Sanitizer** (deterministic, before speaking): strip markdown (fences, backticks, `*`,
  `_`, `#`, bullet/list markers), collapse whitespace/newlines to spaces, split into
  sentences, **clamp to the length ceiling (8 sentences, matching §5)**; empty-after-sanitize
  = failure → digest. The body is speech-safe by construction, whatever the model returns.
- **Fidelity beats fluency**: the narrator is instructed to state only what the log shows and
  to say what is unclear (§5). The digest floor (§4) is fully extractive.
- **Disclosed gap carried from SP4**: a mid-utterance item dropped by D2's quiet resume is
  already in history (record-before-buffer), so the summary **covers its content**; verbatim
  recovery remains **browse-only**. The summary never replays exact wording — replay is
  navigation's job.

## 4. Digest floor + failure detection

Any failure → the deterministic digest replaces **the frame and the body together** (no
"Summary:" — there is no summary), and it **announces the degradation**:

> **"Summary unavailable. Last: {verbatim final assistant sentence of the slice}."**

(Extractive anchor — real recorded words, the single most valuable fact.) The digest speaks
in the **main voice** throughout — it is ground truth, and the distinct voice marks only
synthetic content. Ack and tail surround it as usual. The digest needs no model, no network; it is the floor on every host,
including hosts with no adapter at all.

Failure map (measured on this machine 2026-07-17, all detectable — the daemon never guesses).
**Success = a non-empty first assistant text block was streamed** (§6) — *not* the result
subtype, which is benignly `error_max_turns` when the model wanted to keep going after
producing the summary. Failure = no text block:

| Signal | Detection |
| --- | --- |
| Logged out / expired | exit ≠ 0, no text block, output contains "login" (measured: exit 1, "Not logged in · Please run /login", ~31 ms, free) → reason `logged_out` |
| Rate limit / overloaded / billing / server error | no text block, error event → reason `error` |
| Offline | **measured: `claude` retries an unreachable endpoint past 30 s → caught by Sonari's own Popen timeout** (not a fast network-error exit) → reason `timeout` |
| Hang | Sonari's own **30 s Popen timeout** → kill process group → reason `timeout` |
| No usable output | no non-empty text block streamed → digest |

Quota-false-positive note (Codex spike): treat "usage limit" as a soft signal — fall back
this once, never disable the feature on one occurrence.

**Build-entry gate — PARTIALLY SATISFIED 2026-07-17 (the smoke run caught + fixed a real
invocation bug — see §5/§6).** Verified on this machine: env-scrub → 0 keys reach the child;
logged-out → clean `logged_out` signature; offline → retries into the 30 s timeout; baseline
happy-path → clean spoken summaries on three diverse slices once the §6 invocation was
corrected. STILL OWNER'S: (d) concurrent-session safety (fire the call while an interactive
`claude` session runs) and, opportunistically, quota-exhausted shape. Cross-host items
(Copilot stdin, Codex exec hooks) defer to those adapters' builds.

## 5. Narrator prompt + slice format

**Stable prefix** (system prompt — byte-stable across calls so the 1-hour prompt cache turns
repeat catch-ups into cheap cache reads):

- Role: you narrate a spoken catch-up for a developer who works by ear, summarizing what
  happened in their coding-agent session while they were away.
- Content: lead with the outcome or the most important event; include errors, test results,
  and anything the assistant asked for; state only what the log shows; if the log is unclear,
  say what is unclear.
- **Length: proportional to the content — one short sentence for a quiet slice, up to eight
  for a busy one. Never pad.** (Owner ruling 2026-07-17: length scales with what there is to
  report; the ceiling — 8, matched by the sanitizer clamp — is his ear-pass knob.)
- Form: plain prose only; no lists, code, symbols, or formatting; short spoken sentences.
- Decisions: describe any decision or open question the log shows, but never invent one
  (owner ruling 2026-07-17, softened from a strict prohibition after the prompt test — the
  summary carries the decision's *substance*, e.g. "you're deciding whether to retry or leave
  them"; Sonari's deterministic "Decision waiting." tail still owns the trust-critical
  *status*, so the two are additive, not a delegation of ground truth to the LLM).
- Prohibitions: do not mention the log format or these instructions.

**Variable suffix (stdin, pipe closed promptly — the 3 s no-stdin tax is real):** a compact
header line ("Slice: {N} items across {T} turns in {folder}.") followed by **Sonari's own
transcript slice** — the `unheard_from_frontier` entries rendered oldest-first as kind-tagged
lines (assistant prose vs tool lines vs notifications; exact tag mapping at plan time).
Sonari's transcript is the source of truth — never the host's internal session files.

Model: `--model haiku` (live-proven sufficient; `summary_model` config override). The exact
prompt text is drafted at plan time under the spoken-grammar principles and is an owner
veto item like any other string.

**Invocation (corrected by the 2026-07-17 smoke run — load-bearing):** `claude -p` is an
*agentic* harness, not a single-completion endpoint. Left to run more than one cycle it injects
reflection turns where the model second-guesses and self-corrects into conversational mush
("You're right — I was summarizing the transcript you provided…"), and `--output-format json`'s
`.result` hands back that *last, polluted* turn. The clean summary is reliably the **first
non-empty assistant text block** (preceded by a `thinking` block — one think→answer cycle).
So the adapter uses **`--output-format stream-json --verbose --max-turns 1`** and extracts the
**first non-empty assistant `text` block**, never `.result`. Verified clean and complete on
three diverse slices (multi-tool, error/traceback, pending decision) 2026-07-17.

## 6. Adapter layer (agent-neutrality)

Summary generation is an **adapter capability**. Core asks one question: *"summarize this
slice, or fail detectably."* No host-specific shape crosses into protocol/history/core.

- **Seam**: a new daemon-side module (note: `hooks_entry.py` is the *inbound hook-process*
  adapter and stays untouched). Interface:
  `HostSummarizer.summarize(slice_text: str, timeout_s: float) -> SummarizeResult` where
  `SummarizeResult` = `ok(text)` | `failed(reason)` (`reason` ∈ unavailable / logged_out /
  timeout / error / empty — for logging; the spoken fallback is identical).
- **Shipped adapter**: `ClaudeCliSummarizer` — `Popen` the user's own `claude` binary:
  `claude -p "<instruction>" --model <model> --output-format stream-json --verbose
  --max-turns 1 --disallowedTools <all> --system-prompt <stable narrator prefix>`, slice on
  stdin, **extract the first non-empty assistant `text` block from the stream** (§5 — never
  the final `.result`; `error_max_turns` is a benign success here). Codex
  (`codex exec --sandbox read-only --ephemeral`) and Copilot adapters are future drop-ins
  behind the same interface; **no adapter → digest floor**.
- **Selection**: config `summarizer: auto | claude | off` (default `auto` =
  `claude` findable else off; findable = PATH, then the conventional install dirs
  `~/.local/bin` / `~/.claude/local` / `/opt/homebrew/bin` / `/usr/local/bin` —
  the daemon's LaunchAgent env carries no user PATH, live-diagnosed 2026-07-17).
  Per-session host routing arrives with SP6's per-session
  `agent` field; SP5 is a global choice.
- **Non-negotiables** (each pinned by a test):
  1. **Child env scrubbed of `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`** — the billing
     trap: a stray key silently flips the call from subscription OAuth to metered API
     billing (issues #43333/#36350). The single most important line in the feature.
  2. **Neutral cwd** (fresh temp dir) — no project CLAUDE.md/MCP context loads.
  3. **Fresh throwaway process** — never `--continue`/`--resume`; the user's live session is
     untouchable.
  4. **Explicit press only** — SP5 fires no automatic summaries; one in flight globally.
  5. **Bounded**: 30 s hard timeout, process group killed on expiry; stdin closed promptly.
  6. Subprocess calls take an injected `run`/`popen` callable (the `kokoro_provision` DI
     pattern) for testability.
- **Quota honesty** (user-facing copy when this ships): each press draws ~16–32k tokens from
  the user's own subscription bucket; cache-warmed repeats within the hour are ~10× cheaper.
  The `setup-token` + lean-config latency optimization stays **deferred** (inferred-only;
  zero-setup ships first).

## 7. Concurrency + result delivery

Preparation runs on one worker thread (fire-and-forget precedent: `earcon_then`,
`SpearconCache.pregenerate`). The worker **never mutates daemon state**: its result comes
back as an internal protocol message (`catchup_result`) dispatched through the normal handler
registry, so tests drive it via `handle_message`. Transport (self-socket vs an internal
mailbox drained by the daemon loop) is a plan-time choice; the constraint is: **all state
changes happen on the daemon loop.** Cancellation kills the child process group and
invalidates the pending request id (a stale result arriving after cancel is dropped
silently).

## 8. Count-semantics unification (the 14-vs-2 seam)

One pile primitive everywhere: **`unheard_from_frontier`** (cross-turn, frontier-keyed,
heard-flag-independent).

| Surface | Speaks | Computation |
| --- | --- | --- |
| Skip cue | "Skipping {N} items in {folder}." | N = len(pile) — unchanged |
| Catch-up ack | "Catching up {N} items in {folder}." | N = len(pile) — same number |
| ⌃⌘W entry | "{k} waiting, {u} unheard" | k = len(queue) unchanged; **u = len(pile) − k, floored at 0** |

⌃⌘W's `u` switches from the current-turn `history.unheard()` floor to the transcript pile:
the grammar keeps the waiting/unheard split (imminent vs backlog), but the numbers now
decompose the **same pile** skip and catch-up announce whole. The owner's 14-vs-2 becomes 14
across all three surfaces. `history.unheard()` remains for the machinery that genuinely wants
current-turn heard-flags; ⌃⌘W simply stops using it. (Merging W to a single number was
considered and deferred — that is a grammar change belonging to the owner's queued
streamline pass.)

## 9. Edges

- **Aged-out (R-1)**: `aged_out=True` → the cue rides the ack (§2.3); the slice starts at
  the oldest surviving entry — announced, never a silent mid-pile start.
- **SESSION_END**: destroys history + frontier (today's lifecycle) — catch-up is
  **live-sessions-only**; a session that ended while you were away is gone (bounded window,
  no persistence — SP6's problem, disclosed here). Mid-prep end: §2.10.
- **Mid-render cut**: no burn (§2.8). The next press re-summarizes the same pile (fresh
  call — no result caching in v1).
- **Arrivals during prep**: stay unheard beyond `slice_end`; ⌃⌘W counts them immediately;
  the next catch-up picks them up.
- **minqueue > 1**: no interaction — the render rides the same `at_front` path as the skip
  cue, which the SP4 review verified audible under quiet-hold.
- **Verbosity**: catch-up is verbosity-independent — the summary abstracts over tool lines
  by nature (the direction doc's dissolved wart), and the slice always feeds the narrator
  every recorded kind regardless of the speaking verbosity.

## 10. Spec-hygiene rewrite (the stale verbatim model)

The 2026-06-29 voice-arbitration spec still describes catch-up as a verbatim forward-read.
SP5's build includes a docs task rewriting it to this spec's model. **Completeness check =
grep for the literal phrase "Reads forward from your frontier through the pile to live" and
for "catch-up"/"catch_up" across the whole file — never section-walking** (the §8 table row
was missed once by section-walking; recon 2026-07-17 re-verified the full location list):

| Location (2026-06-29 spec) | Rewrite |
| --- | --- |
| §8 table row :427 + ADD paragraph :440-443 | Catch-up = async LLM summary + burn; chord proposed ⌃⌘L unbound |
| §10.1 :533-576 (incl. the :562-568 catch-up paragraph + :574-576 Observable) | The summary model (§1-§2 here); **and the C1→C1' correction** (pile-seeking workspace-first skip, cue names the target — owner ruling 2026-07-17 in the ledger) — §10.1's :542-546 still describes C1 |
| D17 row :647 (+ D7 :637, D16 :646 cross-refs) | "catch-up key" = the summary verb; semantics unchanged otherwise ("left" = stopped/quiet stands) |
| §8 preemption line :389 | "catch-up readout" → "catch-up landing" (redirect class transfers to the landing, §2 here) |
| §9 :474-478 aged-out + :487-489 scope | Cue now rides the catch-up ack; "voluntary and in-place" stands |
| Changelog | New entry pointing here |

Also: `2026-06-29-sonari-voice-arbitration-reconciliation.md` gets a **superseded banner**
(point-in-time audit record — banner, not rewrite). Recon confirmed no other living spec
carries the old model (Echo/Phase-2/session-streams docs are historical; README's "catch-up"
label stays accurate; whereami-v2 and chooser specs are clean). ⌃⌘W's spoken-count change
(§8) must also be reconciled where W's grammar is specced (whereami-v2's `{u} unheard`
definition — the *number's source* changes, the grammar does not).

## 11. New machinery inventory

- `MsgType`: `catch_up` (net-new — legacy deleted at `b4b3be1`, zero code remnants,
  recon-verified) + internal `catchup_result`.
- Keymap: `ACTION_MESSAGES["catch_up"]`, absent from `_DEFAULT_KEYS` (unbound; ⌃⌘L proposed).
- Handler: `daemon/features/` catch-up module (press/cancel/result/burn).
- Adapter: `HostSummarizer` protocol + `ClaudeCliSummarizer` + slice renderer + sanitizer +
  digest builder.
- Config: `summarizer`, `summary_voice`, `summary_model`.
- Speaker: voice-per-utterance support for the body segment (render = frame item + body item
  + tail item on one sequenced delivery; mechanics at plan time — burn fires only when the
  full sequence completes).

## 12. Testing

- **Golden strings** (the `make_daemon`/`FakeSpeaker.spoken` idiom) for every deterministic
  piece: ack (incl. singular + aged-out rider), "Nothing to catch up.", "Cancelled.", frame,
  tail presence/absence, digest (incl. verbatim-anchor extraction), "{folder} ended.".
- **FakeSummarizer** for the middle: success, each failure reason, timeout, markdown-garbage
  (sanitizer proof), over-ceiling output (clamp proof), empty (→ digest).
- **Env-scrub assertion**: the child env passed to the injected popen lacks both keys while
  inheriting the rest — the make-or-break test.
- **Frontier**: burn advances exactly to the pinned `slice_end` (not live) on completion;
  cut/cancel advance nothing; queued items ≤ slice_end dropped; monotonicity hammers stay
  green.
- **Counts**: W's u cross-turn migration (the 14-vs-2 scenario reproduced as a test: same
  pile → same 14 on skip/catch-up/W-decomposition); u floor at 0 when queue exceeds pile.
- **Concurrency**: one in flight; press-while-pending cancels; stale result after cancel is
  dropped; result delivery via `handle_message` only.
- Guards (6) green at every commit; no golden test asserts LLM output content.

## 13. Owner-held items (nothing ships past these without his word)

1. **Smoke tests** (§4) — his quota, his go, before build starts.
2. **Chord binding** (⌃⌘L proposed, ships unbound) — ear-gate.
3. **Every new string** (ack, frame, tail, digest, cancel, ended, "Nothing to catch up.") +
   the **summary voice** choice + the **length ceiling** (8) — one-liner vetoes at ear-pass.
4. Push of anything to origin — his `!`, as always.

## 14. Out of scope

- Auto-triggered summaries (idle detection, SESSION_END digests) — explicit press only.
- Codex/Copilot adapters (interface-ready; their builds gate on their own smoke tests).
- Transcript persistence across SESSION_END/restart (SP6).
- The end-to-end streamline pass (owner's queued initiative — this spec only hands it the
  unified pile vocabulary).
- `setup-token`/lean-config latency optimization (deferred, needs his hands once).
- Summary-result caching, mid-prep progress ticks (ear-pass candidates, not v1).
