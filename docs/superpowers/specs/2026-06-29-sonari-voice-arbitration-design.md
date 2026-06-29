# Sonari — Voice Arbitration: Behavioral Spec (Pass 1 — DEFINE)

- **Date:** 2026-06-29
- **Status:** Design — Pass 1 (DEFINE) approved in collaborative brainstorm; **code-closed**. Awaiting
  Pass 2 (RECONCILE against the code) before implementation planning.
- **Scope owner:** Nima
- **Audience:** Sonari's users are blind / eyes-free power users — designed for them, not around any
  one person's setup. The behavior below was co-designed with Nima as the lived-experience source.
- **Layer:** Track B — *voice arbitration*: which session owns the voice, what is heard, and when.
  This is the WHAT. Mechanisms ("how the code does it") are deferred to Pass 2.

## 1. Why this spec exists

The "which session do I hear, and when" logic was never specified. It accreted as features plus
bug-patches, so the failures (a background task cutting your live answer; a fresh session staying
silent; jump not raising a window after a restart) were all symptoms of one implicit, tangled
decision layer. This spec defines the **ideal behavior from the eyes-free user's goals**, independent
of today's code, so Pass 2 has a test oracle to rebuild that layer against.

This is **not** a daemon rewrite. The machinery (per-session streams, the speech queue, the prose
assembler, the speak loop, cancel-epoch/barge-in, history) is kept. Only the decision layer is
redefined.

### 1.1 Out of scope (explicitly)

- **Track A — audio infrastructure** (can the daemon get audio out of its launchd context). Separate,
  and already working. A logic rebuild will not fix audio infra.
- **Exact hotkey assignments.** Bindings named below (`⌃⌘S`, `⌃⌘M`, `⌃⌘Tab`, `⌃⌘J`, `⌃⌘D`, `⌃⌘W`,
  `⌃⌘⏎`, `⌃⌘⎋`) are illustrative anchors from the cockpit grammar; this spec governs *behavior*, not
  which chord triggers it.
- **TTS rendering** (voice, rate, spearcon generation, earcon sounds). Owned elsewhere.

## 2. The user and the core insight

The user runs **multiple sessions, many of them autonomous, and actively jumps between them while
they work** — a pilot monitoring instruments, not a passive listener waiting to be told things.

Three instincts drove every decision and resolve every conflict below. When in doubt, decide by
these:

1. **Completeness.** Audio is the user's only channel, so *skipped output is lost output*. Nothing is
   silently dropped. The user would rather hear it all and prune it by hand than have the system
   decide what to discard.
2. **Keep-going.** When a session falls quiet on its own, the voice should flow to the next thing on
   its own — the user should not have to switch the sound by hand to stay informed.
3. **Manual control over system curation.** The user pulls detail (identity, backlog) by navigating;
   the system does not reorder, summarize, or minimize on the user's behalf. A *deliberate* stop is a
   lasting quiet the user controls.

And one hard rule that overrides convenience: **never cut the user's live readout.**

## 3. Vocabulary — the pieces kept separate

The old tangle conflated three things. They are independent here:

- **Voice** — the single audio output channel. At most one session is read aloud at a time.
- **Speaker** — the session the voice is reading *right now* (what you hear). ≤1 at a time.
- **Workspace** — the session that currently holds the **front GUI window + keyboard focus** (where
  typing lands, where the magnifier looks).

Plus the data model that guarantees completeness:

- **Transcript** — each session's ordered, append-only record of spoken-eligible output.
- **Marker** — a per-session pointer: *"you have heard up to here."* The voice reads **forward from a
  session's marker**. **A marker never advances over unheard output.** This single rule is what makes
  "nothing is skipped" mean something testable.

And the audio tiers:

- **Readout** — full spoken content; exclusive (one at a time); the speaker's transcript read aloud
  from its marker forward.
- **Ding** — a brief, generic, non-speech earcon for ordinary output from a *non-speaking* session.
  May overlay the current readout; **never pauses or cuts it**.
- **Decision cue** — a distinct (audibly different from the ding) non-speech earcon: a session is
  blocked on a permission/decision prompt.
- **Speaker-change announcement** — the new speaker's identity (spearcon), spoken when the voice
  moves from one session to a *different* one.

## 4. The invariant (one sentence)

