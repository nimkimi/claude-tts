# Sonari — Voice & Session Behavior: Complete Behavioral Spec (Pass 1 — DEFINE)

- **Date:** 2026-06-29
- **Status:** Design — Pass 1 (DEFINE) approved in collaborative brainstorm; **code-closed**. The
  complete behavioral definition — model + state machine + queue contract + control surface. Awaiting
  Pass 2 (RECONCILE against the code) before implementation planning.
- **Supersedes:** the 2026-06-26 cockpit-grammar spec — this is now the **single behavioral source of
  truth** for voice + session control (§8 reconciles every binding; the grammar becomes historical).
- **Scope owner:** Nima
- **Audience:** Sonari's users are blind / eyes-free power users — designed for them, not around any
  one person's setup. The behavior below was co-designed with Nima as the lived-experience source.
- **Layer:** Track B — *voice & session behavior*: which session owns the voice, what's heard and
  when — plus the state machine, queue contract, and control surface that realize it. This is the
  WHAT. Mechanisms ("how the code does it") are deferred to Pass 2.

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
- **Re-tuning specific chord choices** (which exact key) is Nima's to adjust later — but the
  *required controls and their behavior* ARE in scope, specified in §8, which supersedes the cockpit
  grammar's bindings.
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

## 6. State model (the checkable state machine)

Three independent pointers (speaker · workspace · the per-session marker) plus two layered state sets.
Pass 2 holds the implementation to these states and transitions.

**Per-session state** (each session is in exactly one):
- **idle** — at its marker's live edge; no unheard output; not producing spoken output (a subagent may
  be working silently).
- **producing** — generating output that appends to its transcript (may also be the speaker).
- **queued** — has unheard output behind its marker, waiting for the voice.
- **speaking** — currently the **speaker**: the voice is reading its transcript forward from its marker.
- **muted** — user-stopped (⌃⌘S, or all-muted by ⌃⌘M); marker frozen; will not speak (even when
  navigated to) until an explicit start (⌃⌘S).

Plus an orthogonal flag, **blocked-on-decision** — waiting on a permission answer (its distinct cue
has fired). A session can be `queued` *and* `blocked` at once.

**The voice (global) state** (exactly one):
- **flowing** — default: a speaker is reading, or the voice auto-advances to the next queued session on
  natural idle (keep-going, R4).
- **quiet-hold** — entered by stopping the speaker (⌃⌘S): no auto-advance, new output only dings;
  **lifts on re-engage** (submit / jump / cycle).
- **stopped-all** — entered by ⌃⌘M: every session muted, voice silent; lifts on re-engage, but each
  session stays muted until individually started, so re-engaging lands you on a silent session until
  ⌃⌘S.

**The workspace pointer** — the session with the front window + keyboard. Independent of the speaker;
**moves only on submit / jump / cycle** (R12), never on its own.

**Key transitions** (the ones Pass 2 must get right):

