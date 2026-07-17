# ⌃⌘W readout grammar v2 — owner-delegated redesign

**Status:** owner delegated the design decision in full ("fix it yourself, UX perfectionist mandate", 2026-07-16, post cheap-wave-1 install). This doc AMENDS the cheap-wave-1 design spec §7/W10/W11 readout strings; everything not named here is unchanged. Evidence: design workflow wf_294139bb-1dc (3 adversarially-attacked candidates + duration measurements at the owner's real 225 wpm; verdicts in `.superpowers/sdd/progress.md`).

## Problem (owner-reported, install day)

1. Number-role blur at high wpm: dial digits, counts and (proposed) age numbers stream past with only comma/semicolon boundaries, which are inaudible at rate.
2. Unified pointers are signaled by ABSENCE (no Keyboard clause) — the owner rejected absence-as-signal: "if they are at the same session say so".
3. Wanted per-session output magnitude and muted state (already shipped) — confirmed kept.

## Design findings that bind this grammar

- **Digit-first entry shape is the only never-adjacent-numbers form.** `{n} {folder}, {k} waiting, {u} unheard` = digit·name·count·noun: two numbers never abut, within or across entries. Both folder-first candidates were caught by their attack agents recreating the blur at entry boundaries. → the ratified digit-first Also shape is KEPT (zero retraining).
- **Role rule (teachable, position + adjacent word, redundant):** a number right before a name is a dial digit; a number right after a name/clause is a count. Fixed clause order is the third redundant cue (SBAR/ATC readback pattern).
- **A standalone "Decision:" landmark is a single clippable point of failure** (both attacks, independently): if the sentence-initial frame soft-onsets away at rate, the highest-stakes fact silently reads as a routine pile entry. → "decision" is an INLINE role word on the session's own entry, and decision entries sort FIRST (survives impatient barge-in).
- **Counts stay numeric.** The owner explicitly asked for magnitude. The airtight alternative ("session {n}" frame before every digit) measured +52% duration at 225 wpm — rejected.
- **Sentence boundaries are the only rate-proof prosody.** Kokoro ignores say prosody tags; periods give a real pause + pitch drop at every tested rate. → every Also entry is its own sentence.

## The grammar

Pointer lead:

- Unified (workspace == speaker): `"Voice and keyboard: {folder} {n}, {state}."`
- Diverged: `"Voice: {folder} {n}, {state}. Keyboard: {folder} {m}[, pile clauses]."` — the Keyboard clause carries its OWN `muted/waiting/unheard/stale` clauses (a silent pile at the typing position is the one pile worth pointer-line space).
- Idle voice: `"Nothing playing." | "On hold." | "All stopped."` + `" Keyboard: {folder} {n}[, clauses]."` (positive location even when idle; the clause carries the workspace's own pile exactly like the diverged Keyboard clause) + Also-map excluding the workspace (a session is never named twice).
- A blocking decision on a pointer session appends `, decision` to that pointer clause (the session is never repeated in the Also-map).
- `{state}` vocabulary unchanged: playing / stopped / on hold / all stopped.

Also-map (` Also: …` landmark KEPT):

- Entry shape unchanged: `"{n} {folder}[, decision][, muted][, {k} waiting][, {u} unheard][, stale]"`.
- `decision` = the ⌃⌘D hit predicate verbatim (queued `has_decision()` OR live `_pending_decisions` entry).
- `stale` = oldest unheard entry older than `STALE_AFTER_S = 900` (15 min; module constant, owner-tunable; consumes `HistoryEntry.stamp` — the W2 scope fence is hereby lifted, this is its intended first consumer). Age is ALWAYS a word, never a number.
- **Order = value tiers, number-ascending within each:** decision entries; then pile entries (waiting/unheard); then muted-only entries; then the quiet tail. (Amends the ratified "in NUMBER order" — delegated decision; each entry still self-labels its digit, and the chooser roster remains the complete ordered dial-pad.)
- **Entries are sentences**, joined `". "` — not `"; "`.
- **Quiet collapse:** sessions with nothing to report fold into a trailing `"Plus {word} quiet."` (count as a word, digit-free, terminal position; above the one-to-nine word map it degrades to "many" — never a numeral). One quiet → "Plus one quiet." When ALL other sessions are quiet: `"All quiet."` replaces the Also-map entirely. Zero other sessions: no Also clause (unchanged trained absence).

## Worked examples (test oracle, byte-exact)

Roster: 1 board (2 unheard) · 2 jam (voice+keyboard, playing) · 3 hackimi (5 unheard, oldest 25 min) · 4 docs (quiet) · 5 edrum (muted) · 6 syncward (decision + 3 unheard).

- Unified: `"Voice and keyboard: jam 2, playing. Also: 6 syncward, decision, 3 unheard. 1 board, 2 unheard. 3 hackimi, 5 unheard, stale. 5 edrum, muted. Plus one quiet."`
- Diverged (keyboard on hackimi): `"Voice: jam 2, playing. Keyboard: hackimi 3, 5 unheard, stale. Also: 6 syncward, decision, 3 unheard. 1 board, 2 unheard. 5 edrum, muted. Plus one quiet."`
- All-clear: `"Voice and keyboard: board 1, playing. All quiet."`
- Idle: `"Nothing playing. Keyboard: docs 4. Also: …"` (workspace excluded from the map)

## Accepted edges (documented, no code)

- A folder literally named like a role word ("unheard", "muted"…) stays positionally parseable (first token after the digit is always the folder); no guard built — the owner controls his folder names.
- Session numbers >9 are spoken as-is (multi-digit identifiers; the ≤9 dial keys are the common case; folder-only fallback rejected as YAGNI).
- `u` keeps the ratified floor shape (`max(0, unheard−k)`), but **SP5 changed its SOURCE** to the
  transcript pile (`unheard_from_frontier`, cross-turn, frontier-keyed) instead of the current-turn
  `history.unheard()` — the grammar (the waiting/unheard split, the `unheard` word) is unchanged, only
  the number's provenance moved (`2026-07-17-sonari-sp5-catchup-design.md` §8). `stale` still reads the
  current-turn `unheard_age` as an approximation of pile age — the documented minor inconsistency from
  Task 9 (not reconciled by SP5).

## Unchanged / re-affirmed

Fork-2, R12, one-utterance delivery (mute_exempt + pause_exempt + at_front + barge-in capture/park), the "unheard" word (owner gate stands), `{k} waiting` before `{u} unheard`, Also excludes pointer sessions, the error-earcon branch for unplayable-idle. No new substrate; readers only (`has_decision`, `_pending_decisions`, `stamp`, `stopped`, queue length).
