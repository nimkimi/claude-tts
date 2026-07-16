# Sonari Cheap Wave 1 — Design Spec (13 ratified eyes-free fixes)

**Date:** 2026-07-16 · **Status:** DRAFT — pending design review (chord + earcon-asset gates below)
**Branch:** `fix/cheap-wave-1` · **Baseline:** `main @ 1af21ce`, suite 987 passed / 1 skipped
**Ratified:** owner GO 2026-07-16 to this 13-item set. Ships independent of the SP4 frontier build.

**Sources:** `.superpowers/sdd/ux-atlas/friction-ledger.md` (evidence + citations),
`.superpowers/sdd/ux-atlas/reeval-sheet.md` (Block 0 + the two Block-1 ratifications),
`.superpowers/sdd/ux-atlas/opportunity-map.md` (cheap-solid sketches). Every code citation
below was re-verified against the source in this worktree; where the atlas's fix direction
did not survive code-level verification, the section says so inline and the change is
summarized in §16.

---

## 1 · Invariant glossary (the "risk class" vocabulary)

- **M1** — the keep-going scan+select+set_speaker+pop+claim runs in ONE locked block
  (`host.py:481-513`); nothing may split it or add a TOCTOU gap.
- **R12 / D10** — the workspace never moves on its own; keep-going advances only the
  speaker (`host.py:490-494` comment).
- **Fork-2** — no frontier/tool/cue work may un-mute a session; a chooser landing never
  un-mutes (ratified anchor 3).
- **Guard set** — `tests/test_concurrency_guards.py` is PERMANENT and stays green at every
  commit; **any speak-loop change adds itself to the guards' hammer set** (campaign
  constraint, `docs/superpowers/plans/2026-06-29-sonari-voice-arbitration-campaign.md:14`).
- **Anchors** — the seven ratified fixed points (reeval sheet). This wave touches only
  cue/teaching/content sides, never an anchored gesture or semantics.

Wave order below = riskless first. W1–W3 are near-zero risk; W11–W13 are the three that
touch production pointers or the speak loop.

| Wave | Ratified item | One-liner | Risk |
|---|---|---|---|
| W1 | 11 | ⌃⌘J empty cue → `at_front` | one line |
| W2 | 9 | E1 monotonic timestamps on `HistoryEntry` | substrate, no behavior |
| W3 | 5 | Verbosity confirmation on the live path | one handler line |
| W4 | 2 | ⌃⌘D miss safety (no drain, spoken cue) | handler branch |
| W5 | 1 | `voice_state` staleness fix on Policy-A submit | one enum write |
| W6 | 3 | Distinct failure tones (3-class taxonomy) | earcon plumbing |
| W7 | 4 | Permission expiry earcon + queue cleanup | wait-path branch |
| W8 | 6 | Restart boot cue | boot-path add |
| W9 | 7 | Decision chime call-sign spearcon | protocol field + sequencer |
| W10 | 10 | Backlog-depth under quiet in the ⌃⌘W Also-map | ⌃⌘W string content |
| W11 | 12 | Two-pointer collapse (⌃⌘J + confirmations → workspace) | production retarget |
| W12 | 8 | Repeat-last verb | new chord + speak-loop capture |
| W13 | 13 | Keep-going pre-roll spearcon | inside the M1 block |

---

## 2 · W1 — ⌃⌘J empty cue enqueues `at_front` (item 11)

**Lived problem.** Pressed mid-flood to ask "anything else waiting?", the answer lands at
the *tail* of the very backlog you're escaping (ledger below-cut #8 / opportunity 6.2 —
"the one control cue in the codebase not marked `at_front`" — a framing this spec narrows
below: it is not literally the only one).

**Exact new behavior.**
- GIVEN the speaker's queue holds N backlog items, WHEN ⌃⌘J finds no waiting session,
  THEN "No session waiting." is the NEXT thing voiced (ahead of the backlog). String
  unchanged; only queue position changes.

**Code touchpoints.** `src/sonari/daemon/features/focus.py:54-57` — add `at_front=True`
to the existing `_enqueue(tgt, "prose", "No session waiting.", ...)`. (Correction
(review-found): this is NOT the only control cue lacking `at_front` — `control.py:218`'s
"Nothing playing." + Also-clause, `playback.py:66`'s "Stopped.", and `playback.py:86`'s
"All stopped." also lack it, all `pause_exempt`-only. This wave item fixes only
`focus.py:54`'s "No session waiting." cue; the other three are unchanged and out of
scope.)

**Risk class.** None of M1/R12/Fork-2. Guards untouched (no speak-loop change).

---

## 3 · W2 — E1: monotonic timestamps on `HistoryEntry` (item 9)

