# Sonari session-chooser navigation — design (2026-07-14)

**Supersedes** the ⌃⌘Tab / ⌃⌘⇧Tab rows of the voice-arbitration spec §8 and SP3's
Fork-1 (workspace-anchored cycle). Everything else in
`2026-06-29-sonari-voice-arbitration-design.md` stands. Ratified in-chat with the
owner 2026-07-14 (design + the ⌃⌘0→double-⌃⌘W merge + "delete dead code cleanly").

## 1. Motivation

F-RETEST-1 (live-reproduced, `.superpowers/sdd/f-retest-1-rootcause.md`): the ring
anchored on `workspace()` pins whenever OS focus can't follow the voice (failed
raises, unraisable sessions, eyes-free no-click use). Owner rejected a
foreground()-anchor ("confusing if the cycle does not start from the window in
focus") and asked for a holistic navigation pattern. Result: one browsing gesture
that cannot pin by construction, plus absolute addressing, with the existing
attention jump and status keys unchanged in role.

## 2. The verb model

- **Browse + go (relative):** hold ⌃⌘, tap Tab — the CHOOSER (this spec).
- **Teleport (absolute):** digits while the chooser is held.
- **Where I'm needed:** ⌃⌘J — UNCHANGED (decisions first, oldest backlog).
- **Status:** ⌃⌘W once = ratified terse string; quick second press = numbered
  roster. The separate ⌃⌘0 roster key was considered and DROPPED (owner: same
  verb as W).

## 3. The chooser

**Entry:** ⌃⌘Tab keydown (chord held). **While held:** each Tab = step next;
⇧Tab = step prev; digit 1–9 = instant commit to that session number (unknown
number → error earcon, chooser stays open); **releasing ⌃ or ⌘ = commit** the
current candidate; safety timeout 30 s = cancel. Key-repeat on a held Tab gives
fast walking for free.

**Candidate list:** snapshot at open. Order: index 0 = the current session
(`workspace() or foreground()`), then other sessions most-recently-used first,
then never-visited sessions in registration order. Filter: `is_live()` only —
identical to the ring's W1 semantics (muted sessions stay browsable; dead-tty and
tty-evicted sessions are out). Stepping wraps past the end back to index 0.