> There is one voice. The system flows it to you **completely and in order**, and **keeps it going**
> on its own when a session falls quiet — but it **never cuts what you're hearing** and **never moves
> your screen** on its own. You **drive the voice deliberately** — submit, jump, and cycle each
> preempt what's playing — while of the output that **arrives on its own**, only a prompt your **hands
> typed** cuts in (autonomous progress never does). When you **deliberately stop**, it stays quiet
> until you bring it back. You **always know who's speaking**, you can **never answer the wrong session**, and a
> **restart announces itself and loses nothing**.

Everything in §5 is a corollary of this one sentence.

## 5. The rules (each with an observable outcome)

Every rule states a behavior and a decidable outcome, so Pass 2 can turn it into a test.

### R1 — One voice; everyone else dings and accrues
At any instant ≤1 session is the speaker. Any other session producing output: a **ding** plays (may
overlay), and that output **appends to its transcript** (behind its marker).
> **Observable:** while A is speaker, B emits output → exactly one readout audible (A's); one ding
> plays; B's transcript grows; B's marker is unchanged.

### R2 — The speaker is never auto-cut
A new arrival — any session finishing or producing output, including an autonomous continuation —
**never interrupts the current readout**. It dings and accrues (R1).
> **Observable:** A is mid-readout; B finishes → A's audio plays to its natural end, untruncated; B's
> ding plays.

### R3 — Completeness: the marker never skips
The voice reads a session's transcript **forward from its marker, in order, omitting nothing**. A
marker advances only across content actually read aloud.
> **Observable:** B's unheard transcript is `[t1, t2, t3]`; when the voice reads B it emits `t1`, then
> `t2`, then `t3`; it never emits `t3` before `t1`; B's marker reaches `t3` only after all three are
> read.

### R4 — Keep-going on natural idle (ambient); the window stays put
When the speaker reaches its marker's live edge **and is idle** (e.g. its subagent is working with no
new spoken output), the voice **automatically becomes the speaker of another session that has unheard
output**, reading from *that* session's marker. **The workspace does not change** — only the sound
travels.
> **Observable:** A is idle at its live edge; B has unheard output → the voice begins B's readout
> (speaker = B); the front window and keyboard focus are unchanged from before.

- **Default (vetoable):** when several sessions have unheard output, the voice finishes the current
  session to its live edge, then picks the session whose **oldest unheard output is oldest**
  (longest-waiting first), so nothing starves.

### R5 — You override the flow; the window follows *your* action
Three deliberate user actions change the speaker on demand, and **each makes that session the
workspace** (front window raises + keyboard focus moves):

| Action | Speaker | Workspace (window + keyboard) |
|---|---|---|
| **Submit** (type a prompt + send) | → that session | already there (you typed in it) |
| **Jump / "go there"** (`⌃⌘J`, `⌃⌘D`) | → that session | → that session (window raises) |
| **Cycle / audition** (`⌃⌘Tab`) | → that session | → that session (window raises) |

A deliberate speaker change **cuts the current readout immediately**; the cut content is **not lost**
(the marker did not advance past it — you can re-hear from the marker).
> **Observable:** A is speaker; you jump to C → A's readout stops at once; C becomes speaker (reads
> from C's marker); C's window is frontmost; keyboard focus is on C.

- **Default (vetoable):** deliberate moves cut immediately (rather than finishing the current
  sentence first).

### R6 — Autonomous ≠ deliberate (the preempt discriminator)
Output can begin two ways, and they behave differently:

- **You drove it** — your hands typed a prompt and sent it. A direct request → it **takes the voice
  immediately** (R5-submit), cutting whatever was playing.
- **It drove itself** — a session continuing on its own: a multi-step task, a subagent, an autonomous
  loop submitting its *own* next prompt with no keystroke from you. **Never seizes the voice** — it
  dings and accrues (R1/R2), and is heard later via keep-going (R4) or navigation.

Autonomous output is **still heard** (completeness) — it just **waits its turn** instead of **cutting
in**. Only *your typed* prompt cuts in.

*This rule exists to kill the worst old failure: every prompt-submit — including a background agent's
own autonomous continuation — counted as "the user just asked something," so it grabbed the voice and
cut the live answer.*
> **Observable:** A is speaker; B (autonomous) auto-submits its continuation and emits output → A
> continues; B dings; speaker stays A. Contrast: you type+send a prompt to B → B becomes speaker
> immediately, A cut.
> **Pass-2:** the mechanism to tell a human-submitted prompt from an autonomous one (via hook data).