| From | Trigger | To |
|---|---|---|
| speaker `speaking`, others `queued` | speaker hits live edge + idle | next queued session → `speaking` (R4 order); old → `idle`; voice stays **flowing** |
| any | you submit a typed prompt to X | X → `speaking` (cut current), workspace = X, voice **flowing** |
| any | you jump / cycle to X | X → `speaking` (cut current, read from X's marker), workspace = X |
| non-speaker | autonomous output (R6) | that session → `queued` + ding; speaker unchanged |
| speaker `speaking` | you ⌃⌘S | speaker → `muted` (marker frozen); voice → **quiet-hold** |
| `muted` session | you ⌃⌘S (start) | resumes from frozen marker; counts as re-engage → voice **flowing** |
| voice **quiet-hold** | you submit / jump / cycle | voice → **flowing** (navigated / non-muted session speaks; landing on a *muted* session → open edge, §12) |
| any | you ⌃⌘M | all sessions → `muted`; voice → **stopped-all** |
| `producing` session | hits a permission prompt | set `blocked-on-decision` + distinct cue; stays in marker order (no preempt, R9) |
| `blocked` session is the workspace | you ⌃⌘⏎ / ⌃⌘⎋ | decision answered; flag cleared (wrong target → error tone, R10) |
| any | daemon restart | sessions/markers/identities persist; "restarted" cue; voice → **flowing** (idle); interrupted readout does **not** auto-resume (R11) |

## 7. Queue & ordering contract

"The queue" is not a separate structure — it is **the set of sessions with unheard output** (content
past their markers). This is the contract Pass 2 checks the queue implementation against.

- **Within a session: strict FIFO.** The transcript is append-only and read in order; the marker
  advances only across content read aloud (R3). A session's own output is never reordered or skipped.
- **Across sessions (what speaks next when the voice frees on natural idle):** default = finish the
  current session to its live edge, then take the session whose **oldest unheard output is oldest**
  (longest-waiting first), so nothing starves. *(Vetoable default — §11.)*
- **Preemption:** only a deliberate user action (submit / jump / cycle) preempts the current readout.
  **Autonomous output never preempts** (R2, R6) — it appends, dings, and waits.
- **Barge-in (carried from the cockpit grammar, still required):** a hotkey that *speaks* (⌃⌘W, a
  jump / where-am-I announcement) cuts the current utterance, speaks at the front, then the interrupted
  item **re-queues at the front** and resumes from that item's start. **Rate (⌃⌘+/−) does not cut** —
  immediacy is its feedback.
- **Dings are not queued.** A non-speaker's ordinary output emits a fire-and-forget generic ding (may
  overlay the readout); a blocked decision emits the distinct decision cue. Neither enters the readout
  stream — they only *announce*.
- **Identity:** on a speaker change to a *different* session, announce its spearcon before the readout
  (R8).
- **Stop / restart never drop queued content.** ⌃⌘S freezes a marker and holds the voice (quiet-hold);
  ⌃⌘M mutes all and holds (stopped-all); restart preserves every transcript + marker. Queued output
  survives all three and is heard later (completeness).

## 8. Control surface (behavior → control, reconciled with the cockpit grammar)

Supersedes the 2026-06-26 cockpit-grammar spec where they differ. Every behavior the model requires
maps to a control; every existing control is checked for whether a behavior still needs it. **Chords
are the settled cockpit bindings;** Pass 2 verifies coverage and prunes / adds against this table.

**Fidelity caveat (code-closed):** this table is reconciled against the *documented* grammar, not the
live `keymap.py` / resolved keymap — design docs drift from code. So the verdicts are **provisional**;
Pass 2's first job here is to confirm them against the real bindings before trusting the coverage check.

**Legend:** **KEEP** (works as-is) · **CHANGE** (binding stays, behavior shifts under the new model) ·
**ADD** (newly required) · **CUT** (no behavior needs it).

