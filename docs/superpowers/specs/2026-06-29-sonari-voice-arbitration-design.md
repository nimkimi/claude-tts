# Sonari — Voice & Session Behavior: Complete Behavioral Spec (Pass 1 — DEFINE)

- **Date:** 2026-06-29
- **Status:** Design — Pass 1 (DEFINE) approved in collaborative brainstorm; **code-closed**. The
  complete behavioral definition — model + state machine + queue contract + control surface +
  transcript/verbosity + navigation + sound language. Awaiting Pass 2 (RECONCILE against the code)
  before implementation planning.
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

- **Transcript** — each session's ordered, append-only record of spoken-eligible output, captured at
  **full fidelity** (raw tool input / output, all prose, decisions) **regardless of verbosity**;
  verbosity filters only what's auto-spoken, never what's kept (§9).
- **Marker — two positions per session** (§10): the **frontier** (the high-water *"furthest I've
  heard"*, which only ever advances and **never moves over unheard output**) and the **browse cursor**
  (where you are when you navigate back to review). The voice reads **forward from the frontier**;
  re-reading moves only the browse cursor. Throughout §5–§7, *"the marker"* means the **frontier**.

And the audio tiers:

- **Readout** — full spoken content; exclusive (one at a time); the speaker's transcript read aloud
  from its marker forward.
- **Ding** — a brief, generic, non-speech earcon when a *non-speaking* session **completes a turn**
  (§11); may overlay the current readout, **never pauses or cuts it**. (Mid-turn streaming is silent.)
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
At any instant ≤1 session is the speaker. Any other session producing output **appends to its
transcript** (behind its frontier) and **dings on turn-completion** (§11) — not on every chunk.
> **Observable:** while A is speaker, B produces output → exactly one readout audible (A's); B's
> transcript grows; B's frontier unchanged; B's ding fires when B *completes a turn*.

### R2 — The speaker is never auto-cut
A new arrival — any session finishing or producing output, including an autonomous continuation —
**never interrupts the current readout**. It dings and accrues (R1).
> **Observable:** A is mid-readout; B finishes → A's audio plays to its natural end, untruncated; B's
> ding plays.

### R3 — Completeness: the marker never skips
When the voice reads a session it goes **forward from its frontier, in order, omitting no item** —
rendered at the current verbosity's detail (§9; the *transcript* itself drops nothing). The frontier
advances only across content you've **dealt with** — *heard*, or *deliberately skipped* (⌃⌘↓, §10.1) —
**never on its own, and never when new content merely arrives.** (Skipped content stays in the
transcript, browsable; it's just out of the auto-catch-up path. A *deliberate* skip is your choice, not
a silent system gap — that's the distinction the "ding and join the pile" rule, D17, was protecting.)
> **Observable:** B's unheard transcript is `[t1, t2, t3]`; when the voice reads B it emits `t1`, then
> `t2`, then `t3`; it never emits `t3` before `t1`; B's frontier reaches `t3` only after all three are
> read.

### R4 — Keep-going on natural idle (ambient); the window stays put
*(Auto-readout layer — applies in everything / medium; **quiet** has no auto-readout, see §9.)*
When the speaker reaches its marker's live edge **and is idle** (e.g. its subagent is working with no
new spoken output), the voice **automatically becomes the speaker of another session that has unheard
output**, reading from *that* session's marker. **The workspace does not change** — only the sound
travels.
> **Observable:** A is idle at its live edge; B has unheard output → the voice begins B's readout
> (speaker = B); the front window and keyboard focus are unchanged from before.

- **Default (vetoable):** when several sessions have unheard output, the voice finishes the current
  session to its live edge, then picks the session whose **oldest unheard output is oldest**
  (longest-waiting first), so nothing starves.
- **Active sessions only (§10.1):** keep-going auto-advances among **active** sessions; a session you
  **stopped** (⌃⌘S / ⌃⌘M) or in **quiet** is *not* auto-drained — it piles navigably and you absorb it
  with the catch-up key. (Switching focus to another session does **not** "stop" the first — it stays
  active / auto-flowed.)

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
at which point it resumes from its frozen marker. While stopped, its output **piles** (dinging on
turn-completion); you absorb that pile with the **catch-up key** (§10.1) — reading from the frozen
marker forward, never an auto-blast. Navigating *to* a still-muted session raises its
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
| voice **quiet-hold** | you submit / jump / cycle | voice → **flowing** (navigated / non-muted session speaks; landing on a *muted* session → open edge, §15) |
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
  (longest-waiting first), so nothing starves. *(Vetoable default — §14.)*
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
| Between-response nav (R3) | ⌃⌘↑ / ↓ | **KEEP** | ⌃⌘↓ to newest = live edge (also: skip a pile to live). |
| Catch up the focused session (§10) | catch-up key (reuse legacy `catch_up`) | **ADD** | Reads forward from your frontier through the pile to live — the bridge for navigable (stopped / quiet) sessions. The new model makes it first-class. |
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
- **ADD (real):** the **catch-up key** (row above) — first-class in the new model (reuse the legacy
  `catch_up` action); it's how you absorb a navigable pile. **Skip** is already served by **⌃⌘↓**
  (jump a session to its live edge).
- **No hotkey is a cut candidate** — the cockpit binding *set* is largely correct; the rebuild is the
  arbitration *semantics* behind the keys (the CHANGE rows), not the key inventory.

## 9. Transcript, verbosity & what's spoken

The transcript (§3) is the **full-fidelity record** of a session — every prose item, every tool use
(raw input / output), every decision — **captured regardless of verbosity**. Verbosity never changes
what is *kept*; it changes only what is *auto-spoken* and at what detail. **This is what lets
completeness and verbosity coexist:** completeness = the transcript never drops; verbosity = a readout
filter you can turn up, or pull past, at any time.

**The three modes (auto-readout rendering):**
- **Everything** — full prose + full tool detail (the actual commands / outputs).
- **Medium** — prose + a **short tool-use summary** ("searching for X in the repo", "reading
  `paths.py`") instead of the raw command. *(Mechanism, Pass-2: the summary is templated from the
  tool's structured input — Grep's pattern, Read's path — needing no LLM for the common case; an opaque
  raw bash line falls back to an LLM-distilled gist, or, failing that, is skipped from the **readout**
  — never from the **transcript**.)*
- **Quiet** — no auto prose. Activity is a **turn-completion ding**, a waiting decision gets the
  **distinct cue**, and you **pull** content on demand (⌃⌘W status, or navigate / raise verbosity to
  hear it). Awareness without narration.

**Verbosity scope.** Today verbosity is one **global** setting (`/sonari:verbosity`), so "quiet →
navigable" (§10.1) applies system-wide. Whether it should instead be **per-session** (only quieted
sessions go navigable) is parked (§15) — a Pass-2 / product call.

**Always recoverable.** Because the transcript is whole, you can always get the full detail of
anything: navigate to it, raise verbosity, or switch a quiet session to everything — the raw record is
right there. A quiet stretch is never a permanent blind spot.

**Scope of the auto-readout layer.** R1 (speaker / ding), R3 (read forward from the frontier), R4
(keep-going), and the invariant's "keeps it going on its own" describe the **auto-readout** of
**everything / medium**. **Quiet has no auto-readout** — nothing speaks on its own; the frontier
advances only as you pull, while dings, the decision cue, and barge-in still apply. *(Consequence:
leaving quiet for everything / medium after a while fires keep-going over the whole accumulated
backlog — a big flood on the mode switch. Same auto-flood you accepted (D7); cut it with stop / rate.)*

> **Observable:** in **medium**, a `Grep "TODO"` tool use is spoken "searching for TODO"; the raw
> invocation is still in the transcript; raising to **everything** (or navigating to that item)
> surfaces the full command. In **quiet**, the same tool use makes no speech; its turn completion
> dings; ⌃⌘W or nav surfaces it.

This refines **R3**: "omitting nothing" means the *transcript* drops nothing; the spoken detail is
whatever the current verbosity renders, always upgradable.

## 10. Navigation & the marker (two positions)

Each session carries **two** positions, not one:
- **The frontier** — the high-water "furthest I've *dealt with*" mark. **Monotonic: it only ever
  advances** — as you *hear* new content, or *deliberately skip* a pile. Keep-going (R4) and forward
  readout read *from the frontier* and push it forward. It never retreats, and new content arriving
  never moves it.
- **The browse cursor** — where you are *right now* when you navigate back to review. Replay /
  older-response / jump-to-decision move the **browse cursor**, never the frontier.

So reviewing carries no penalty: re-hearing an earlier item or jumping to an older response **un-hears
nothing** — the frontier stays put, and keep-going still resumes from it. **⌃⌘↓ (to newest) snaps the
browse cursor back to the live edge / frontier.**

**What advances the frontier:** hearing content *beyond* it (forward readout, keep-going), **or a
deliberate skip** (⌃⌘↓) past a pile you choose to drop — skipped content stays in the transcript
(browsable), just out of the auto-catch-up path. Re-hearing content *below* it does not move it, and
**new content arriving never moves it** — which is exactly what keeps the catch-up key from losing its
place.

The nav keys (§8) operate the **browse cursor**: ⌃⌘← / → (within a response; ← = hear-again),
⌃⌘↑ / ↓ (between responses; ↓ = live edge), ⌃⌘D (to the waiting decision).

> **Observable:** frontier at item 10; you ⌃⌘← to replay item 5 (browse cursor = 5, frontier stays
> 10); a new item 11 arrives; keep-going reads **11** (from the frontier), not 6–10. ⌃⌘↓ returns the
> browse cursor to the live edge.

This pins the meaning of "the marker" used in §5–§7: **"marker" = the frontier**; the browse cursor is
the review-only position introduced here.

### 10.1 Catching up — auto-flow vs navigable (the sweet spot)

A backlog can be handled two ways, and **which one is governed by whether you deliberately stepped
away** — *not* by how big the pile is (no fuzzy threshold):

- **Default — auto-flow (you didn't stop anything).** The voice keeps going (R4): it follows your
  active thread, and when that idles it rolls to other **active** sessions and catches them up from
  their frontiers — contiguous, **no gaps**, hands-free. Small real-time piles drain smoothly; a rare
  big one you skip with **⌃⌘↓** (jump that session to its live edge).
- **Navigable — after a deliberate stop / quiet.** A session you **stopped** (⌃⌘S / ⌃⌘M) or that's in
  **quiet** is **not** auto-drained; its output piles (dinging on turn-completion), and you pull it
  with the **catch-up key**. This is the "I stepped away and several piled up" case — no flood, you
  choose what to hear.

So **"left" means stopped or quieted, not merely switched focus**: switching focus to another session
leaves the first one **active** (still auto-flowed when the voice frees); only a deliberate stop/quiet
turns it into a navigable pile.

Why it has to split this way: you ruled out gaps (a behind session's new turn joins the pile, never
reads ahead — D17, the "ding and join the pile" choice). So a behind session can only **flood** (read its
whole pile) or **wait** (navigable) — there is no "read just the new bit." Convenience (auto-flow) and
rigor (navigable) therefore genuinely trade off; the deliberate stop/quiet is the clean switch between
them.

**The catch-up key** (reuse the legacy `catch_up`) reads **forward from your frontier** on the focused
session, through its whole pile *and anything that arrived while you were away*, until you reach live —
then you're following again. It **can never lose its place**, because the frontier moves *only when you
hear content* (R3), never when new content arrives — new output only extends the pile *beyond* the
frontier.

> **Observable:** you ⌃⌘S-stop session B (it keeps working) → B's new turns ding + pile, the voice does
> not read them; you focus B and press catch-up → it reads from B's frontier forward through the pile to
> live. Meanwhile session C, which you never stopped, is still auto-flowed when the voice is free.

## 11. Sound language (reuse, not invent)

The model's audio tiers (§3) map onto the **existing** cockpit sound vocabulary — consistency means
reuse, not a new earcon language:
- **Generic ding** = the existing **turn-completion** earcon. A non-speaking session dings when it
  **finishes a turn** (a notable "did a thing"), **not** on every streamed chunk. Mid-turn streaming is
  silent; the turn-done ding is the awareness beat.
- **Distinct decision cue** = the existing **decision-alert** earcon (choice / plan / permission),
  audibly distinct from the turn-done ding.
- **Speaker-change announcement** = the existing **spearcon** (time-compressed session name), on a
  change of speaker (R8).
- **Directional pitch chirps** (rising = forward / next / yes, falling = back / prev / no) and the
  **error** earcon (e.g. answering a session with no pending decision, R10) — existing, reused
  unchanged.

No new earcon vocabulary is introduced by this redesign; the arbitration model rides the sounds that
already shipped.

> **Observable:** a background session finishing a turn → exactly one turn-completion ding; the same
> session mid-turn (still streaming) → no ding; a session blocking on a permission prompt → the
> decision-alert earcon, distinct from the turn-done ding.

## 12. User stories (the seven scenarios, as outcomes)

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

## 13. Decisions log (every fork, and what was chosen)

Recorded so Pass 2 does not re-litigate, and so a reversal sweeps every dependent rule.

| # | Fork | Chosen | Why |
|---|---|---|---|
| D1 | Background finishes mid-reply: keep reply + signal / keep reply silent / cut in | **Keep reply + a quick signal** | Never cut the live answer, but stay aware. |
| D2 | The signal: name the session / generic sound / name-on-demand | **Generic ding** (overlays, no pause) | Sessions are autonomous and the user navigates between them, so identity is *pulled*, not pushed. |
| D3 | When the voice is free and sessions produce output: only the attended one / auto-read ambient / pull only | **Auto-read ambient, one at a time** *(everything / medium, **active** sessions; quiet or stopped = dings + pull, §9–§10.1)* | Keep-going; hands-free awareness. |
| D4 | Stop the speaker while another waits: advance to the queue / go fully quiet | **Go fully quiet** | A deliberate stop means silence, not a hand-off. |
| D5 | Cycle by ear: window stays / raises on every landing | *(superseded by D9)* | — |
| D6 | Restart mid-reply: cue + keep sessions / seamless resume / silent recovery | **Cue + keep sessions** | A silent gap reads as "broken"; auto-resume after a gap is disorienting. |
| D7 | Landing on a piled-up session: live edge / catch-up + latest / everything from where you left off | **Everything from where you left off** — *refined by D16: auto-flow for **active** sessions; **stopped/quiet** piles are navigable via the catch-up key, not auto-blasted* | Completeness — audio is the only channel; the user prunes with stop / rate / nav / catch-up. |
| D8 | After a deliberate stop, does brand-new output speak: revive ambient / stay quiet until re-engage | **Stay quiet until re-engage** | Stop is a *lasting* quiet the user controls (discoverable via dings + `⌃⌘W`). |
| D9 | Window ↔ speaker coupling | **Coupled on every user action** (submit, jump, **and cycle**) | The user values hear = see; accepts viewport movement on cycle. Upgrades D5. |
| D10 | Does the system's ambient auto-move also raise the window | **No** | Protects keyboard focus — auto-moving the window would land keystrokes in the wrong session. |
| D11 | Permission prompt mid-reply: cue + jump the line / cue + wait its turn / ordinary ding | **Distinct cue, waits its turn** | Awareness without the system reordering; the user controls timing via `⌃⌘D`. |
| D12 | What's an "item" / verbosity vs completeness | **Transcript captures full fidelity always; verbosity filters only auto-readout** (everything = full · medium = prose + short tool summary · quiet = sounds + pull) | Audio is the only channel — detail must stay recoverable; verbosity is "how chatty now," not "what's remembered." |
| D13 | Quiet mode out loud | **Turn-done dings + distinct decision cue + on-demand pull; no auto prose** | Awareness without narration; the transcript is still whole, so pull / raise verbosity recovers detail. |
| D14 | Marker ↔ navigation | **Two positions — a monotonic frontier + a separate browse cursor** | Reviewing must never re-queue everything after the point you went back to. |
| D15 | How often a background session dings | **On turn-completion** (= the existing turn-done earcon) | You monitor by "a session finished a thing"; per-chunk dinging machine-guns. |
| D16 | Auto-flow vs navigable backlog (the sweet spot) | **A (auto-flow) by default; B (navigable + catch-up) after a deliberate stop / quiet** (§10.1) | Convenience normally, rigor for the rare "away + multiple piles." No gaps were allowed, so a behind session can only flood or wait — stop/quiet picks "wait." |
| D17 | What "left" means + the catch-up key | **"Left" = stopped / quiet (NOT merely switched focus); a first-class catch-up key** (reuse legacy `catch_up`) | The frontier moves only by *hearing*, so catch-up never loses position; switching focus keeps the other session active / auto-flowed. |

## 14. Vetoable defaults (chosen by inference, easy to flip in Pass 2)

- **R4 cross-session order:** finish current session to its live edge, then take the longest-waiting
  session's backlog.
- **R5 cut timing:** deliberate moves cut the current readout immediately (vs. finishing the
  sentence).
- **R8 announce trigger:** announce identity on speaker-change only (not every chunk).

## 15. Parked for Pass 2 (need the code)

- **R6 discriminator** — how Sonari tells a human-typed prompt from an autonomous continuation.
- **R11 persistence** — how transcripts, markers, and session identities survive a restart.
- **R10 targeting** — the exact answer-key target (workspace vs speaker) and the fail-closed path.
- **R4 scheduling** — the precise cross-session "what plays next" policy (confirm the §14 default).
- **Medium tool-summary rendering** (§9) — template the summary from the tool's structured input (no
  LLM) for the common case; LLM-distill or skip-from-readout fallback for opaque raw bash. Behavior is
  defined; the rendering mechanism is Pass-2.
- **Marker mechanics** — whether the marker is per-session only (assumed) and how "live edge / idle"
  is detected for keep-going (R4).
- **⌃⌘↓ skip semantics** (§10.1) — the *principle* is settled (a deliberate skip advances the frontier
  past the pile; the skipped content stays browsable but leaves the auto-catch-up path). The open
  detail: does ⌃⌘↓ *always* do this, or only on a held / repeated press vs. a plain browse-to-live?
  Pass-2 to bind.
- **Verbosity scope** (§9) — global (today) vs per-session; per-session would let only quieted sessions
  go navigable. Pass-2 / product call.
- **Quiet-hold + cycle onto a muted session** (unresolved design edge, R7/§6) — re-engaging by cycling
  lifts quiet-hold, but the landed session may itself be muted (silent). Open: does the voice then
  keep-go to a *different* queued session (you hear one session while your workspace sits on the muted
  one), or stay silent until you start it? Resolve in Pass 2 — predictability is the whole point, so
  this edge must have one defined answer.

## 16. What Pass 2 does next

Read the reconciliation reference, the cockpit-grammar bug map, and the code. For each rule above:
does the code satisfy it, diverge, or lack it? Map the gap, decide **keep vs rebuild**, estimate cost,
then plan and build with this spec as the test oracle. Keep the two permanent concurrency guards
green.