### R7 — Stop = lasting deliberate quiet; workspace untouched
**Stop-the-speaker** (`⌃⌘S`): the stopped session is **muted** (its marker freezes at the stop point;
on restart it resumes reading from there) **and** the voice enters a **quiet hold** — subsequent
output only **dings** (no readout, no keep-going) until you **re-engage** (submit / jump / cycle).
Stopping does **not** move the workspace.

The quiet hold is **discoverable, never a silent surprise**: dings continue, and where-am-i (`⌃⌘W`)
reports that the voice is stopped.

Re-engaging (cycle / jump / submit to any session) **lifts the quiet hold** and ambient flow resumes
for non-muted sessions. The specifically stopped session **stays muted** until you start *it* again,
at which point it resumes from its frozen marker. Navigating *to* a still-muted session raises its
window (it becomes the workspace) but it **stays silent** until you start it (`⌃⌘S`) — navigation
never un-mutes; only an explicit start does.

**Stop-all** (`⌃⌘M`): every session muted, voice silent, quiet hold; re-engage to resume.
> **Observable:** A is speaker, B waiting; you stop A → A silent; no readout begins for B; voice stays
> silent; `⌃⌘W` reports "stopped." C later emits output → only a ding. You cycle to C → hold lifts; C
> becomes speaker + workspace; A stays muted until you start A again (then resumes from its marker).

### R8 — You always know who's speaking
When the speaker changes to a *different* session, the new speaker's **identity (spearcon) is
announced** at the start of its readout. Continuous output from the **same** session is **not**
re-announced. Ordinary non-speaking output = generic **ding**; a blocked decision = distinct
**decision cue**.
> **Observable:** voice moves A→B → B's identity announced, then B's content; voice continues B→B →
> no re-announcement.

### R9 — Permission prompts: distinct cue, no jump, no cut
When a session becomes **blocked on a permission/decision prompt**, a **distinct decision cue** plays
immediately (awareness). The prompt's readout takes its **normal place by marker order** (R3) — it
**does not cut** the current readout and **does not jump ahead** of other queued output. The user
reaches it on demand via **jump-to-decision** (`⌃⌘D`), which is a go-there (R5): it becomes speaker +
workspace.
> **Observable:** A mid-readout; B blocks on a prompt → distinct decision cue plays; A continues; B's
> prompt is read only when the voice reaches B in normal order — *or* immediately if you press `⌃⌘D`
> (which makes B speaker + workspace).

### R10 — You can never answer the wrong session
The answer keys (`⌃⌘⏎` allow / `⌃⌘⎋` deny) act on the **pending decision of the session you have gone
to** (the workspace). If that session has no pending decision, an **error tone** plays; the keypress
**never silently answers a different session**.
> **Observable:** workspace = B with a pending decision; `⌃⌘⏎` → B's decision answered allow.
> Workspace = B with no pending decision; `⌃⌘⏎` → error tone; no other session's decision changes.
> **Pass-2:** exact targeting (workspace vs speaker coupling) and fail-closed details.

### R11 — Restart announces itself and loses nothing
On daemon restart: a brief **"Sonari restarted" cue** plays; **all sessions, transcripts, markers, and
identities survive** (cycle / jump / raise keep working with no re-prompting); the readout interrupted
by the restart **does not auto-resume**; new output flows per the normal rules; the user can re-hear
from a session's marker on demand.
> **Observable:** A is mid-readout when the daemon restarts → "Sonari restarted" cue; afterward A
> exists with its transcript + marker; `⌃⌘J` to A raises A's window (identity survived); A's
> interrupted readout does not auto-start; new output dings/reads per the rules.
> **Pass-2:** the persistence mechanism.

### R12 — The window rule (cross-cutting)
The **workspace** (front window + keyboard focus + magnifier) changes on **exactly the three
deliberate user actions** (submit, jump, cycle), each making the targeted session the workspace. It
**never changes on its own** — not on ambient keep-going (R4), not on a background completion, not on
a ding or decision cue, not on the voice changing speaker by itself. Speaker and workspace **re-sync
the instant the user acts**.
> **Observable:** during ambient flow the speaker moves A→B→C while the front window stays on whatever
> the user last navigated to; the user cycles → the front window jumps to the cycled session.

## 6. User stories (the seven scenarios, as outcomes)

1. **Submit and listen.** You type a prompt to A and send it → A is speaker + workspace; you hear A's
   reply in full from its marker. *(R5, R3)*
2. **A background session finishes mid-reply.** You're hearing A; B finishes → A plays on
   uninterrupted; a ding tells you something landed; B's result is heard later, in full, when the
   voice reaches it. *(R2, R1, R3, R4)*