| Behavior (rule) | Control | Verdict | Note |
|---|---|---|---|
| Stop / start the focused session (R7) | ⌃⌘S | **CHANGE** | Now also puts the **voice into quiet-hold** (no auto-advance) until re-engage — not just per-session silence. Resume-from-marker stays. |
| Stop everything (R7) | ⌃⌘M | **KEEP** | All muted, voice stopped-all; re-engage + per-session ⌃⌘S to return. |
| Jump to a waiting session (R5, R11, R12) | ⌃⌘J | **CHANGE** | Now reliably **raises the window + keyboard** (workspace follows) and survives a restart (identity persists) — fixes FOCUS-1. |
| Cycle next / prev session (R5, R12) | ⌃⌘Tab / ⌃⌘⇧Tab | **CHANGE** | Now **raises the window** on each landing (was voice-only). Speaker + workspace move together. |
| Submit a typed prompt (R5, R6) | *(type + enter)* | **KEEP** | A **human-typed** submit preempts (R5); an autonomous session's own submit does **not** (R6) — it dings + queues. Workspace already there. |
| Within-response nav / hear-again (R3) | ⌃⌘← / → | **KEEP** | Moves within the transcript; ← re-reads (marker-aware). |
| Between-response nav (R3) | ⌃⌘↑ / ↓ | **KEEP** | ⌃⌘↓ to newest = live edge. |
| Go to the waiting decision (R9) | ⌃⌘D | **KEEP** | How you reach a permission prompt that (by R9) waits its turn; it's a go-there (raises). |
| Approve / deny a permission (R10) | ⌃⌘⏎ / ⌃⌘⎋ | **KEEP** | Targets the workspace's pending decision; wrong target → error tone (fail-closed). |
| Where am I (R7, R8) | ⌃⌘W | **CHANGE** | Now also reports the **voice state** (flowing / quiet-hold / stopped-all) so a deliberate stop is never a silent surprise. |
| Speed up / slow down (R4 / D7) | ⌃⌘+ / − | **KEEP** | No-cut; also the tool to prune an auto-flood. |
| Settings (verbosity / voice / minqueue / keymap) | slash commands | **KEEP** | Orthogonal to arbitration. |

**Coverage check:** every rule with a user action has a binding above; the automatic rules (R1–R4
ambient, R8 identity, R11 restart cue) need no key — no behavior is left without a control.

**Cut / add candidates for Pass 2 to weigh** (flagged, not decided):
- **CUT (logic, not a key):** the old "#65 voice-follows-speaker / nothing-steals-it" automatic rule is
  **superseded** by the full arbitration (R1–R6) — remove it, don't preserve it.
- **ADD (consider):** a "skip the rest of this session's backlog and let keep-going move on" control,
  *if* pruning an auto-flood via ⌃⌘S / ⌃⌘↓ / cycle proves too blunt in the live test. Not adding now
  (YAGNI until the flood is felt).
- **No hotkey is a cut candidate** — the cockpit binding *set* is largely correct; the rebuild is the
  arbitration *semantics* behind the keys (the CHANGE rows), not the key inventory.

## 9. User stories (the seven scenarios, as outcomes)

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

## 10. Decisions log (every fork, and what was chosen)

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

## 11. Vetoable defaults (chosen by inference, easy to flip in Pass 2)

- **R4 cross-session order:** finish current session to its live edge, then take the longest-waiting
  session's backlog.
- **R5 cut timing:** deliberate moves cut the current readout immediately (vs. finishing the
  sentence).
- **R8 announce trigger:** announce identity on speaker-change only (not every chunk).

## 12. Parked for Pass 2 (need the code)

- **R6 discriminator** — how Sonari tells a human-typed prompt from an autonomous continuation.
- **R11 persistence** — how transcripts, markers, and session identities survive a restart.
- **R10 targeting** — the exact answer-key target (workspace vs speaker) and the fail-closed path.
- **R4 scheduling** — the precise cross-session "what plays next" policy (confirm the §11 default).
- **Marker mechanics** — whether the marker is per-session only (assumed) and how "live edge / idle"
  is detected for keep-going (R4).
- **Quiet-hold + cycle onto a muted session** (unresolved design edge, R7/§6) — re-engaging by cycling
  lifts quiet-hold, but the landed session may itself be muted (silent). Open: does the voice then
  keep-go to a *different* queued session (you hear one session while your workspace sits on the muted
  one), or stay silent until you start it? Resolve in Pass 2 — predictability is the whole point, so
  this edge must have one defined answer.

## 13. What Pass 2 does next

Read the reconciliation reference, the cockpit-grammar bug map, and the code. For each rule above:
does the code satisfy it, diverge, or lack it? Map the gap, decide **keep vs rebuild**, estimate cost,
then plan and build with this spec as the test oracle. Keep the two permanent concurrency guards
green.
