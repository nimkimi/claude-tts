# Sonari — Voice-Arbitration Reconciliation Reference (Pass 2 — RECONCILE)

- **Date:** 2026-06-29
- **Branch:** `design/voice-arbitration` · **Baseline:** `836 passed, 1 skipped` (this branch, not 858 — that was the Phase-1 branch)
- **Oracle (the WHAT):** `docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md` @ `36b5cfb` (§1–§16 + §10.1)
- **This doc (the gap-check):** maps every spec rule + state machine + queue + control surface + transcript/verbosity + marker + sound onto the **real code**, with a verdict (SATISFY / DIVERGE / LACK), a keep-vs-rebuild call, a cost estimate, and code loci. It is read code-first against ONE unified model of the decision layer (every decision-layer file was read in full), per the Pass-2 contract.
- **Status of the build:** none yet. This precedes `writing-plans`. Where code and spec disagree, **the spec wins** unless a spec error is surfaced here (none material found; see §I corrections to the spec's own framing).

---

## A. What the decision layer actually is today (the architecture snapshot)

One pointer does almost everything, and that is the root of the tangle the spec set out to untie.

- **The voice = `SessionManager._foreground`** — a single session id (`sessions.py:55-57`). The speak loop plays **only the foreground stream** (`host.py:374-432`, `_speak_loop_once`: `fg = self.sessions.foreground(); st = self._state._streams.get(fg); item = st.queue.pop_next()`). Background streams "accumulate untouched until they become foreground" (`host.py:380` comment, `prose.py:18-21`).
- **`foreground` is set by everything:** `set_foreground` on SessionStart **and** UserPromptSubmit (`hooks_entry.py:93-116`), `focus()` on jump (`focus.py:53`), cycle (`focus.py:106`), and nav-cross (`navigation.py:108`, `playback.py:95`). So **"who owns the voice" and "who I last acted on" are the same variable.** The spec's whole move is to split this into **voice/speaker** vs **workspace** (§3).
- **Workspace today = `_os_focused_session`** (`sessions.py:41,114-141`), an **inbound** signal fed by a **real, live focus-watcher inside `sonari-hotkeyd.swift`** — a 0.5s `Timer` polling `NSWorkspace.frontmostApplication` (`sonari-hotkeyd.swift:101-145,233-235`) that emits `OS_FOCUS` with the front terminal's tty (Apple Terminal) / iTerm GUID, or `{focused:false}` when a non-terminal is frontmost. `set_os_focus` resolves it to a session by captured `Identity`; `focused_session()` returns it (`focus.py:23-33`). **This is the spec's workspace — and it already exists.** Caveat (load-bearing): it is fed **only** when (a) hotkeyd is installed (a speech-only user has none), (b) a supported terminal is frontmost, and (c) that terminal is a registered session — **otherwise `None` → every caller falls back to `foreground()`** (`focus-aware-nav design §6`). Today only `nav`, `jump_decision`, and `answer_permission` consult it; the rebuild promotes it to the authoritative workspace + the R6 discriminator.
- **The transcript = `SessionHistory`** (`history.py`): per session, a `deque(maxlen=cap=200)` of `HistoryEntry(text, kind, msg_id, seq, turn_id, heard)`, grouped by message and by turn. It persists **across turns** (a new prompt only `start_turn`s; `SESSION_END` clears). **In-memory, rolling-capped, never written to disk.**
- **The "frontier" today = the per-entry `heard` boolean** (`history.py:24`), flipped True by `note_spoken` only when an utterance **completed** (`host.py:246-253`). There is **no single monotonic position** — just a scatter of per-entry flags. The query `history.unheard(session)` (`history.py:145-154`) is the closest thing to "forward from the frontier," but it is **dead code** ("with catch_up/REPEAT retired it has no replay consumer") and is **bounded to the current turn**.
- **The browse cursor exists:** `SessionStream.nav_cursor` + `nav_turn` (`session_stream.py:20-21`), moved by `nav` / `nav_response` (`navigation.py`). But re-reading **clears the live queue and re-enqueues from the cursor forward** (`navigation.py:46-51`) and re-marks `heard` on replay — i.e. there is **no frontier/browse separation**; the one marker does double duty.
- **Stop = per-session sticky `SessionStream.stopped`** (`session_stream.py:22`), toggled by `stop_session` (`playback.py:31-62`) / `stop_all` (`playback.py:65-81`). The held branch of the speak loop (`host.py:382-407`) only voices `pause_exempt` cues. **There is no voice-global state** — no `flowing` / `quiet-hold` / `stopped-all` (the spec's §6 voice machine).
- **The #65 gate = `_voice_busy_elsewhere`** (`host.py:130-148`): applied in `on_set_foreground` (`lifecycle.py:66-69`) — a background session's prompt event registers (becomes a jump target) instead of seizing the voice when another session is actively speaking/queued/buffering. This is the **crude proxy** for R6; it has no idea whether the submit was human or autonomous (the spec replaces it).
- **Dispatch:** 31 `MsgType`s, every one handler-bound, guarded at import by `assert_complete` (`daemon/__init__.py:11-43`). **Adding a behavior that needs a new message = a new MsgType + handler + an entry in that list** (or import fails).
- **Concurrency discipline (locked, never retire):** pop+claim the foreground item **under `self._lock`** with the cancel-epoch captured atomically (`host.py:408-419`); re-queue-on-stop-mid-utterance **inside the lock** (`host.py:443-453`); no orphaned `_pending_heard` markers (`test_concurrency_guards.py`). Any selection rebuild must preserve all three.

**Net:** the machinery the spec says to keep (streams, queue, assembler, speak-loop mechanism, cancel-epoch/barge-in, history) is clean and tested. The **decision layer riding on top of it is one conflated pointer plus a crude busy-gate, with no keep-going, no voice-global state, no frontier, no persistence.** That is the rebuild surface.

---

## B. Rule-by-rule gap-check (R1–R12)

Legend: **SATISFY** (code already does it) · **DIVERGE** (does something, but not the spec's behavior) · **LACK** (no machinery). Cost is rough dev-effort for the rebuild: **S** ≤ a focused task, **M** a task cluster, **L** a foundational piece several tasks lean on.

### R1 — One voice; everyone else dings and accrues — **SATISFY (mostly), one gap. KEEP. Cost S.**
- The voice is exclusive (one foreground stream plays). A non-speaker's prose accrues in its own stream (`prose.py:18-29`) and a **once-per-turn** "waiting" earcon fires (`host.py:206-209`). 
- **Gap:** the ding is a `"waiting"` earcon fired when a non-foreground stream's queue first becomes non-empty (`_flush_prose_buffer`), **not** the spec's **turn-completion** ding (§11/D15 = the existing `turn_done` earcon). The spec wants the dings tied to **turn completion**, and §11 says reuse `turn_done`, not a separate `waiting` cue. → small rewire (see §F-sound).

### R2 — The speaker is never auto-cut — **SATISFY. KEEP. Cost 0.**
- Nothing auto-cuts the foreground readout: only `speaker.cancel()` cuts, and it is called only on deliberate actions (flush-on-own-prompt, jump, cycle, nav, stop, answer, where-am-i). An arriving background completion just enqueues into its own stream. The one nuance: a background autonomous **submit** currently routes through the #65 gate so it can't become foreground mid-speech (`lifecycle.py:66`) — so it can't cut either. **Behaviorally satisfied today**, but for the *wrong* reason (the busy-gate, not a human/autonomous discriminator) — R6 makes the reason right.

### R3 — Completeness: the marker never skips — **DIVERGE (one marker, not two). REBUILD (the marker model). Cost L.**
- Within a session, order is FIFO and the transcript drops nothing *except* the 200-entry rolling cap (`history.py:30`). `heard` flips only on completion (interrupted stays unheard) — good.
- **Divergence:** there is **no monotonic frontier**. `heard` is per-entry; nav replay re-enqueues `ids[new:]` and re-marks them heard (`navigation.py:46-51`). The spec's R3+§10 require a **frontier that only advances on hearing/skip and never on re-read or new content**, separate from the browse cursor. Today re-reading and forward-reading both run through the same `nav_cursor`+`heard` machinery. → this is the **transcript/two-position-marker rebuild** (shared with R4, §10, catch-up).

### R4 — Keep-going on natural idle (ambient); window stays put — **LACK. REBUILD (foundational). Cost L.**
- **The single biggest lack.** The speak loop plays **only the foreground stream** and, when it empties, **waits** (`host.py:428-432`). There is **no cross-session auto-advance.** A background session with unheard output is silent until the user manually `jump`/`cycle`s to it.
- Everything R4 needs is new: idle/live-edge detection per session, a cross-session "what speaks next" selection (longest-waiting-first, §14), **without moving the workspace** (R12/D10), and confined to **active** sessions (not stopped/quiet, §10.1).
- **This is the contract tension the advisor flagged:** "keep the speak loop" is true for the *mechanism* (pop+claim+speak under lock), but the loop's **session-selection** today (`fg = foreground()`) **is part of the rebuild** — selection must cross sessions while preserving the locked pop/claim/cancel-epoch discipline. The two concurrency guards are the safety net for exactly this.

### R5 — You override the flow; window follows your action — **DIVERGE (coupling incomplete). REBUILD. Cost M.**
- Submit → foreground (`lifecycle.py:69`) — but gated by #65, and "workspace already there" is only true if the human typed there (R6/R12 interaction).
- Jump (`⌃⌘J`) → `focus()` (voice) **+ raises the window** (`focus.py:53,84-88`). **Couples voice+workspace** ✓ — but only to the single `_waiting_target` (`focus.py:7-20`), **excluding stopped sessions** — you cannot "go there" to an arbitrary or a muted session via ⌃⌘J.
- **Cycle (`⌃⌘Tab`) → `focus()` (voice) but NO raise** — `focus.py:91-113` is explicitly "A SOFT switch (no terminal-raise)". **This contradicts the spec (R5/R12: cycle raises) AND the cockpit-grammar memory** that claimed cycle raises. **CHANGE confirmed against live code:** cycle must raise the window. (This is precisely the §8 fidelity caveat paying off.)
- So R5's "each deliberate move makes that session the workspace" is **half-true**: jump raises, cycle doesn't, submit assumes you're already there. Rebuild = make all three couple speaker+workspace, and make "go there" able to target a chosen/muted session.

### R6 — Autonomous ≠ deliberate (the preempt discriminator) — **DIVERGE (crude proxy). REBUILD. Cost M. Mechanism = #1 open question.**
- Today **every** `UserPromptSubmit` → `SET_FOREGROUND` + `FLUSH` unconditionally (`hooks_entry.py:93-98`), with **no human-vs-autonomous signal**. CONC-1 (a background agent's own continuation grabbing the voice and cutting your live answer) is mitigated only by the `_voice_busy_elsewhere` busy-gate (`lifecycle.py:66`), which is a **proxy for "is the voice busy," not "did a human type this."**
- The spec's R6 demands the real discriminator: a human-typed submit preempts (R5); an autonomous submit dings + accrues and never seizes (R1/R2). 
- **RESOLVED, but NOT via focus alone (adversarial pass corrected an unsafe first cut).** No hook-data discriminator exists (the `UserPromptSubmit` payload is 6 fields — `session_id, transcript_path, cwd, permission_mode, hook_event_name, prompt` — no autonomy flag; structurally, **subagents and mid-turn continuations don't fire `UserPromptSubmit` at all** — only a `/loop`/cron/headless **self-submit** does). 
- **The trap (C1):** "a submit preempts iff it's from the workspace (`focused_session()`)" is **unsafe** — the converse of "typing needs focus" ("anything from the focused terminal was typed") is **false**: an autonomous loop running *in your focused window* also self-submits with that session's id. During keep-going (speaker ≠ workspace), that submit would cut the session you're actually hearing — **CONC-1 relocated, a violation of the hard rule.** A human submit to a non-speaker focused session and an autonomous self-submit from it are **fundamentally indistinguishable** (no hook field, no focus signal separates them).
- **The safe resolution (recommend Policy A):** drop focus from the *preempt* decision. **A typed submit takes the voice immediately iff the voice is IDLE, or the submit comes from the session you are currently HEARING (the speaker).** Any other submit — including from your focused-but-not-heard window — **dings + accrues** and is reached instantly with **⌃⌘J / ⌃⌘Tab** (which always preempt — they're real hotkeys, unambiguously deliberate). This kills CONC-1 in **every** configuration and honors the §2/§4 hard rule ("never cut the live readout," which the spec says overrides convenience) absolutely. **Cost:** typing into a focused-but-not-speaker session isn't an *instant* cut — but jump/cycle (Nima's normal "go there" gesture) is. Focus is still used for R10/R12 (workspace = where you answer / raise), just **not** for the preempt decision. → **Policy A vs the more-responsive Policy B is a genuine runtime-feel fork; batched to Nima** (§I-1).
- **Where it lands (M4):** the discriminator lives where the #65 gate is — `on_set_foreground` (`lifecycle.py:56-69`), which is stacked `@handler` for **both** `SET_FOREGROUND` and `SESSION_START` (and `UserPromptSubmit` emits `SET_FOREGROUND` too, `hooks_entry.py:100-101`). The plan must specify the **SESSION_START** path under the new rule: a fresh session has no identity/transcript yet, isn't the speaker, and (Policy A) only takes the voice if the voice is idle — so a new session opening while you're hearing another **won't cut it** (the desired B5-1-adjacent behavior). State this explicitly; don't let SESSION_START fall through the submit logic.

### R7 — Stop = lasting deliberate quiet; workspace untouched — **DIVERGE (per-session only; no voice-global hold). REBUILD. Cost M.**
- `stop_session` mutes the foreground stream stickily and re-reads from the interrupted item on resume (`playback.py:31-62`) — the per-session half is solid and matches "marker freezes / resumes from there."
- **Missing the voice-global half:** the spec's `⌃⌘S` ALSO puts the **voice into quiet-hold** (no keep-going for anyone; new output only dings; **lifts on re-engage**). Today there is no keep-going to suppress and no voice-global state, so quiet-hold doesn't exist. This rule is **co-dependent with R4** — quiet-hold only means something once keep-going exists.
- `stop_all` (`⌃⌘M`) mutes all + cancels (`playback.py:65-81`) → maps to `stopped-all`, but again no global state object, and no "re-engage lifts it, each session stays muted until individually started."
- Workspace untouched ✓ (stop never raises).
- **Discoverability (R7):** `⌃⌘W` must report the voice state (flowing/quiet-hold/stopped-all). Today `on_where_am_i` reports only per-session "Playing/Stopped" + waiting count (`control.py:120-163`) — **no voice-global state.** → §8 CHANGE.

### R8 — You always know who's speaking — **SATISFY. KEEP. Cost 0–S.**
- `_attributed_text` prefixes the folder (or plays the spearcon) **only on a speaker change** to a different session, not on continuation (`host.py:225-244`); spearcon path via `_spearcon_path`. Matches R8 + the §14 announce-on-change default. Keep as-is; the only change is that keep-going (R4) introduces *more* speaker changes, all of which already route through `_attributed_text`.

### R9 — Permission prompts: distinct cue, no jump, no cut — **SATISFY. KEEP. Cost 0.**
- A blocking `PermissionRequest` fires `earcon("permission")` immediately (the distinct decision cue, `decisions.py:164`), records+enqueues the decision into the **asking session's own stream** (normal marker order, no cut, no jump-ahead — `decisions.py:165-168`), and registers a pending decision that blocks **outside** the lock (`host.py:255-269,472-481`). `⌃⌘D` reaches it (`playback.py:85-114`). All matches R9. (Non-blocking `Notification` permission and `CHOICE`/`PLAN` paths likewise enqueue in order.)

### R10 — You can never answer the wrong session — **SATISFY (target ≈ workspace). KEEP, re-verify under R12. Cost S.**
- `answer_permission` targets `focused_session() or foreground()` and **error-tones** when that target has no pending decision (`decisions.py:177-197`); fail-closed everywhere (`host.py:469-470,480`, `hooks_entry.py:132-141`). Matches R10.
- **Caveat for the rebuild (M3 — bigger than "one line"):** `answer_permission` targets `focused_session() or foreground()` (`decisions.py:185`). The spec says answer targets the **workspace**. The danger is the **fallback**: today `foreground()` *is* the speak-loop's speaker (`host.py:382,413`). If the rebuild advances keep-going by repointing `foreground()`, then for a focus-unknown user (no hotkeyd / non-terminal frontmost → `focused_session()` is `None`), ⌃⌘⏎ would answer **whatever keep-going last voiced** — not where the user is. So the fix is **not** one line: (i) the speak loop must read an explicit **speaker** pointer, not `foreground()`; (ii) `answer_permission`'s fallback must resolve to the **workspace** (the last deliberately-acted session), never the auto-advancing speaker; (iii) decide what `foreground()` *becomes* after the speaker/workspace split (maps to workspace, or is retired). Load-bearing for "never answer the wrong session."

### R11 — Restart announces itself and loses nothing — **LACK (total). REBUILD (foundational). Cost L.**
- **Nothing persists.** `SessionManager` (foreground, identities, os-focus) and `SessionHistory` are pure in-memory; grep confirms **zero** disk writes of session/marker/history state. On restart: identities lost (→ FOCUS-1/B5-1 from the reconciliation reference), foreground null, transcripts gone.
- **No restart cue** ("restarted" appears only in comments).
- R11 requires: persist sessions + transcripts + markers (frontier) + identities across a daemon restart, replay a "Sonari restarted" cue, do **not** auto-resume the interrupted readout, and keep cycle/jump/raise working. → mechanism is a §G open (what to serialize, where, when, atomicity).

### R12 — The window rule (cross-cutting) — **DIVERGE, but the separation is largely FREE. REBUILD (mostly cycle-raise + promote workspace). Cost M (less than first feared).**
- **Key realization:** the speaker/workspace split is *naturally* realized by what already exists — **speaker = the speak-loop's current session** (voice), **workspace = `focused_session()`** (inbound OS focus, fed by the live focus-watcher). Keep-going (R4) never raises a window, so the OS focus — and thus the workspace — **stays put on its own**; the decoupling falls out for free as long as the R4 selector keys off "sessions with unheard output," not the workspace.
- The conflation to break is `foreground`: today it is *both* "the voice owner" *and* "the last session I deliberately acted on." Split it into **speaker** (voice) and **workspace** (`focused_session`, with `foreground` fallback).
- "Workspace changes on exactly submit/jump/cycle, never on its own." Today: **submit** → the human is physically typing in that terminal → the watcher reports `os_focus` → workspace follows (≤0.5s) ✓; **jump** raises the target window (outbound AppleScript) → OS focus moves → workspace follows ✓; **cycle does NOT raise** → workspace does NOT follow ✗ (the one real CHANGE). 
- **No feedback-loop risk:** the daemon never *sets* `os_focus` — it *raises a window* (outbound) and the OS *reports* focus (inbound). One source of truth (the OS). So R12 is: (i) **cycle must raise** (so the workspace follows it), (ii) promote `focused_session()` to the authoritative workspace consulted by R6/R10 with the `foreground()` fallback, (iii) ensure the R4 selector never reads the workspace.

---

## C. State model (§6) — mapping to code

| Spec state | Today | Verdict |
|---|---|---|
| per-session **idle** | implicit (foreground stream empty + not producing) — not a named state | LACK (needs naming for R4 idle-detection) |
| per-session **producing** | implicit (assembler has pending / prose buffering) | partial |
| per-session **queued** | `len(stream.queue) > 0` (live) — but "unheard" really lives in `history.heard` | DIVERGE (queue vs transcript-frontier are two different notions today) |
| per-session **speaking** | `session == foreground()` and it's the one being popped | SATISFY |
| per-session **muted** | `stream.stopped` (sticky) | SATISFY |
| orthogonal **blocked-on-decision** | proxy: `queue.has_decision()` + a `_pending_decisions[session]` Event | DIVERGE (no explicit flag; two proxies) |
| voice **flowing** | implicit default | LACK (no object) |
| voice **quiet-hold** | — | LACK |
| voice **stopped-all** | all streams `stopped`, but no global marker | LACK |
| **workspace pointer** | `_os_focused_session` (OS-driven, inbound only) | DIVERGE (not daemon-authoritative) |

**Implication:** the rebuild introduces (a) an explicit **voice-global state** enum (flowing / quiet-hold / stopped-all), (b) an authoritative **workspace pointer**, and (c) a clean notion of **"has unheard output"** = transcript content past the frontier (which unifies the "queued" state with the marker model). The per-session producing/idle/speaking/muted states mostly exist; they need naming + idle-edge detection for R4.

The §6 transition table is the test oracle for the rebuilt machine. The two that don't exist at all today: **"speaker hits live edge + idle → next queued session speaks (voice stays flowing)"** (R4) and **"quiet-hold + re-engage → flowing"** (R7).

---

## D. Queue & ordering (§7) — mapping to code

- **Within-session FIFO + marker-advances-only-on-read:** SATISFY (`queue.py` deque; `heard` on completion). KEEP.
- **Across-session selection (longest-waiting-first):** LACK — no cross-session selection exists (R4). The spec's default (finish current to live edge, then oldest-unheard-oldest) is **new policy** the rebuilt selector implements.
- **Preemption only by deliberate action; autonomous never preempts:** DIVERGE → REBUILD (R6).
- **Barge-in (cut current, speak at front, re-queue interrupted at front, resume):** SATISFY — `where_am_i` (`control.py:130-163`) is the canonical implementation; `cancel()` + `enqueue_front`. KEEP. Rate (`⌃⌘+/−`) does not cut (`control.py:30-56` enqueues a "Rate N." line; no cancel) ✓.
- **Dings not queued (fire-and-forget):** SATISFY — `speaker.earcon()` is out-of-band (`host.py:209`, `prose.py:59`). KEEP. (Rewire which earcon: turn_done vs waiting — §F.)
- **Identity announce on cross-session change:** SATISFY (`_attributed_text`). KEEP.
- **Stop/restart never drop queued content:** stop ✓ (freezes, doesn't clear — `playback.py` sets `stopped`, doesn't `clear()`); **restart ✗** (everything is lost — R11).

---

## E. Control surface (§8) — VERIFIED against the REAL keymap

Confirmed against `keymap.py` `ACTION_MESSAGES` (`keymap.py:26-47`) and `_DEFAULT_KEYS` (`keymap.py:54-60`) + macOS `extra_default_bindings` (cycle = ⌃⌘Tab/⇧Tab; response-nav = ⌃⌘↑/↓). **The §8 fidelity caveat earned its keep.** Rows where the *verified* verdict differs from the spec's: **stop_all** (spec KEEP → CHANGE, needs the `stopped-all` voice state), **submit** (spec KEEP → REBUILD, the handler semantics are the R6 rebuild), **catch-up** (spec ADD-reuse → ADD-**genuine**, legacy retired), and **⌃⌘D** (spec KEEP → CHANGE, it doesn't actually raise — C2). *(The spec's cycle row was already CHANGE "was voice-only" — the spec was right about cycle; this doc merely confirmed it against live code.)*

| Behavior (rule) | Control | Spec verdict | **Verified verdict** | Note |
|---|---|---|---|---|
| Stop/start focused (R7) | ⌃⌘S `stop_session` | CHANGE | **CHANGE ✓** | Add voice quiet-hold; per-session resume-from-marker already there (`playback.py:31-62`). |
| Stop everything (R7) | ⌃⌘M `stop_all` | KEEP | **CHANGE** | Behaviorally KEEP, but needs the `stopped-all` voice-global state object it lacks today. |
| Jump to waiting (R5/R11/R12) | ⌃⌘J `jump_waiting` | CHANGE | **CHANGE ✓** | Already raises (`focus.py:84`); fixes after R11 (identity persists). But target = only `_waiting_target`, **excludes muted** — R7 wants to be able to go to a muted session. Widen targeting. |
| Cycle next/prev (R5/R12) | ⌃⌘Tab / ⌃⌘⇧Tab `cycle_session` | CHANGE | **CHANGE — confirmed live divergence** | **Cycle does NOT raise today** (`focus.py:91` "SOFT switch, no terminal-raise"). Must add raise. The cockpit-grammar memory that "cycle raises" is FALSE vs code. |
| Submit (R5/R6) | type+enter (`UserPromptSubmit`) | KEEP | **REBUILD** | The binding is "kept" (it's not ours), but the **handler semantics** (`hooks_entry.py:93-98` → unconditional SET_FOREGROUND+FLUSH) are the R6 rebuild. |
| Within-response nav / hear-again (R3) | ⌃⌘← / → `nav_prev`/`nav_next` | KEEP | **KEEP (semantics shift under §10)** | ← = hear-again ✓ (`navigation.py`); but "hear-again must not move the frontier" needs the marker rebuild. |
| Between-response nav (R3) | ⌃⌘↑ / ↓ `nav_prev_response`/`nav_next_response` | KEEP | **KEEP (semantics shift)** | ⌃⌘↓ = "Back to the latest" / live edge (`navigation.py:74-89`). The spec also wants ⌃⌘↓ to **skip a pile** (advance frontier past it) — that's the §10.1 skip semantics, a behavior ADD on the same key (§G). |
| Catch up focused (§10) | catch-up key | ADD ("reuse legacy `catch_up`") | **ADD — genuine, NOT a reuse** | **`catch_up`/REPEAT were RETIRED** (only a `history.py:149` comment remembers them; not in `ACTION_MESSAGES` or `MsgType`). New action + new MsgType + new handler + a default chord (none free in the cockpit set — §G). |
| Go to waiting decision (R9) | ⌃⌘D `jump_decision` | KEEP | **CHANGE (C2 — verify found it doesn't raise)** | `on_jump_decision` (`playback.py:85-114`) moves the voice (`sessions.focus`) but **never calls `_raise()`** (raise lives only in `focus.py`'s `on_jump_waiting`). Spec R5/R9 make ⌃⌘D a "go-there" that **raises** (speaker **and** workspace). Add a raise — load-bearing for R10 (else you answer the session you physically left). Secondary: it targets only `focused_session() or fg` (`playback.py:92`), so it can't reach a pending decision in a third session — a follows-focus design choice that diverges from R9's "reach the waiting prompt"; engage it in the plan. |
| Approve/deny (R10) | ⌃⌘⏎ / ⌃⌘⎋ `approve`/`deny` | KEEP | **KEEP ✓** | `decisions.py:177-197`; fail-closed; targets `focused_session()`. Re-verify target = workspace under R12. |
| Where am I (R7/R8) | ⌃⌘W `where_am_i` | CHANGE | **CHANGE ✓** | Add voice-global state to the spoken status (`control.py:120-163` reports only per-session today). |
| Speed up/down (R4/D7) | ⌃⌘+ / − `faster`/`slower` | KEEP | **KEEP ✓** | No-cut (`control.py:30-56`). Norwegian-keyboard +/− physical-position check is a live-dogfood item, not a logic change. |
| Settings (verbosity/voice/minqueue/keymap) | slash commands | KEEP | **KEEP ✓** | Orthogonal. |

**CUT — but with a carve-out (M2):** cut the **#65 *seize-gate policy*** (the `lifecycle.py:66` branch that registers-instead-of-foreground), which R1–R6 supersede. But **do NOT delete the idle predicate inside `_voice_busy_elsewhere` (`host.py:139-147`)** — extract and keep it: it is reused as (i) the R4 keep-going **idle / live-edge signal** (its inverse) and (ii) the Policy-A preempt test ("voice idle"). So: **refactor the idle-detection core out and retain it; remove only the seize-gate policy that called it.** Also re-evaluate the legacy `STOP` (`playback.py:7-17`) and `SKIP` (`playback.py:20-28`) handlers — `STOP` overlaps `stop_session`; `SKIP` advances `heard` then cancels (a frontier op). Keep both only if a CLI consumer needs them; otherwise fold into the new model. (`stop`/`skip` are not hotkey actions — `keymap.py:24` note.)

**Coverage:** every rule with a user action has a binding; automatic rules (R1–R4 ambient, R8 identity, R11 cue) need none. The **one new binding** is the catch-up key.

---

## F. Transcript/verbosity (§9), marker/sweet-spot (§10/§10.1), sound (§11)

### Transcript & verbosity (§9) — **DIVERGE / partial. REBUILD the fidelity guarantee. Cost M.**
- `verbosity` is **global** (`config["verbosity"]`, read via `ctx.verbosity`, `context.py:37-39`) — confirms the §9/§15 parking-lot premise (global vs per-session is unresolved; today = global).
- **Quiet already = no auto-readout** (`prose.py:20` `speak = verbosity != "quiet"`; quiet records to history but doesn't enqueue) — matches §9's "quiet has no auto-readout." SATISFY for prose.
- **The fidelity guarantee is violated for tools:** `on_tool` only enqueues in `everything` and **never calls `history.record`** (`prose.py:39-50`) — so tool uses are **absent from the transcript in every mode**, and **medium drops them from the readout AND the record**. The spec §9/D12 require the transcript to capture **every tool use (raw input/output) regardless of verbosity**, with medium rendering a short summary from structured input. → REBUILD: record tools to the transcript always; render per verbosity (everything=full, medium=short summary from `tool_input`, quiet=none-spoken). The summary mechanism (template from structured input; LLM/skip fallback for opaque bash) is a §G open but the spec defines it as Pass-2 mechanism.
- **Cap & persistence undercut "always recoverable":** the 200-entry rolling cap (`history.py:30`) silently evicts old transcript; nothing persists (R11). "A quiet stretch is never a permanent blind spot" only holds within the cap and within one daemon lifetime. → tie-in with R11.

### Marker / sweet-spot / catch-up (§10/§10.1) — **REBUILD (foundational, shared with R3/R4). Cost L.**
- **Frontier (monotonic):** LACK as a single position — today scattered `heard` flags + dead `unheard()`. Build a per-session frontier that advances **only** on hear/skip, never on re-read or new content.
- **Browse cursor:** EXISTS (`nav_cursor`/`nav_turn`) but is entangled with the frontier (replay re-marks heard). Decouple: nav moves only the browse cursor; the frontier is untouched by review.
- **Catch-up key:** ADD (new action/MsgType/handler/binding) — reads **forward from the frontier** through the pile to live; seed is `history.unheard` but it must (a) be revived, (b) span the relevant range (the pile, possibly across turns — today bounded to the current turn), (c) advance the frontier as it reads.
- **Auto-flow vs navigable sweet spot:** the discriminator is **"did you deliberately stop/quiet this session"** (§10.1/D16/D17), NOT pile size. Active sessions get keep-going (R4); stopped/quiet sessions become navigable piles absorbed by catch-up. "Left" = stopped/quiet, not merely switched-focus — so switching focus must leave the other session **active** (auto-flowed when the voice frees). This is a clean rule; it rides on R4 (keep-going) + R7 (stop semantics) + the frontier.

### Sound (§11) — **SATISFY (vocabulary exists); one rewire. KEEP + small CHANGE. Cost S.**
- The earcon vocabulary already shipped: `turn_done`, decision earcons (`choice`/`plan`/`permission`), `error`, spearcons (`_spearcon_path`/`_attributed_text`), directional pitch chirps (`speaker.pitch`, `playback.py`/`navigation.py`/`decisions.py`). §11 says **reuse, don't invent** — so this is mostly KEEP.
- **The one rewire (not purely additive):** the background "something landed" ding must be the **turn-completion** earcon (D15), fired when a **non-speaking** session **completes a turn** — today it's a separate **`waiting`** earcon fired when its queue first fills (`host.py:206-209`). Move the ding to the turn boundary (`Stop`→`turn_done`, `hooks_entry.py:88-91` / `prose.py:53-64`) for non-speaking sessions, and retire the `waiting` cue. **But `turn_done` currently fires for EVERY session including the speaker** (`on_earcon` plays it unconditionally, `prose.py:53-60`) — the rewire must also **suppress `turn_done` for the speaker** (whose turn-end you're hearing live), so it becomes a *background-only* cue. (Mid-turn streaming stays silent — already true. Note the cosmetic double-earcon today: a background `permission_request` fires `permission` then `_flush_prose_buffer`'s `waiting` — `decisions.py:164/167` → `host.py:206-209`; retiring `waiting` cleans it up.)

---

## G. Parking-lot resolutions (§15)

| Item | Resolution | Confidence |
|---|---|---|
| **R6 discriminator** (CONC-1, #1) | **✅ RATIFIED 2026-06-29 — Nima chose Policy A.** Final rule: **a typed submit takes the voice immediately iff the voice is idle OR the submit is from the speaker (the session you're hearing); otherwise it dings + accrues, reached via ⌃⌘J/⌃⌘Tab.** Focus is NOT used for the preempt decision (no 0.5s-poll race on the cut path). _(Detail below.)_ **RESOLVED (focus-only first cut was unsafe — see §B-R6/C1).** No hook-data discriminator; a human submit to a non-speaker focused session is **indistinguishable** from an autonomous self-submit from it. **Policy A (recommend):** a typed submit takes the voice immediately iff the voice is **idle** OR the submit is from the **speaker** (the session you're hearing); everything else dings + accrues, reached via ⌃⌘J/⌃⌘Tab (always-preempt hotkeys). Kills CONC-1 in every config; honors the hard rule absolutely; focus-independent (no 0.5s-poll race on the preempt path). Cost: typing into a focused-but-not-heard session isn't an instant cut. **Policy B (more responsive):** preempt iff submit == workspace (`focused_session()`); faithful to R5-submit immediacy but carries the **C1 hole** (an autonomous loop in your focused window cuts the keep-going speaker). **Genuine runtime-feel fork → Nima decides** (§I-1). | **resolved-pending-Nima** |
| **R11 persistence** | Serialize the durable state (sessions + identities + per-session transcript + frontier + folder labels; NOT live queues or voice-global transient state) to `~/.sonari/` via the existing `atomicio` atomic-write, snapshotted at safe points (turn boundary / session lifecycle / on a debounce), reloaded on `run()`. "Interrupted readout does not auto-resume" = restore the frontier but do not re-enqueue. Exact cadence/format = a plan decision; the **mechanism is feasible and additive** (atomicio + a schema). | high it's feasible; format TBD |
| **R10 answer-targeting** | Keep `answer_permission` targeting the **workspace** (`focused_session()`), made daemon-authoritative under R12, fail-closed (error tone when no pending decision). Confirm it tracks workspace, not speaker, once they decouple. | high |
| **R4 scheduling default** | Adopt the §14 default verbatim: finish current session to live edge, then the session whose **oldest-unheard is oldest** (longest-waiting-first). It's a clean, starvation-free policy; vetoable later. | high |
| **Marker mechanics** | Per-session frontier (monotonic) + per-session browse cursor (exists). "Idle / live edge" for R4 = foreground stream queue empty AND assembler has no pending AND no open prose buffer (the daemon already knows all three — `_voice_busy_elsewhere` reads exactly these at `host.py:144-147`). Reuse that predicate, inverted, as the idle signal. | high |
| **⌃⌘↓ skip semantics** | ⌃⌘↓ stays "to newest / live edge" (browse) by default; a **deliberate skip that advances the frontier past a dropped pile** is the harder case. Recommend: a plain ⌃⌘↓ = browse-to-live (no frontier move); the **catch-up key** owns "absorb the pile," and a distinct **held/repeat** or a dedicated skip is the "drop the pile, advance frontier" action. Bind precisely in the plan; low-risk either way. | medium (settle in plan) |
| **Verbosity scope** | global (today) vs per-session — **genuine product fork** (per-session would let only quieted sessions go navigable while others stay everything). **Batch to Nima** (§I). | needs Nima |
| **Quiet-hold + cycle-onto-muted** (must have ONE answer) | Recommend: re-engaging by cycling **lifts quiet-hold and the voice keep-goes to a different *active* queued session** while the workspace sits on the (still-silent) muted session you landed on — consistent with speaker≠workspace (R12) and "navigation never un-mutes" (R7). The alternative (stay fully silent until you ⌃⌘S the landed session) is more surprising for a pilot monitoring others. **Recommend the keep-go answer; confirm with Nima** (§I). | recommend; confirm |
| **Catch-up key binding** | Genuine ADD (legacy gone). No chord is free in the cockpit set; propose a new default chord (e.g. ⌃⌘Y / ⌃⌘G) — **a taste/keymap call for Nima** (§I), co-designed per his keybinding-tuning preference. | needs Nima |

---

## H. Keep-vs-rebuild summary + cost

**KEEP (clean, tested — do not touch the mechanism):** per-session streams (`session_stream.py`), `SpeechQueue` (`queue.py`), `ProseAssembler`, the speak-loop **mechanism** (pop+claim+speak+note_spoken under lock, cancel-epoch/barge-in — `host.py`/`speaker.py`), `SessionHistory` storage (`history.py` — extend, don't replace), the dispatch/registry/server/Ctx glue, `test_concurrency_guards.py`. R2, R8, R9, R10, barge-in, dings-out-of-band, no-cut-rate all keep.

**REBUILD (the decision layer):**
- **L (foundational):** R4 keep-going + cross-session selection (`host.py` speak-loop selection); the transcript **frontier/browse-cursor** model (R3/§10, `history.py` + `navigation.py` + `note_spoken`); R11 persistence (new module + lifecycle).
- **M:** R5/R12 speaker-vs-workspace decoupling + cycle-raises + workspace pointer; R6 discriminator (`hooks_entry.py` + `lifecycle.py`); R7 voice-global quiet-hold/stopped-all state machine (§6) + ⌃⌘W reporting; §9 tool-transcript fidelity + medium rendering.
- **S:** §11 ding rewire (waiting→turn_done); catch-up key wiring; §10.1 sweet-spot gating (rides on R4+R7).
- **CUT (with carve-out):** the #65 **seize-gate policy** (`lifecycle.py:66`), NOT the idle predicate inside `_voice_busy_elsewhere` (`host.py:139-147`) — extract + keep that for R4 idle-detection and Policy-A preempt (M2); re-evaluate legacy `STOP`/`SKIP`.

**New concepts the rebuild introduces:** (1) an explicit **voice-global state** (flowing/quiet-hold/stopped-all); (2) an authoritative **workspace pointer** decoupled from the speaker; (3) a monotonic **frontier** separate from the browse cursor; (4) **cross-session keep-going selection**; (5) the **catch-up key**; (6) **persistence** of sessions/transcripts/markers/identities; (7) **tool entries in the transcript** + medium rendering.

**Estimated shape:** ~3 foundational (L) pillars + ~5 (M) + ~3 (S) → a multi-task plan. The foundational L's (frontier model, keep-going selection, persistence) should land first as they underpin the rest; R6 must be resolved (mechanism) before its task.

---

## I. Forks — Nima's rulings (2026-06-29)

**✅ #1 R6 preempt policy → Policy A** (Nima chose). **✅ #2 Verbosity scope → global** (delegated; stop already gives per-session navigability). **✅ #3 Cycle-onto-muted → lift hold + keep-go to another active session** (delegated; navigating never un-mutes). **⏳ #4 Catch-up chord → co-design at build time** (new binding required). Originals below for the record.

1. **R6 preempt policy (runtime feel) — Policy A vs B. → A chosen.** **A (recommend):** a typed submit cuts the voice only if the voice is idle or the submit is from the session you're hearing; otherwise it dings + accrues and you reach it with ⌃⌘J/⌃⌘Tab. Honors "never cut the live readout" absolutely; cost = no *instant* cut when you type into a focused-but-not-heard session. **B:** a submit from your focused front terminal cuts the voice immediately (more faithful to "type = it speaks now"), but an autonomous loop in your focused window can cut the session you're hearing (the C1 hole). Nima feels this one directly — his call.
2. **Verbosity scope** — global (simpler; today) vs per-session (only quieted sessions go navigable; more control, more state). Product call.
3. **Quiet-hold + cycle-onto-muted edge** — confirm the recommended answer (cycle lifts the hold and keep-goes to another *active* session while the workspace sits on the muted one) vs stay-silent-until-started.
4. **Catch-up key chord** — a new binding is required (legacy retired); co-design the chord.

## J. Risks / concurrency-guard implications

- **Cross-session selection (R4) is the riskiest change** — it moves the speak loop from "pop the foreground stream" to "pick a session, then pop." **The selection scan itself must be atomic with the pop, under `self._lock` (M1)** — if the scan-for-longest-waiting runs *outside* the lock and only the pop is locked, a `STOP_SESSION`/`STOP_ALL`/`on_flush` landing between scan and pop makes the loop pop from a now-stopped/flushed stream → speaking a muted session or resurrecting flushed content. So: scan + pop + claim + cancel-epoch in **one** locked block. The stop-mid-utterance re-queue already keys off `item.session` not `foreground()` (`host.py:449-451`) — so it's already keep-going-ready; only the **selection scan** is the new exposure. The two guards (`test_concurrency_guards.py`) are the safety net and **must stay green** — they already hammer SET_FOREGROUND/JUMP_WAITING/FLUSH/STOP against the real loop; **add the cross-session selector to that hammer set.**
- **Persistence (R11)** introduces disk I/O on the daemon — keep it **off the hot path** (snapshot at boundaries, atomic writes), never inside the speak loop's lock-held section.
- **Workspace authority (R12)** — no feedback loop exists (the daemon raises a window; the OS reports focus inbound = one source of truth). The real watch-items: (a) the **0.5s focus-poll latency** means the workspace lags a fast deliberate move by up to half a second — fine for nav, but it's the residual risk in the R6 discriminator (mitigated by the never-cut-on-unknown fallback); (b) the **conditional feed** (no hotkeyd / non-terminal frontmost / unregistered terminal → `None`) means R6/R10/nav must all degrade cleanly to `foreground()`; (c) **restart loses identities** until re-registration, so the workspace is unknown right after a restart (→ R11 persistence should restore identities so focus re-resolves).

---

## K. What's next (Pass-2 procedure)

1. ✅ **R6 research folded in** (§G-R6): workspace-focus discriminator + fallback. Done.
2. ✅ **Adversarially verified** (fresh agent, read-only, cite-or-retract). It falsified two claims now corrected here: **C1** — the focus-only R6 discriminator cuts a live keep-going readout when an autonomous loop runs in your focused window (→ revised to Policy A + a Nima fork); **C2** — ⌃⌘D was marked "raises" but `on_jump_decision` never calls `_raise()` (→ CHANGE). Plus M1 (selection scan must be lock-atomic), M2 (keep the idle predicate, cut only the #65 policy), M3 (R10 `foreground()`-fallback coupling), M4 (SESSION_START path). It confirmed-sound: catch_up genuinely retired, cycle no-raise, nav mapping, R9 satisfy, §9 tool-fidelity gap, R11 total lack, and the focus-watcher plumbing is real + fed. All folded above.
3. Batch the **§I forks** to Nima (R6 ratify + verbosity scope + cycle-onto-muted + catch-up chord).
4. `superpowers:writing-plans` → `superpowers:subagent-driven-development` (TDD, spec as oracle), foundational L's first (frontier model, keep-going selection, persistence), guards green throughout. Deploy (`./bin/sonari install`) is Nima's step.