3. **Juggling; one is talking and another produces output.** You hear A; B (autonomous) emits "task 1,
   2, 3" → dings, no cut; when A goes idle the voice rolls to B and reads task 1 → 2 → 3 in order,
   without moving your window. *(R4, R3, R6, R12)*
4. **Stop one / stop all / resume.** Stop A → silence, B held, dings continue, `⌃⌘W` says "stopped";
   re-engage to resume ambient; start A again → A resumes from its marker. Stop-all → everything
   muted until you re-engage. *(R7)*
5. **Jump to a waiting session / cycle between sessions.** Jump or cycle → that session becomes
   speaker **and** its window raises + takes the keyboard; you hear it from its marker (full history,
   not just the live edge). *(R5, R3, R12)*
6. **Daemon restarts mid-reply.** "Sonari restarted" cue; sessions + identities survive; the cut
   readout does not auto-resume; navigation still raises windows. *(R11)*
7. **Always know who's talking.** Every speaker-change announces the new session; ordinary output
   dings; a blocked decision gives a distinct cue. *(R8, R9)*

## 7. Decisions log (every fork, and what was chosen)

Recorded so Pass 2 does not re-litigate, and so a reversal sweeps every dependent rule.

| # | Fork | Chosen | Why |
|---|---|---|---|
| D1 | Background finishes mid-reply: keep reply + signal / keep reply silent / cut in | **Keep reply + a quick signal** | Never cut the live answer, but stay aware. |
| D2 | The signal: name the session / generic sound / name-on-demand | **Generic ding** (overlays, no pause) | Sessions are autonomous and the user navigates between them, so identity is *pulled*, not pushed. |
| D3 | When the voice is free and sessions produce output: only the attended one / auto-read ambient / pull only | **Auto-read ambient, one at a time** | Keep-going; hands-free awareness. |
| D4 | Stop the speaker while another waits: advance to the queue / go fully quiet | **Go fully quiet** | A deliberate stop means silence, not a hand-off. |
| D5 | Cycle by ear: window stays / raises on every landing | *(superseded by D9)* | — |
| D6 | Restart mid-reply: cue + keep sessions / seamless resume / silent recovery | **Cue + keep sessions** | A silent gap reads as "broken"; auto-resume after a gap is disorienting. |
| D7 | Landing on a piled-up session: live edge / catch-up + latest / everything from where you left off | **Everything from where you left off** | Completeness — audio is the only channel; the user prunes with stop/rate/nav. |
| D8 | After a deliberate stop, does brand-new output speak: revive ambient / stay quiet until re-engage | **Stay quiet until re-engage** | Stop is a *lasting* quiet the user controls (discoverable via dings + `⌃⌘W`). |
| D9 | Window ↔ speaker coupling | **Coupled on every user action** (submit, jump, **and cycle**) | The user values hear = see; accepts viewport movement on cycle. Upgrades D5. |
| D10 | Does the system's ambient auto-move also raise the window | **No** | Protects keyboard focus — auto-moving the window would land keystrokes in the wrong session. |
| D11 | Permission prompt mid-reply: cue + jump the line / cue + wait its turn / ordinary ding | **Distinct cue, waits its turn** | Awareness without the system reordering; the user controls timing via `⌃⌘D`. |

## 8. Vetoable defaults (chosen by inference, easy to flip in Pass 2)

- **R4 cross-session order:** finish current session to its live edge, then take the longest-waiting
  session's backlog.
- **R5 cut timing:** deliberate moves cut the current readout immediately (vs. finishing the
  sentence).
- **R8 announce trigger:** announce identity on speaker-change only (not every chunk).

## 9. Parked for Pass 2 (need the code)

- **R6 discriminator** — how Sonari tells a human-typed prompt from an autonomous continuation.
- **R11 persistence** — how transcripts, markers, and session identities survive a restart.
- **R10 targeting** — the exact answer-key target (workspace vs speaker) and the fail-closed path.
- **R4 scheduling** — the precise cross-session "what plays next" policy (confirm the §8 default).
- **Marker mechanics** — whether the marker is per-session only (assumed) and how "live edge / idle"
  is detected for keep-going (R4).

## 10. What Pass 2 does next

Read the reconciliation reference, the cockpit-grammar bug map, and the code. For each rule above:
does the code satisfy it, diverge, or lack it? Map the gap, decide **keep vs rebuild**, estimate cost,
then plan and build with this spec as the test oracle. Keep the two permanent concurrency guards
green.