**Lived problem.** The whole temporal family (age, stall, elapsed) is impossible because
`HistoryEntry` has no time field (opportunity map, substrate E1 — "no prior doc flagged
it"). This wave lays the field only.

**Exact new behavior.**
- GIVEN any `history.record(...)` call, THEN the returned entry carries `stamp` = the
  injected clock's value at record time; stamps are non-decreasing across successive
  records. **No spoken string, earcon, or handler reads it in this wave.**
- `SessionHistory(cap=..., clock=time.monotonic)` — clock injectable for tests; default
  `time.monotonic`.

**Deviation from the atlas (verified).** Atlas E1 sketched `stamp=time.time()` plus a
per-stream `last_activity`. Ratified item 9 says *monotonic*; `time.monotonic()` is used
(the codebase itself prefers monotonic for age reads — see the STATUS comment,
`src/sonari/daemon/host.py:163-169`). `last_activity` is **dropped from this wave** (the
ratified text names `HistoryEntry` only); it joins the SP-later liveness work.

**Code touchpoints.** `src/sonari/history.py:14-24` (`__slots__` gains `"stamp"`),
`:27-33` (ctor gains `clock`), `:35-45` (`record` stamps). Extend `tests/test_history.py`.

**Risk class.** Pure substrate. Explicitly out of scope: ANY behavior derived from stamps
(age readouts, stall detection, expiry ranking) — that is SP5+ material.

---

## 4 · W3 — Verbosity confirmation on the live path (item 5)

**Lived problem.** `/sonari:verbosity` confirms nothing; the built "Verbosity {level}."
speech is stranded on the dead `CYCLE_VERBOSITY` handler, 0 senders (ledger below-cut,
opportunity 8.6).

**Exact new behavior.**
- GIVEN any valid `SET_VERBOSITY` (CLI `sonari verbosity quiet`, the skill, any client),
  WHEN the handler accepts the value, THEN it speaks exactly `"Verbosity quiet."` /
  `"Verbosity medium."` / `"Verbosity everything."` — enqueued to **`workspace()`**
  (per W11's collapse; this confirmation is born on the collapsed target),
  `mute_exempt=True, pause_exempt=True`.
- Invalid value → unchanged early return, no speech (existing `_valid_verbosity` gate).
- Setting the same value re-confirms (idempotent readback; no changed-only gate).

**Justified extension.** The stranded implementation (`control.py:138-140`) used a plain
enqueue to `foreground()`. Two deltas: target follows W11, and the cue gains
`mute_exempt+pause_exempt` — a settings confirmation that can be silently swallowed while
the voice is held reproduces the very silence this item kills. Note: prose muting under
quiet happens at `on_prose` (`prose.py:20`), so direct `_enqueue` cues speak at every
verbosity — "Verbosity quiet." IS voiced as the last thing you hear.

**Code touchpoints.** `src/sonari/daemon/features/control.py:106-113` (`on_set_verbosity`
gains the confirm), `:128-141` (dead `on_cycle_verbosity` left as-is — removal is
protocol-registry surgery out of this wave's scope), `src/sonari/cli/control.py` (sender,
no change needed).

**Risk class.** None of M1/R12/Fork-2. Guards untouched.

---

## 5 · W4 — ⌃⌘D miss safety (item 2)

**Lived problem.** On a session with no pending decision, ⌃⌘D pops the browse queue to
empty hunting a decision that lives elsewhere, and says nothing — silent data loss
(ledger §3.1, HIGH, CONFIRMED; the verb-naming half stays an [EAR] item, untouched).

**Exact new behavior.** Define **hit** = the target's queue holds a decision item
(`queue.has_decision()`) OR the target has a live pending blocking decision
(`host._pending_decisions.get(target)` non-None).
- GIVEN the target session has NO hit, WHEN ⌃⌘D fires, THEN speak exactly
  `"No decision here."` (`mute_exempt=True, pause_exempt=True, at_front=True`, routed to
  `speaker()` if non-None else the target — same routing rule as ⌃⌘J's empty cue), and
  **nothing else happens**: no queue drain, no `speaker.cancel()`, no voice/workspace
  move, no heard-marking, no `voice_state` write, no window raise.
- GIVEN a queued decision exists, THEN behavior is exactly today's (drain-to-decision,
  cross/raise, spearcon cue) — unchanged.
- GIVEN a live pending decision exists but its text is no longer queued (already
  narrated, still answerable inside the 120s window), THEN do NOT drain; re-speak the
  stored prompt text `at_front, mute_exempt` — sourced from
  `host._pending_decisions[target]["text"]` (see the scope addition immediately below;
  NOT `st.options`, which `on_permission_request` never writes) — so ⌃⌘D never claims
  "no decision" over an answerable one.

**Scope addition — `_pending_decisions` gains a `text` field (review-found: the original
remedy cited `st.options`, a field `on_permission_request` never sets).**
`on_permission_request` writes `host._pending_decisions[session]` at `decisions.py:173`
as `{"event": threading.Event(), "behavior": None}` — no text. Extend that literal with a
`text` field, reusing the SAME string already computed at `decisions.py:163`
(`text = _permission_request_text(msg)`) and passed to `history.record(session,
"permission", text)` at `decisions.py:165` — i.e. `_pending_decisions[session]["text"] =
text`, one extra key on an already-built dict, no new computation. The third branch above
reads that field back.

**Sub-item — `on_reread_options` (`decisions.py:200-211`) gains the same fallback (SCOPE
ADDITION surfaced by this review, not in the original 13-item ratification).**
GIVEN `st.options` is empty/None for the foreground session AND a blocking permission is
pending in `_pending_decisions` for that session, WHEN `REREAD_OPTIONS` fires, THEN
re-speak `host._pending_decisions[fg]["text"]` (the same field the sub-item above adds)
instead of falling through to "No options right now." *Rationale (one line): the review
proved `REREAD_OPTIONS` is silently broken for this exact class today — `on_permission_request`
never sets `st.options`, so a live blocking permission can never be re-read via this verb
— and it was already a below-cut honesty item the owner's wave direction covers, so this
wave should close it alongside the ⌃⌘D fix rather than leave the same gap on a second
gesture.*

**Deviation from the atlas (verified).** The atlas fix was queue-scoped ("no decision
here + don't drain"). Code-level: an answerable-but-already-read permission has an empty
queue (`has_decision()` scans queued items only, `queue.py:78-81`) — a queue-scoped cue
would speak a lie exactly where honesty matters. Hence the two-part hit predicate.

**Code touchpoints.** `src/sonari/daemon/features/playback.py:92-134` (`on_jump_decision`
— the miss guard goes BEFORE the `voice_state`/focus/cancel writes at `:100-114`),
`src/sonari/queue.py:70-76` (`jump_to_decision`, unchanged — the caller guards),
`:78-81` (`has_decision`), `src/sonari/daemon/features/decisions.py:156-174`
(`on_permission_request` — add the `text` key to the `_pending_decisions` literal at
`:173`, reusing the `:163` local), `:200-211` (`on_reread_options` — add the
`_pending_decisions` fallback described in the sub-item above).

**Risk class.** Fork-2: the miss branch must not touch `st.stopped` (it doesn't touch the
stream at all). R12 untouched. Guards untouched (handler-side only).

---

## 6 · W5 — `voice_state` staleness fix (item 1)

**Lived problem.** A Policy-A submit hands the voice to a non-stopped session; the speak
loop reads it aloud (the held branch gates on the stream's own `.stopped`,
`host.py:451-453`, not the enum), but `voice_state` stays `"quiet-hold"` — ⌃⌘W says
"on hold" while you audibly hear it talk (ledger §1.3, CONFIRMED; the missing-enum-write
bug under anchored Policy-A, not a preempt change). Side effect verified: the stale
enum also keeps the keep-going gate closed (`host.py:485` requires `"flowing"`).

**Exact new behavior.**
- GIVEN `voice_state == "quiet-hold"`, WHEN a Policy-A submit takes (or retains) the
  voice AND the resulting speaker's stream is NOT stopped, THEN write
  `voice_state = "flowing"` in the same handler transaction. No new speech or earcon.
- ⌃⌘W pressed during the subsequent playback now says `"Voice: {folder} {n}, playing."`
  (derivation unchanged, `control.py:227-233`; the input is no longer stale).
- GIVEN the submit is DENIED by Policy-A (register-only branch), THEN no enum write.
- GIVEN `voice_state == "stopped-all"`, THEN no lift ever (the master quiet is
  deliberate; under stopped-all new streams are born stopped anyway, `host.py:195-201`,
  so the non-stopped condition is unreachable — the guard is belt-and-braces).
- GIVEN the taking session's stream IS stopped (a muted speaker self-submitting), THEN no
  lift — "on hold" remains true.

**Code touchpoints.** `src/sonari/daemon/features/lifecycle.py:71-85` (both take-voice
branches of `on_set_foreground`; the denied branch at `:85` untouched). Precedents for
the write: `focus.py:66` (⌃⌘J), `playback.py:55,100` (⌃⌘S-start, ⌃⌘D). Cross-check:
`lifecycle.py:132-133` (the session-end phantom-hold lift this also heals),
`state.py:26-31` (enum born `"flowing"`).

**Risk class.** M1 untouched (handler runs under the same lock as the claim —
`host.py:543-551`). Fork-2: writes the global enum only, never any `st.stopped`. The SP4
synthesis's inherited open-Q6 is discharged by this item — note that in the SP4 plan.

---

## 7 · W6 — Distinct failure tones (item 3)

**Lived problem.** Nine failure sites share one Sosumi: "answered from the wrong
session", "dead chooser digit", "empty jump", and "speak-loop crash" are
indistinguishable (ledger §3.2a, HIGH, CONFIRMED).

**The taxonomy (3 classes, principled by what-you-should-do-next — NOT nine sounds).**

| Class | Meaning / next action | Earcon kind | Call sites (verified, exhaustive) |
|---|---|---|---|
| Invalid / nothing there | "that input has no referent here; nothing lost" | `error` (Sosumi, **unchanged**) | `chooser.py:67,159,187,251` · `focus.py:59` fallback · `playback.py:46` · `control.py:220` · `decisions.py:183` |
| Misdirected | "valid intent, wrong session — go to the asking one" | `error_misdirected` (**new**) | `decisions.py:188` (⌃⌘⏎/⌃⌘⎋ with no pending decision on the workspace) |
| System failure | "Sonari itself failed; content preserved unheard" | `error_system` (**new**) | `host.py:431` (`_signal_speak_failure`) |

**Exact new behavior.**
- GIVEN a pending permission on session B and workspace on A, WHEN ⌃⌘⏎ fires, THEN the
  `error_misdirected` tone plays (not Sosumi).
- GIVEN an utterance raises in the speak loop, THEN `error_system` plays (not Sosumi).
- All other failure sites keep Sosumi byte-identically.

**Asset proposals — OWNER GATE (his ear at the design review):**
`error_misdirected` → `/System/Library/Sounds/Basso.aiff` (deep descending "wrong door");
`error_system` → `/System/Library/Sounds/Blow.aiff` (hollow, "broke inside").
Alternates: Frog, Pop; or the atlas's `afplay -v` loudness-variant of Sosumi (rejected as
default: loudness is ambient-volume-dependent — unreliable as a semantic channel
eyes-free; kept on the palette for his ear).

**Deviation from the atlas (verified).** The opportunity map called this "CHEAP via
`afplay -v`". Two code facts change the shape: (a) `Speaker.earcon` silently no-ops on a
kind missing from the config dict (`speaker.py:102-104`), and (b) bootstrap merges
defaults only when the whole `earcons` key is absent (`bootstrap.py:73-74`) — so for
every EXISTING install a new config-dict kind would be **silently disabled**, the worst
eyes-free failure. Fix shape follows the codebase's own precedent: `Speaker.pitch`
resolves its asset package-side precisely so "the cue can never be silently disabled by
an existing user's earcons config" (`speaker.py:109-123`). New failure kinds resolve:
config dict first (user-overridable), then fall back to the built-in default path when
the kind is absent. Sosumi's `error` key keeps today's semantics.

**Code touchpoints.** `src/sonari/platform/macos/earcon.py:9-15` (`_DEFAULTS` gains the
two kinds), `src/sonari/speaker.py:97-107` (`earcon()` gains the never-silent fallback
for the new kinds), `src/sonari/daemon/features/decisions.py:188`, `src/sonari/daemon/
host.py:431` (call-site kind swaps).

**Risk class.** `host.py:431` is inside `_signal_speak_failure` (called from the speak
loop's except paths) — the change is a string constant, but it IS on the speak-loop path:
**the failure-tone scenario joins the guard hammer set** (cheap: the guards already drive
speak failures). M1/R12/Fork-2 untouched.

---

## 8 · W7 — Permission expiry: earcon + queued-text cleanup (item 4)

**Lived problem.** A blocking permission silently dies at the ~120s daemon wait
(`PERMISSION_WAIT_TIMEOUT`, `host.py:19`); no cue marks the mark, and the queued
permission text still reads later as a live, answerable channel → you answer correctly
and get an error tone (ledger §4.1, HIGH, CONFIRMED). Ranking-by-expiry is explicitly a
later refinement (needs W2's stamps + SP5) — NOT in this wave.

**Exact new behavior.**
- GIVEN a blocking permission request on session B, WHEN the daemon wait times out
  (`got == False` in `_await_permission_decision`), THEN:
  1. the `permission_expired` earcon plays once (asset proposal — OWNER GATE:
     `/System/Library/Sounds/Purr.aiff`, soft low fade, "it slipped away"; alternates:
     a rate-shifted Funk via `afplay -r` — the atlas's aging-chime lever — or Bottle);
  2. under the daemon lock, B's still-queued permission `SpeechItem` is removed
     (`queue.remove_by_id`) and its pending-heard marker dropped — a later ⌃⌘D/read on B
     never voices the dead ask as answerable.
- GIVEN the request was ANSWERED (`got == True`), or superseded by a newer request for
  the same session (the stale-waiter release, `decisions.py:171-173`), THEN no expiry
  earcon and no cleanup (the newer request owns the queue slot).
- History is NOT cleaned: the transcript keeps the permission entry as a record (nav
  replay is explicit archaeology, kind-labelled "permission"; the QUEUE is the "live and
  answerable" channel — only it must not lie). SP4's frontier reads history; leaving it
  intact is load-bearing.
- Edge (accepted, documented): if the permission text is IN FLIGHT at expiry it cannot be
  removed from the queue (already popped); it finishes, and the expiry earcon that lands
  beside it is the honest context.

**Mechanism.** `on_permission_request` records the enqueued item's id in the pending dict
(`{"event": ..., "behavior": None, "item_id": ...}`); `_enqueue` returns `item.id`
(currently returns None — non-breaking, all callers ignore). `_await_permission_decision`
does the cleanup inside its existing tail lock.

**Code touchpoints.** `src/sonari/daemon/host.py:19` (constant, unchanged), `:223-247`
(`_enqueue` returns the id), `:322-336` (`_await_permission_decision` — earcon + cleanup
on the timeout branch), `src/sonari/daemon/features/decisions.py:156-174`
(`on_permission_request` stores `item_id`), `src/sonari/queue.py:98-106` (`remove_by_id`,
exists — built for the chooser, reused verbatim).

**Risk class.** The wait runs OUTSIDE the daemon lock by design (`host.py:322-325`
comment); cleanup takes the lock exactly as the existing pop does — no new lock ordering.
M1/R12/Fork-2 untouched. Guards untouched (no speak-loop change; earcon is fire-and-forget).

---

## 9 · W8 — Restart boot cue (item 6)

**Lived problem.** A daemon restart rebuilds the world empty and announces nothing; the
first ⌃⌘W is a bare error tone indistinguishable from a mis-press (ledger §1.6,
HIGH-weakened; the cue is explicitly decoupled from SP6 persistence — reeval Block 0).

**Exact new behavior.**
- GIVEN the daemon process completes startup (singleton lock won, server about to run),
  THEN it speaks exactly `"Sonari restarted. Sessions re-register on their next prompt."`
  once, at every verbosity (a trust cue, not narration).
- Plays on every daemon boot, including the first-ever (accepted looseness: without SP6
  persistence there is no state to distinguish first-boot from restart; SP6 refines the
  wording if the owner wants).

**Mechanism — deviation from the atlas (verified).** The cue cannot ride the queue: at
boot no session is registered, the speak loop plays only `speaker()`'s stream
(`host.py:481-517`) and keep-going scans only `sessions.session_ids()` (`host.py:57`) —
an enqueued boot cue would never voice. Fix: a one-shot daemon thread started in
`bootstrap.main()` immediately before `daemon.run()`, calling `speaker.speak(...)`
directly. Overlap window with the first real utterance is theoretically nonzero but
human-timescale-empty (sessions re-register on their next prompt); accepted and
documented. Rejected: blocking speech before `run()` (delays the socket bind that
lazy-start clients poll).

**Code touchpoints.** `src/sonari/daemon/bootstrap.py:51-93` (`main()`, thread start
between `:92` and `:93`). String lives beside it as a constant for the test to import.

**Risk class.** None of M1/R12/Fork-2 (pre-loop, pre-session). Guards untouched — the
speak loop itself is not modified. **Awareness note (review-found, not a fix):**
`Speaker.speak` is looser than "thread-safe by construction" suggests — `_current` is
lock-guarded (`_current_lock`) but the method does not hold the lock across
`proc.wait()`, so two concurrent callers can each be mid-wait at once. Benign here: at
boot no session is yet registered, so the speak loop has nothing to say and the overlap
window with the one-shot boot thread is empty — there is no second caller for the boot
cue to race against.

---

## 10 · W9 — Decision chime call-sign (item 7)

**Lived problem.** The decision chime is anonymous — "a permission, *somewhere*"; the
correct follow-up gesture depends on WHO asked, which the sound never says (opportunity
1.1 — "the single sharpest discovery gap"; ledger §3.2's addressing gap is the same
seam). The ratified fix: the cue gains the requesting session's spearcon.

**Exact new behavior.**
- GIVEN session `backend-api` (spearcon cached) raises a blocking permission, WHEN the
  chime fires, THEN the ear hears: Funk chime, THEN the ~200ms "backend" spearcon —
  sequenced, not overlapped.
- Same for the legacy non-blocking decision cues: CHOICE (Ping), PLAN (Submarine),
  PERMISSION-notification (Funk) each gain the asking session's spearcon after the chime.
- GIVEN the spearcon is not cached yet, THEN chime alone (unchanged today-behavior) and
  the miss kicks background generation (`spearcon.py:76-83`) — self-heals by next time.
- GIVEN the EARCON message carries no session (old hook version), THEN chime alone.
- `turn_done` is untouched (it has its own suppression logic, `prose.py:62-73`).

**Deviation from the atlas (verified).** Opportunity 1.1 called this "wiring order, not
new infra". Two code facts say slightly more: (a) the legacy decision chimes are
**sessionless** protocol messages (`hooks_entry.py:53,62,78`; asserted sessionless at
`prose.py:60-61`) — the EARCON message must gain an optional `session` field (backward/
forward compatible: unknown fields are ignored; absent field → chime-only fallback);
(b) earcons are fire-and-forget Popens (`speaker.py:97-107`) — chime and spearcon would
overlap; a tiny sequencer (spawn chime proc → `wait()` → spawn spearcon) on a fire-and-
forget thread is required. Neither blocks the caller.

**Code touchpoints.** `src/sonari/hooks_entry.py:53,62,78` (msgs gain `session=session`),
`src/sonari/daemon/features/prose.py:53-76` (`on_earcon` decision-kind branch: sequence
chime + `host._spearcon_path(folder)`), `src/sonari/daemon/features/decisions.py:164`
(blocking path — session known in-hand, same sequencer), `src/sonari/speaker.py` (the
sequencer helper lives beside `earcon()`).

**Risk class.** Not cancellable/barge-able — same as today's chimes (earcons never are);
total added audio ≈ 200ms. No speak-loop change → guards untouched. M1/R12/Fork-2
untouched. Out of scope: severity envelopes, urgency ordering (opportunity 3.4 — EAR).

---

## 11 · W10 — Backlog-depth by ear before un-muting (item 10)

**Decision: fold into the ⌃⌘W Also-map — no new chord, no new verb.** Justification: the
un-mute decision is made AT the ⌃⌘W readout (the Also-map is the ratified "teleport
dial-pad"); a targeted query would spend a chord and a new teaching burden to deliver a
number ⌃⌘W already frames. Rejected: `⌃⌘'-shaped "how big is X"` query — chord space is
precious (see §15) and the roster surface already exists.

**Lived problem.** Under GLOBAL quiet verbosity prose never queues (`prose.py:20`), so
the Also-map's `", {k} waiting"` (`k = len(st.queue)`, `control.py:41`) reads 0 for a
session that has produced for an hour — the pile is invisible exactly when you're
deciding whether to step into it (ledger §2.3's quiet blind spot; the anchored grammar
SHAPE is untouched — the count CONTENT is the open half, per the reeval sheet).

**Exact new behavior.**
- Per Also-map entry, compute `k = len(st.queue)` (unchanged) and
  `u = max(0, len(history.unheard(s)) - k)` — the recorded-but-not-queued unheard floor.
- GIVEN `k > 0`, THEN `", {k} waiting"` appears exactly as today.
- GIVEN `u > 0`, THEN `", {u} unheard"` is appended (after the waiting clause when both).
  Example under quiet: `"Also: 2 billing, muted, 12 unheard; 3 auth."`
  Example quiet + queued decision: `"2 billing, 1 waiting, 11 unheard"`.
- GIVEN `k == 0 and u == 0`, THEN the entry is unchanged (no clause).
- Non-quiet strings are unchanged for the ordinary case: everything that is recorded is
  also queued, so `u == 0` for the common Also-map candidate. (The only stream with an
  in-flight-but-unheard entry under normal play is the speaker's, and the Also-map
  excludes the speaker, `control.py:242`; the None-speaker branch has no in-flight
  either.)

**Correction (review-found: "byte-identical / u==0 always" is falsified by a
preemption-cut — the surfaced count is nonetheless correct behavior).** A ⌃⌘J
(`focus.py:65`) or a crossed ⌃⌘D (`playback.py:102`) calls `sessions.focus()`, which moves
`_speaker` off the former speaker (`sessions.py:234`) while cancelling its in-flight item.
`host.py:535` re-queues an interrupted item only when its OWN stream is `stopped`, so a
non-stopped former speaker's cut item is left `heard=False` and un-queued
(`note_spoken(..., False)`, `host.py:319/541`). That former speaker is now a non-speaker
Also-map candidate with `unheard > k`, so `u ≥ 1` and ", N unheard" CAN legitimately
appear in a non-quiet string. This is not a leak: it is a genuine unheard pile created by
preemption, and R-8's "preemption class = cut, no resume" keeps that pile browsable
rather than lost — surfacing it is correct. The guarantee this wave actually provides is
**no spurious unheard beyond genuinely-unheard preempted content**, not strict non-quiet
byte-identity.

**The `- k` subtraction (verified necessity).** Queued items' history entries are ALSO
unheard until spoken (`host.py:309-320` flips heard on completion), so a raw
`len(unheard)` double-counts every queued item. The subtraction is an approximation in
the caller's favor (never overstates), documented as a floor.

**Honest limitation (spoken nowhere, documented here).** `unheard()` is current-turn-
bounded (`history.py:145-154`); the count is a floor across a multi-turn pile.
**Explicitly NOT built:** any frontier-derived count — cross-turn true depth is SP5's
`(msg_id,seq) > frontier` read (flagged in `sp4-recon-synthesis.md §6 Q5` as an SP5
change to this exact string). This wave reads only the existing heard-markers.

**OWNER GATE.** The `", {u} unheard"` wording is a ⌃⌘W string change inside the anchored
grammar — his ear vetoes/tunes the word at the design review ("unheard" vs "unread" vs
"piled").

**Code touchpoints.** `src/sonari/daemon/features/control.py:23-45` (`_also_clause`),
`src/sonari/history.py:145-154` (`unheard`, gains its first prod caller — the docstring's
"no replay consumer" note is amended, the turn-bounding is now load-bearing),
`src/sonari/daemon/features/prose.py:20` (the quiet gate, unchanged — cited as cause).

**Risk class.** Anchor 2 content-side only. No speak-loop change → guards untouched.
Self-pile inclusion (the "own pile before resuming" dial) stays an EAR item — out of scope.

---

## 12 · W11 — Two-pointer collapse (item 12, Block-1 ratification)

**Lived problem.** The hands act on three pointers but ⌃⌘W teaches two; the nameless
third, `foreground()`, is what ⌃⌘J keys on and where the rate confirmation lands — so
"Rate 250." can be enqueued to a session you aren't hearing, i.e. **you hear nothing at
all** (ledger §1.1). Ratified: collapse onto `workspace()`; `foreground()` stops being a
gesture target.

**Observable behavior change — precise.**
1. **⌃⌘J targeting** (`focus.py:44-45` — today `fg = sessions.foreground()`,
   `_waiting_target(ctx, exclude=fg)`): the exclusion becomes `workspace()`. New rule:
   ⌃⌘J never jumps to the session your keyboard is on (`workspace()`) nor the one you're
   hearing (`speaker()`, already excluded in `_waiting_target`, `focus.py:21`); every
   other waiting session is eligible. Felt difference (only under live focus divergence):
   after keep-going drifted the voice to B and you clicked C, the old exclude was your
   last *deliberate* session A — so ⌃⌘J could "jump" to C, the terminal already in front
   of you. Now C is excluded and A is reachable.
2. **⌃⌘J empty-cue routing** (`focus.py:54`): fallback target `speaker() or foreground()`
   becomes `speaker() or workspace()`.
3. **Rate-delta confirmation** (`control.py:89-91`): `"Rate {n}."` enqueues to
   `workspace()` — the terminal you're at hears its own confirmation.
4. **Verbosity confirmation** (W3): born targeting `workspace()`.
5. **Degenerate case — no change:** `workspace()` resolves `focused_session()` falling
   back to `foreground()` (`sessions.py:132`, the ledger's §1.1 citation) — with no
   OS-focus signal (focus-watcher off, single terminal) every retargeted surface behaves
   byte-identically to today.

**The pointer itself survives internally** (ratified nuance): `sessions.foreground()`
remains as `workspace()`'s fallback and as plumbing (`STATUS`'s diagnostic field,
`control.py:153`; the CLI-only `STOP` handler, `playback.py:12`; `REREAD_OPTIONS`,
`decisions.py:202` — unbound, 0 senders). None of these is a hotkey gesture; they are out
of scope and unchanged.

**Correction (review-found: the exhaustiveness claim below was not exhaustive as
written).** One more site reads `foreground()` directly on a ⌃⌘ gesture's path:
`chooser.py:48`'s `origin = sessions.workspace() or sessions.foreground()`, reached on
⌃⌘Tab's first step (`CHOOSER_STEP` → `_open` → `_snapshot`). It is **provably dead code,
not a live behavioral read**: `workspace() = focused_session() or self._foreground` and
`foreground() = self._foreground` (`sessions.py:132-136,121-125`), so the `or
sessions.foreground()` fallback can only be reached when `workspace()` is already
falsy — at which point `foreground()` is `None` too. So no ⌃⌘Tab behavior changes; the
correct claim is **after this wave, no ⌃⌘ gesture's OBSERVABLE behavior depends on
`foreground()`** (not the stronger, falsified "no gesture reads `foreground()`
directly").

**Optional wave step (cleanup, not required for correctness — makes the claim literally
true, zero behavior change):** drop the redundant `or sessions.foreground()` at
`chooser.py:48`, leaving `origin = sessions.workspace()`.

**Code touchpoints.** `src/sonari/daemon/features/focus.py:44-45,54`,
`src/sonari/daemon/features/control.py:89-91` (+ W3's new confirm). Existing tests that
pin foreground-targeting (`tests/test_daemon_focus_nav.py`, `tests/test_cli_focus_follow.py`)
are updated to the new oracle — behavior change is the ratified point, not a regression.

**Risk class.** Production behavior change (owner-ratified today, Block 1). R12 preserved
— these surfaces READ `workspace()`; nothing new writes it. M3 (answer follows workspace)
already holds (`decisions.py:185`); this makes the rest of the surface consistent with
it. Fork-2/M1 untouched. Guards untouched.

---

## 13 · W12 — Repeat-last (item 8)

**Lived problem.** The single most frequent by-ear need — "say that again" — has no verb;
only decisions re-speak, and that handler is stranded (opportunity 9.1, `intents #2`,
`below #4`).

**Exact new behavior.**
- New protocol message `REPEAT_LAST`, new keymap action `repeat_last` (chord: §15).
- The daemon tracks **the last COMPLETED non-`mute_exempt` utterance** as
  `(spoken_text, audio_path)` — captured at speak-completion with the text AS SPOKEN
  (i.e. `_attributed_text`'s output, folder prefix included: verbatim = what your ear
  got, `host.py:288-307`). The non-`mute_exempt` filter excludes control chrome (⌃⌘W
  readouts, "Stopped.", jump cues) AND makes repeat idempotent: the repeat playback
  itself is enqueued `mute_exempt` and therefore never becomes the new target — pressing
  it N times re-speaks the same content utterance.
- GIVEN a last utterance exists, WHEN `repeat_last` fires, THEN: barge-in-class,
  capture-and-requeue **exactly the ⌃⌘W discipline** (`control.py:222-254`): capture the
  in-flight item + its heard-entry, `speaker.cancel()`, re-queue the interrupted item
  `at_front` FIRST (ends up deepest), then enqueue the repeat text `at_front`
  (`mute_exempt=True, pause_exempt=True`) — routed to `speaker()`; when `speaker()` is
  None, to a playable workspace stream else the error earcon (mirror ⌃⌘W's None-speaker
  branch, `control.py:193-221`).
- GIVEN no last utterance (fresh boot), THEN speak exactly `"Nothing to repeat."`
  (same exemptions/routing). Spoken cue, not an error tone — an empty repeat is not a
  mis-press.
- A spearcon-only last utterance repeats the audio file (audio_path replay).

**Code touchpoints.** `src/sonari/protocol.py` (MsgType), `src/sonari/keymap.py:26-47`
(`ACTION_MESSAGES`) + `:54-60` (`_DEFAULT_KEYS`, chord per §15),
`src/sonari/daemon/host.py:505-541` (capture at the completion site — the spoken `text`
is computed at `:513` and completion is known at `:519-527`), new handler in
`src/sonari/daemon/features/playback.py` or a sibling, `src/sonari/daemon/state.py`
(the `(text, audio_path)` slot beside `_last_spoken_session`).

**Risk class.** **Speak-loop change** (the capture write in `_speak_loop_once`) → the
campaign constraint applies verbatim: **REPEAT_LAST joins the guard hammer set**
(`tests/test_concurrency_guards.py` hammers it alongside PAUSE/FLUSH). M1: capture is a
post-speak assignment under the existing tail lock (`host.py:529-539`) — no new locked
region, no gap. Fork-2: repeat enqueues to the speaker/playable-workspace, never
un-stops anything. Barge-in interaction is the specced capture-park-resume — the
interrupted item is never lost.

---

## 14 · W13 — Keep-going pre-roll spearcon (item 13, Block-1 ratification)

**Lived problem.** The most frequent voice switch carries the thinnest cue: keep-going
splices the folder name onto the first content sentence (`host.py:288-307`); miss the
word and your model of who's talking is silently wrong. Deliberate switches all get an
`at_front` spearcon pre-roll; the passive switch gets none (ledger §2.2, HIGH, CONFIRMED;
spec §11:474 even promises the spearcon keep-going never honored).

**Exact new behavior — delivery only.**
- GIVEN keep-going advances the speaker from A to B (selection: UNCHANGED — same
  `_select_keep_going`, same longest-waiting `oldest_id()` order, anchor 7), WHEN the
  switch happens, THEN the ear hears: B's ~200ms folder spearcon, THEN B's first content
  sentence WITHOUT the spliced folder prefix (the spearcon claims attribution via
  `names_session`, exactly like a deliberate jump's cue — `host.py:297-300`).
- GIVEN B's spearcon is not cached, THEN today's behavior byte-identically (spliced
  folder prefix, `_attributed_text`) and the miss kicks background generation —
  self-heals. (Deliberate switches fall back to a SPOKEN "Jumping to..." cue; the passive
  fallback deliberately stays the splice — no new string invented for a fallback path.)

**Mechanism (the M1 obligation, stated).** Inside the EXISTING single locked block
(`host.py:481-513`), after `set_speaker(next_sess)`: if a spearcon path resolves for the
new speaker's folder, synthesize the spearcon `SpeechItem`
(`mute_exempt=True, names_session=True, audio_path=...`) and claim IT as this
iteration's item, leaving the content item queued (popped normally next iteration, now
attribution-claimed). No content pop is skipped-and-lost; nothing leaves the lock;
`_select_keep_going` is not modified; the locked block's scan+select+set_speaker+claim
shape is preserved (the pop of content moves one iteration later — the queue, not a
local, holds it across iterations, so FLUSH/STOP semantics are inherited for free: an
intervening FLUSH clears the content item exactly as it would any queued item).
Equivalent alternative for the implementer: `enqueue_front(spearcon)` then `pop_next()`
inside the same lock — same observables; reviewer's pick.

**MUST NOT (ratified constraints, restated as test oracles):**
- `_select_keep_going` (`host.py:43-68`) is byte-identical — no selection change.
- No un-mute anywhere (Fork-2): stopped streams are already skipped by the selector.
- R12: `_foreground`/workspace untouched — the pre-roll moves no pointer.
- **Concurrency-guard obligation:** this IS a speak-loop path change → the keep-going-
  with-spearcon scenario **joins the guard hammer set**; `tests/test_concurrency_guards.py`
  green at every commit, per the campaign constraint.

**Code touchpoints.** `src/sonari/daemon/host.py:485-513` (the keep-going branch of
`_speak_loop_once`), `:279-286` (`_spearcon_path` — never blocks, safe under the lock:
verified, it is a cache-path stat + non-blocking Popen kick, `spearcon.py:76-83`),
`:288-307` (`_attributed_text` — unchanged; the spearcon's `names_session` uses the
existing suppression), `src/sonari/daemon/features/lifecycle.py:115-119` (spearcon
pregeneration at SESSION_START keeps the cache warm — unchanged, cited as why the miss
path is rare).

**Risk class.** The highest-care item of the wave: M1 + guards + Fork-2 + anchor 7 all
named above. It is last in the build order on purpose.

---

## 15 · Chord proposals — **OWNER GATE (his veto at the design review)**

Survey of the ACTUAL bound ⌃⌘ space (`keymap.py:54-60` + `platform/macos/hotkeys.py:
111-122`): `S M J D W ← → ↑ ↓ = - Return Escape Tab ⇧Tab`. Keys already present in
`src/sonari/platform/macos/keytables.py:4-21` but UNBOUND: `R L V O F P . ] [`. Digits are chooser-hold-internal
by ratified anchor (bare `⌃⌘digit` is deliberately inert — the flip-design is the owner's
sketched reopen; no wave item may squat there).

**Existing chords are never reassigned in this wave.** One new verb needs a binding
(item 10 chose the fold-in — no chord):

| Verb | Proposal | Mnemonic | Alternates |
|---|---|---|---|
| `repeat_last` (W12) | **⌃⌘R** | R = Repeat — first-letter, matches the S/M/J/D/W family convention | 1. ⌃⌘L ("Listen again / Last") — flagged: the SP4 synthesis floats ⌃⌘L for the SP5 catch-up chord (non-locked), collision risk; 2. ⌃⌘. — rejected-by-default: ⌘. is the system-wide cancel idiom, adjacent muscle memory |

The earcon ASSETS in W6/W7 (Basso / Blow / Purr) are likewise OWNER GATE — semantics
(the 3-class taxonomy, the expiry cue existing) are ENG; the specific sounds are his ear.
The `", {u} unheard"` wording (W10) is his third gate.

---

## 16 · Where the atlas's fix direction did not survive code verification

1. **W6 (tones):** "cheap via `afplay -v`" → new earcon kinds would be **silently
   disabled on every existing install** (`speaker.py:102-104` + `bootstrap.py:73-74`
   whole-key merge); adopted the `pitch()` package-fallback pattern; distinct assets over
   loudness variants (loudness ≠ reliable semantic channel).
2. **W9 (call-sign):** "wiring order, not new infra" → the legacy decision chimes are
   sessionless protocol messages (field addition needed) and earcons are overlapping
   fire-and-forget Popens (sequencer thread needed).
3. **W2 (E1):** atlas sketched `time.time()` + per-stream `last_activity` → ratified
   monotonic ⇒ `time.monotonic()` with an injectable clock; `last_activity` dropped from
   wave scope.
4. **W10 (depth):** the naive `len(unheard)` double-counts queued items (their history
   entries are unheard until spoken) → `u = max(0, unheard - k)`; count is a
   current-turn floor, honestly documented; frontier counts explicitly NOT built (SP5).
5. **W13 (pre-roll):** "give it the same pre-roll" cannot be an enqueue from outside —
   the mechanism must live inside the existing M1 locked block as a claimed synthesized
   item; joins the guard hammer set.
6. **W4 (⌃⌘D):** queue-only miss predicate would speak "No decision here." over an
   answerable-but-already-read permission → predicate extended to `_pending_decisions`.
7. **W8 (boot cue):** an enqueued cue would never voice (no speaker at boot; keep-going
   scans only registered sessions) → direct one-shot speaker thread.

---

## 17 · Global constraints (inherited from the campaign, binding on every wave item)

- **Keep the machinery, touch only the decision layer** — no rewrites of
  `session_stream.py`, `queue.py` mechanism, `ProseAssembler`, the pop+claim+speak+
  note_spoken core, `SessionHistory` storage (W2 EXTENDS, never replaces), dispatch/
  registry/server/Ctx (campaign :13).
- **`tests/test_concurrency_guards.py` green at every commit; speak-loop changes join
  the hammer set** (:14) — applies to W6 (failure-tone path), W12 (capture), W13
  (pre-roll).
- **TDD, spec as oracle** — every given/when/then above becomes a test; red → green →
  commit, bite-sized (:15).
- **macOS-only; Python 3 / `say` / `afplay` / the Swift hotkeyd; NO new runtime deps**
  (:16) — every mechanism above is stdlib + the two binaries.
- **Ratified 2026-06-29 decisions stay binding** (:17): Policy A untouched (W5 fixes the
  enum write UNDER it), global verbosity untouched (W3/W10 are cue/content), the seven
  anchors respected throughout.
- **Deploy is the owner's step** (`./bin/sonari install` from a real GUI Terminal); live
  audio feel is his ears — mechanical verification only from sessions (:18).
- **Wave-local additions:** suite stays green from the 987/1skip baseline; no frontier
  substrate, no persistence, no presence model (SP4/SP5/SP6 borders per-item above).

---

## 18 · Test-plan summary

| Wave | New/extended tests | Notes |
|---|---|---|
| W1 | extend `tests/test_daemon_focus_nav.py` | cue precedes a seeded backlog |
| W2 | extend `tests/test_history.py` | stamp present, non-decreasing, injectable clock |
| W3 | `tests/test_verbosity_confirm.py` | exact strings ×3, workspace routing, invalid silent |
| W4 | `tests/test_jump_decision_miss.py` | queue preserved byte-for-byte on miss; the three GIVEN branches, incl. the third re-speaking `_pending_decisions[...]["text"]` (not `st.options`); + REREAD_OPTIONS fallback to the same stored text (scope addition) |
| W5 | `tests/test_voice_state_submit.py` | lift on take-voice; NO lift on deny / stopped-all / muted-self-submit; ⌃⌘W says "playing" |
| W6 | `tests/test_failure_tones.py` | kind per call site; existing-config fallback never silent |
| W7 | `tests/test_permission_expiry.py` | timeout → earcon + queue cleanup; answered/superseded → neither; in-flight edge |
| W8 | `tests/test_boot_cue.py` | exact string, spoken once, non-blocking start |
| W9 | `tests/test_decision_callsign.py` | sequenced chime→spearcon; sessionless fallback; miss fallback |
| W10 | `tests/test_also_map_unheard.py` | quiet floor, `-k` no-double-count, non-quiet strings unchanged in the ordinary (non-preemption) case; preemption-cut case asserts no spurious unheard beyond genuinely-unheard preempted content — not byte-identity |
| W11 | `tests/test_pointer_collapse.py` + update the two foreground-pinned files | divergence cases; degenerate no-focus case byte-identical |
| W12 | `tests/test_repeat_last.py` + **hammer-set addition** in `test_concurrency_guards.py` | verbatim incl. prefix; idempotent; capture-park-resume; "Nothing to repeat." |
| W13 | `tests/test_keepgoing_preroll.py` + **hammer-set addition** | spearcon-claims-attribution; selection byte-identical; miss = today's splice; FLUSH mid-switch loses nothing |

Baseline 987/1skip stays green throughout; expected end state ≈ +55–70 tests, both
concurrency guards permanent and extended, never weakened.