**Previews** (each step): spoken "{number}, {folder}" ("{number}, another
session" when the folder is unknown), plus ", muted" when that stream is stopped.
Spearcons may replace the folder word where cached. Each preview barge-ins the
previous one and is DELIVERED exactly like a ⌃⌘W cue: enqueued to the speaker's
stream (or the playable-workspace fallback when the speaker is None) with
mute_exempt + pause_exempt + at_front. Previews move NOTHING — no
voice change, no workspace change, no raise. Index 0 is spoken
"{number}, {folder}, current".

**Commit** (release, or digit): exactly the ratified cycle-landing semantics —
`focus(target)`, `voice_state = "flowing"`, landing cue (names_session,
spearcon-capable), raise when the identity supports it; committing onto a MUTED
session keeps Fork-2: workspace lands, target stays muted, landing silent, voice
released to keep-going. Committing to index 0 (the current session) is a no-op
landing: no cut, no cue, interrupted speech resumes. A quick tap-and-release
therefore lands on index 1 — the previous session — giving the ⌘Tab toggle with
no special-casing.

**Interrupted speech:** on open, capture the in-flight item (the ⌃⌘W
capture-and-requeue pattern). Cancel or commit-to-current → re-queue it at the
front so it resumes. Commit-to-other → the cut matches today's cycle-cut
behavior (parity with `on_cycle_session`'s `speaker.cancel()`).

**Cancel** (timeout, hotkeyd death): restore the captured item, move nothing,
say nothing. The daemon treats a CHOOSER_* message arriving for a stale (>30 s)
open as a fresh open.

**Why it cannot pin:** browsing state lives in the daemon and is advanced by the
gesture itself; neither raises nor OS-focus movement participate until the single
commit. There is no anchor recomputation between taps.

## 4. Wire protocol + keymap

New MsgTypes (hotkeyd → daemon): `CHOOSER_STEP {"direction":"next"|"prev"}`
(the first step opens), `CHOOSER_DIGIT {"digit":1-9}`, `CHOOSER_COMMIT`,
`CHOOSER_CANCEL`. **DELETED:** `CYCLE_SESSION`, keymap actions
`cycle_session_next` / `cycle_session_prev`, and `on_cycle_session` — the owner
mandated a clean dead-code sweep: protocol entry, handler, keymap rows, hotkeyd
send path, and superseded tests all go (behavioral tests MIGRATE to the chooser
equivalents — the W1/eviction/muted-landing semantics keep coverage through the
new path).

## 5. hotkeyd (Swift)

Chooser-mode FSM. On ⌃⌘Tab keydown: enter mode, send the step, dynamically
`RegisterEventHotKey` ⌃⌘1–9 (digits are registered ONLY while the chord is held —
they must never shadow other apps otherwise), start a ~40 ms release-poll timer
reading the modifier state (`NSEvent.modifierFlags` / `CGEventSource.flagsState` —
verified 2026-07-14 to be permission-free, no event tap, no new TCC). Further
⌃⌘Tab/⇧Tab → steps; digit → CHOOSER_DIGIT, exit mode; ⌃ or ⌘ observed released →
CHOOSER_COMMIT, exit mode; 30 s cap → CHOOSER_CANCEL, exit mode. Exit always:
unregister digits, stop the poll timer. The existing 0.5 s focus poller is
untouched.

## 6. Session numbers

`SessionManager` assigns each registered session the lowest free number ≥ 1 at
registration; the number is stable for the session's lifetime and freed on
unregister. Numbers are SPOKEN in: the chooser previews, the roster, the ⌃⌘W
voice/keyboard clauses ("Voice: bravo two, Playing. Keyboard: alpha one. …"),
and a registration announce ("{folder}, {number}.", suppressed at verbosity
quiet). Numbers are NOT injected into content attribution prefixes or jump cues
(noise). Numbers > 9 are spoken but unreachable by digit teleport (accepted
edge; realistic fleet ≤ 5).

## 7. ⌃⌘W double-press roster

A second `WHERE_AM_I` arriving within 2.0 s of the previous one escalates to the
roster instead of repeating the summary: sessions in NUMBER order,
"{number}, {folder}[, muted][, {k} waiting]." per session, waiting = that
stream's queue length when > 0. Same delivery flags as the summary
(speaker-stream, mute/pause-exempt, at_front, barge-in + resume). Detection is
daemon-side — no new binding.

## 8. Recency (MRU)

A recency list in `SessionManager`, updated by deliberate acts only:
`set_foreground` / `focus` (submit, jump, chooser commit) and a matched
`set_os_focus` (a click counts as "you were there"). Keep-going's
`set_speaker` NEVER updates it (voice drift is not presence — R12 discipline).
In-memory, like the roster.

## 9. Invariants preserved

R12 (`_foreground` writers unchanged — chooser commit uses `focus()`); M1
speak-loop atomicity untouched (previews/commits run in the handler transaction);
Fork-2 muted-in-ring; W1 liveness filtering + sp3.2 eviction (chooser list uses
`is_live`); the concurrency guards stay green and the hammer set gains the
chooser messages.

## 10. Testing

Unit: numbering (lowest-free, stable, holes, >9), MRU update rules (incl. NOT on
set_speaker), chooser open/step/wrap/digit/commit/cancel/stale, muted-commit
keep-go parity, eviction+dead-tty filtering through the chooser, W double-press
window (1.9 s yes / 2.1 s no), interrupted-item capture/resume, verbosity-gated
announce. hotkeyd: builds via the existing swiftc path; resolved keymap contains
the new actions and NOT the cycle ones. Live checklist for the owner's ear pass
ships with the build report.

## 11. Out of scope

The intermittent raise failures (10/63 focus events, separate follow-up) — they
now affect only whether a committed window comes forward, never browsing. The
SP4+ campaign items are unchanged. F-RETEST-1 is CLOSED by this design.
