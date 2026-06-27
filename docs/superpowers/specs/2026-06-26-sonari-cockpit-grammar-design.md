# Sonari — Eyes-Free Cockpit: Control Grammar Redesign (v1)

- **Date:** 2026-06-26
- **Status:** Design — approved in collaborative brainstorm; ready for implementation planning
- **Scope owner:** Nima
- **Supersedes / dissolves:** the global-pause model (and bug #69 with it); folds in #65 (voice
  follows the speaker — already shipped)
- **Audience:** sonari's users are blind / eyes-free power users (designed for them, not around any
  one person's setup)

## 1. Problem

Sonari's control surface grew one feature at a time. The result: overlapping concepts with no named
model (the trigger was "what's the difference between pause and mute?"), and an **incomplete hotkey
surface** — half the in-the-moment controls (stop, skip, jump-to-decision, reread, rate) were not
reachable by hotkey at all. This is a **ground-up redesign** of the interaction grammar, not a
re-binding of the old controls.

## 2. North star & foundations (locked)

- **Fast expert cockpit** — built for someone who uses it all day and has it in muscle memory:
  velocity, dense memorable bindings, minimal spoken chrome, every action one gesture away.
- **Scales 1 → n smoothly** — one grammar whether you run 1 session or 6; source controls go quiet
  (no-op) at a single session.
- **Snappy / barge-in** — pressing a hotkey never queues behind narration; the latest action always
  wins (see §7).
- **No leader.** The command set landed lean enough that a one-shot leader layer wasn't worth its
  complexity (mode machinery + a hotkeyd rewrite). Everything is a flat global chord; rare settings
  live on the existing slash commands.
- **Modifier:** `⌃⌘` (Ctrl+Cmd) for every hot chord — deliberately avoids VoiceOver's `⌃⌥`.

## 3. Mental model

**One voice you fly, many sessions you tune.** The voice is a transport (stop/seek/scrub); each
Claude session is a "track" with its own stream, its own stop/resume marker, and its own unread
backlog. The voice plays the foreground session's stream (per the per-session-streams architecture);
switching foreground is how you change track. This single model replaces the old global-pause /
per-session-mute / pin lattice.

## 4. The keymap

Bindings are by **physical key position** (macOS keyCodes), so finger positions are identical across
layouts; only a few symbol legends differ on a Norwegian board (shown). All keys are identical on the
Norwegian layout **except rate (+/−)**.

### Sessions
| Action | Intl | Norwegian | Behavior |
|---|---|---|---|
| Stop / start the focused session | ⌃⌘S | ⌃⌘S | Toggle; resumes from the exact item it stopped on (§6.1) |
| Stop **everything** | ⌃⌘M | ⌃⌘M | One-way; bring each session back individually with ⌃⌘S (§6.2) |
| Jump to a waiting session | ⌃⌘J | ⌃⌘J | The session calling you; decisions ranked ahead of prose |
| Cycle next / prev session | ⌃⌘Tab / ⌃⌘⇧Tab | same | Fixed insertion order; complements ⌃⌘J |

### Navigate a session's transcript
| Action | Intl | Norwegian | Behavior |
|---|---|---|---|
| Within a response — prev / next prose | ⌃⌘← / → | same | ← also = "hear that again" (re-reads the prior item) |
| Between responses — older / newer | ⌃⌘↑ / ↓ | same | ⌃⌘↓ to the newest = back to the live edge |
| Jump to the question / decision | ⌃⌘D | same | Semantic landmark jump (replaces the old "go to last item" hack) |

### Answer & status
| Action | Intl | Norwegian | Behavior |
|---|---|---|---|
| Approve a permission | ⌃⌘⏎ | ⌃⌘⏎ | Via the hook channel — no keystroke injection (§6.4) |
| Deny a permission | ⌃⌘⎋ | ⌃⌘⎋ | Same |
| Where am I | ⌃⌘W | ⌃⌘W | Terse: session (spearcon), playing/stopped, waiting count |
| Speed up / slow down | ⌃⌘+ / − | ⌃⌘+ / − | Norwegian: **+** right of 0, **−** right of the period. Continuous (§7) |

### No key — automatic behavior
- **Land on a session → it auto-reads its unread** (unless that session is individually stopped).
- **The voice follows whoever's speaking; nothing steals it** (#65, shipped).
- **A decision pings instantly** (earcon) the moment it appears.

### Settings → existing slash commands (not hotkeys)
`/sonari:verbosity` · `/sonari:voice` · `/sonari:minqueue` · `/sonari:keymap` (the spoken
cheat-sheet).

### Dropped (did not earn their place)
- **Pin** — nothing steals the voice anymore (#65) and switching is manual, so there's nothing to pin
  against.
- **Per-session "mute forever"** — quiet a noisy session by ⌃⌘Tab-to-it then ⌃⌘S (accepted trade-off;
  see deferred gaps §10).
- **A separate "next-prompt" key** — folded into ⌃⌘D.
- **The leader layer** — see §2.

## 5. The reused / re-read mapping (no concept lost)

Every old control is accounted for: pause → ⌃⌘S (per-session); mute / stop → ⌃⌘M (all) + ⌃⌘S (one);
skip → ⌃⌘→ / ⌃⌘D; jump_decision → ⌃⌘D; nav (within/responses) → ⌃⌘ arrows; jump_waiting → ⌃⌘J;
reread_options → ⌃⌘D then ← ; rate → ⌃⌘+/− ; verbosity/voice/minqueue → slash; status → ⌃⌘W;
os_focus → automatic; pin / per-session-mute → dropped.

## 6. Behaviors

### 6.1 Per-session stop / start (⌃⌘S)

Pause is **per-session**, not global. Each session's stream carries its own stopped-state and a
resume marker. ⌃⌘S toggles the **focused** session: stopping holds the current item at the front of
that session's stream (the existing "re-queue interrupted item at front" mechanism) so resume picks up
**from that item's start**. A stopped session stays silent — even when you land on it — until you
⌃⌘S it again. Because pause is per-session, **no global pause exists for a background event to touch
— this dissolves #69.**

### 6.2 Stop everything (⌃⌘M)

Broadcasts "stopped" to every session at once — the master quiet/panic key. **One-way:** sound returns
per-session via ⌃⌘S. After ⌃⌘M, landing on a session does **not** auto-read (it's individually
stopped); you pull each one back with ⌃⌘S.

### 6.3 Navigation — two layers + a landmark

A response contains many proses; the user navigates at two grains plus one semantic jump:
- **⌃⌘← / →** step within the current response (prose to prose); ← re-reads (= "again").
- **⌃⌘↑ / ↓** move between whole responses (older / newer); ⌃⌘↓ to the newest returns to the live
  edge (no separate "jump to live" key).
- **⌃⌘D** jumps straight to the question/decision — a clean semantic destination, replacing the
  "navigate to the last item" workaround the live setup forced today.

### 6.4 Answer (approve / deny) — via the hook, not keystrokes

Keystroke injection is **out** (Secure Event Input silently swallows synthetic keys; wrong-session
approval is an irreversible safety risk — see the 2026-06-26 feasibility verdict). Instead, **permission
prompts** are answered through the channel sonari already owns: a **PreToolUse hook** that returns
`hookSpecificOutput.permissionDecision = allow | deny`. The hook blocks (with a timeout) until the
user presses ⌃⌘⏎ / ⌃⌘⎋, delivered hook↔daemon over the existing IPC. This is SEI-immune, has no focus
race, and **structurally can only answer the session that asked.** Scope: **permissions only**;
AskUserQuestion option-selection and plan approval have no safe channel (see §9).

### 6.5 Where am I (⌃⌘W)

Speaks a terse, on-demand status: the focused session (as a spearcon — its time-compressed folder
name), whether it's playing or stopped, and how many sessions are waiting. Pull, never push.

## 7. Responsiveness — barge-in + resume (the core feel rule)

**Press = instant. A hotkey's effect is never appended behind ongoing narration.**

- **Barge-in:** pressing a hotkey **cuts the current utterance and acts now.** The result (a spoken
  answer, or the new place it takes you) plays immediately at the front of the queue.
- **Resume after an interjection:** for a hotkey that *speaks* while a session is reading (⌃⌘W, a jump
  announcement), after it speaks, reading **resumes from the start of the item that was interrupted** —
  you cut in, hear the answer, and drop back into your place. Mechanism: the interjection is enqueued
  at the front (mute-exempt); the interrupted item is re-queued at the front right behind it (the
  same machinery per-session stop already uses).
- **Continuous-setting exception:** **rate (⌃⌘+/−) does NOT cut** — the change is audible in the very
  next words; cutting would be jarring. That immediacy is its feedback.
- **Always confirm the press fired** (research P7): every accepted hotkey emits an immediate signal —
  the spoken result, the new content, or a short earcon — so a key never feels ignored, even a no-op.

## 8. Sound / confirmation language

Grounded in the prior-art research (OSARA, Emacspeak, NVDA, the Nees & Liebman 2023 meta-analysis):
**abstract earcons are NOT the confirmation backbone** — terse speech beats a large learned earcon
vocabulary. So:

- **Terse, on-demand speech** carries state (⌃⌘W and readouts), pulled not pushed; report only what
  changed.
- **Spearcons** (time-compressed spoken labels) carry the open-ended vocabulary — session names,
  identities — self-labelling, zero recall load.
- **Pitch as a direction channel:** rising = forward / next / yes; falling = back / prev / no.
- **Abstract earcons: a tiny fixed set only** — *waiting* (a background session pinged), *error /
  blocked*, and decision alerts (choice / plan / permission). No big earcon language.
- **Barge-in everywhere** (§7), and **always-confirm-fired** (no silent no-ops).

## 9. Upstream-blocked (one feature request)

*Acting on* Claude — **selecting an AskUserQuestion option** and **interrupting Claude mid-generation**
— both need a channel Claude Code does not expose: hooks can allow/deny a tool but cannot supply a
tool's result, and there is no safe IPC / response API for an interactive session (verified
2026-06-26). Browsing options by ear ships (⌃⌘D then ←/→); the final selection stays in the terminal
until upstream support exists. **Action:** file a Claude Code feature request for a hook (or IPC) that
fires on AskUserQuestion / ExitPlanMode and accepts the chosen option / approval from an external tool.

## 10. Deferred to a later iteration (owner: "enough for first iteration")

Real gaps, intentionally out of v1:
- **Read-verbatim / spell-out** — hear a command / path / hash exactly (to verify before ⌃⌘⏎). The
  highest-value future add for a developer by ear.
- **Permission granularity** — "allow always" vs "allow once."
- **Jump by landmark** — leap to the next *error* or *tool-call* (a return of the "rotor" idea).
- **Silence one session** — a permanent per-session quiet (the dropped "mute forever").

## 11. Collision-vet (build-time, before finalizing)

Confirm against macOS defaults; swap only the offending key (the model is unaffected):
- `⌃⌘Tab` vs app/tab switching.
- `⌃⌘ ←/→/↑/↓` vs Spaces / Mission Control (those are plain `⌃`+arrow — likely clear).
- `⌃⌘ + / −` vs zoom.

## 12. Implementation notes (high-level; the plan will detail)

- **Per-session stop:** replace the global `_paused` Event with a per-session stopped-state on
  `SessionStream`; the speak loop skips a stopped foreground session; ⌃⌘S toggles it; reuse the
  re-queue-at-front resume. Remove the #69 global-pause path.
- **Stop-all:** a broadcast that sets every session stopped.
- **Cycle (Tab/⇧Tab):** new handler over the session roster in insertion order.
- **Two-layer nav + ⌃⌘D:** re-bind existing within-turn / response nav; ⌃⌘D = jump to the next decision
  item in the foreground stream (the `jump_to_decision` queue op already exists).
- **Approve/deny via hook:** a blocking PreToolUse permission hook + hook↔daemon IPC to surface the
  pending decision and receive ⌃⌘⏎ / ⌃⌘⎋; returns `permissionDecision`. New surface — design the IPC
  + timeout carefully; safety-test the "only the asking session" guarantee. Two facts the later plan
  must start from (verified against the code 2026-06-26): (a) the decision must ride a **PreToolUse
  hook's stdout** (`hookSpecificOutput.permissionDecision`) — the `Notification(permission_prompt)`
  event is the *spoken-prompt* channel and cannot carry an answer; (b) the round-trip can reuse the
  existing request/reply transport (`client.send(expect_reply=True)`; the server already replies on
  the same connection), but the connection thread must wait on a **per-decision Condition OUTSIDE the
  daemon lock** — message handlers run under the transaction lock, so blocking there on a keypress
  would freeze the whole daemon.
- **Where am I:** new status interjection (barge-in + resume), spearcon for the session name.
- **Barge-in:** every hotkey handler cancels current speech and acts; interjections enqueue at front +
  re-queue the interrupted item; **rate is the explicit exception** (no cancel).
- **Sound language:** add spearcon synthesis (compressed TTS of the folder name) and pitch-direction
  cues; shrink the earcon set per §8.
- **Drops:** remove `pin_toggle` + pin state and the per-session `mute` handler/state; repurpose ⌃⌘M.
- **Keymap:** rewrite the default keymap/keytables to §4; honor user overrides.

## 13. Testing strategy (TDD)

Daemon-side behavior is unit-testable behind the existing fakes: per-session stop/resume, stop-all
broadcast, auto-read-on-landing, cycle order, two-layer nav + ⌃⌘D landmark, barge-in + interjection
resume (interrupted item re-read), and the approve/deny decision path (mock the hook↔daemon IPC; test
the "only the asking session" guarantee). The macOS hotkey/keystroke layer and the on-hardware feel
get a human acceptance gate (sacrificial setup, never the live install). Collision-vet (§11) is a
build-time check.

## 14. Research grounding

Principles applied (full report 2026-06-26): P1 barge-in (latest action wins), P2 audible state cues /
prefer no persistent modes (we removed the leader), P4 a small composable grammar + jump-to-landmark,
P5 confirmation hierarchy (speech + spearcons + pitch over abstract earcons), P6 pull terse state on
demand (⌃⌘W), P7 always confirm the action fired. Sources: OSARA/Reaper, Vim, Emacs/Emacspeak,
VoiceOver/NVDA/JAWS, tmux, and the auditory-display literature (Nees & Liebman 2023; Gaver; Brewster).

## 15. Sub-project B — resolved bindings, collision-vet & implementation decisions (2026-06-27)

Sub-project A (per-session control core) shipped (PR #70 → main). This section records the
implementation-grain decisions for **sub-project B (navigation & session grammar)**, resolved from a
code-recon + collision-vet pass and one owner decision. The §4 bindings stand; the deltas below are how
they land on the post-A code.

**Collision-vet outcome (macOS defaults, adversarial agent + web check):**
- **⌃⌘+ / ⌃⌘− → clear** (zoom is ⌥⌘+/−, a different modifier). **⌃⌘S → clear** (Finder sidebar is ⌥⌘S).
- **⌃⌘D → collides** with the macOS system **"Look Up / Dictionary"** shortcut. **Owner decision (Nima,
  2026-06-27): KEEP ⌃⌘D.** Rationale: Look Up is a *visual* popover the eyes-free audience does not use;
  Carbon `RegisterEventHotKey` wins the chord (sonari fires correctly); it only shadows Look Up while
  sonari runs; D=Decision is the strongest mnemonic. Accepted cost: a sighted user loses Look Up while
  sonari is running.
- **⌃⌘← / → / ↑ / ↓, ⌃⌘Tab, ⌃⌘⇧Tab, ⌃⌘W, ⌃⌘M → "verify on hardware"**: no documented system binding
  found, but not affirmatively provable-free; ⌃⌘←/→ shadow Xcode's app-level editor back/forward (Carbon
  wins — acceptable). These are cleared at the on-device human-acceptance gate (§13), not a blocker.

**Binding deltas (post-A → B), by physical key position:**
- Within-response nav ⌃⌘← / → — **binding already correct** (nav_prev/nav_next = left/right). ← = "hear
  again" falls out of the existing `prev` (re-reads current/prior item); no handler change.
- Between-response nav — **rebind** from ⌃⌘⇧←/→ to **⌃⌘↑ / ↓** (the handler `_nav_response` is unchanged;
  ⌃⌘↓-to-newest = live edge already works). This frees the shift+arrow chord.
- **Collision to resolve atomically:** ⌃⌘↑/↓ are today `nav_first`/`nav_last` defaults. Those two lose
  their default keys (they remain as actions, just unbound) so ⌃⌘↑/↓ can own between-response nav. Must
  happen in one change or `test_no_two_default_actions_share_a_key` fails on an intermediate state.
- **⌃⌘D** jump-to-decision — add the keymap binding (the `JUMP_DECISION` handler + `jump_to_decision`
  queue op already exist). Plus a 1-line consistency fix: `on_jump_decision` targets `foreground()` today;
  make it `focused_session() or foreground()` to match `on_nav` (so ⌃⌘D acts on the OS-focused session
  like every other nav key).
- **⌃⌘Tab / ⌃⌘⇧Tab** cycle sessions — **new.** One new `MsgType.CYCLE_SESSION` carrying a `direction`
  field ("next"/"prev"); a `SessionManager.session_ids()` roster accessor; an `on_cycle_session` handler
  (mirrors `on_jump_waiting`: pick next/prev in insertion order with wrap, `sessions.focus(target)`,
  cancel, folder cue at_front/mute_exempt/names_session — no terminal-raise). Edge: <2 sessions → an
  "error" earcon (no silent no-op).
- **⌃⌘W** where-am-i — **new, and the one genuinely new mechanism.** A new `MsgType.WHERE_AM_I` + spoken
  handler (NOT the CLI `STATUS` dict path). It must implement the §7 **interjection-resume**: capture the
  in-flight `_current_item` under the lock, `cancel()` (barge-in), enqueue the terse status cue at_front
  (mute_exempt + pause_exempt), then **re-queue the interrupted item at_front behind the cue** so reading
  resumes from its start. NOTE the post-A speak loop only auto-re-queues an interrupted item when the
  session is *stopped*; a non-stopping interjection must re-queue the item explicitly, preserving its
  `pending_heard` entry (so it isn't silently marked unheard/lost). Spoken text (plain for B; spearcon/
  pitch polish is sub-project D): "{folder}. {Playing|Stopped}. {N} waiting." — folder from
  `sessions.folder(fg)`, stopped from the stream, N from a count loop mirroring `_waiting_target`.
- **⌃⌘+ / ⌃⌘−** rate — **bind** the existing `faster`/`slower` actions (no handler change; `on_set_rate`
  already does NOT cancel → rate is the §7 no-cut exception, the "Rate N." cue is its feedback). Add the
  `=`/`−` Carbon keyCodes. Norwegian +/− physical position is **verified at the hardware gate**, not
  assumed — ship the ANSI positions (equal/minus) as the default and flag for on-device confirmation.

**Protocol inventory:** 27 → **29** (`CYCLE_SESSION`, `WHERE_AM_I`; `JUMP_DECISION` already exists). Keep
`assert_complete([...])` + its count comment + `test_daemon_registry` (ALL_27→ALL_29) + `test_protocol`
in sync.

**Out of scope for B** (later sub-projects): the answer-via-hook channel ⌃⌘⏎/⎋ (C); spearcon synthesis +
pitch-direction sound language (D) — B's ⌃⌘W speaks plain terse status.

## 16. Sub-project C — answer-via-hook: verified architecture & implementation decisions (2026-06-27)

A + B shipped (PRs #70/#71 → main @ a862bdc). This section records **sub-project C (answer permission
prompts by hotkey)**, resolved from a code-recon + an **empirical verification pass against a live Claude
Code session**. §6.4's intent stands ("permissions are answered through a hook that blocks for ⌃⌘⏎/⎋");
the mechanism below **corrects this design's earlier recorded facts**, which were made on incomplete info.

### 16.1 Architecture correction (the load-bearing change)
§6.4 and §12 said the decision rides a **PreToolUse** hook's `permissionDecision`. **That is wrong on both
the hook and the schema.** Verified end-to-end in a real interactive Claude Code session (CC v2.1.187/195)
driven under a PTY, observed via files (not the TUI):
- **The correct hook is `PermissionRequest`, not PreToolUse.** PreToolUse fires for *every* tool call (so a
  blocking PreToolUse hook would freeze all normal tool use, and it cannot tell whether a given call would
  even prompt). `PermissionRequest` fires **only when a permission dialog would appear** — proven: it did
  NOT fire for an allowlisted Bash command, and DID fire for a non-allowlisted one.
- **The output schema is `hookSpecificOutput.decision.behavior = "allow" | "deny"`** (with
  `hookEventName="PermissionRequest"`, optional `decision.updatedInput`) — **not** `permissionDecision`
  (that is the PreToolUse field).
- **It can block:** the hook slept **5.0s** (FIRED → RESUME) and Claude Code waited. Hook execution timeout
  is **600s** default, per-hook configurable — a keypress-wait is well within budget.
- **`deny` actually stops the tool** (the command never ran) — fail-closed proven.
- **Returning no decision falls through** to Claude Code's normal permission dialog, and *then*
  `Notification(permission_prompt)` fires.
- **`Notification(permission_prompt)` does NOT fire when PermissionRequest resolves** (allow/deny). So the
  answer-hook must **both speak the prompt and block for the answer** — the old Notification speak-channel
  cannot carry the spoken prompt in the answered case.
- **Payload** carries `session_id`, `cwd`, `permission_mode`, `tool_name`, `tool_input{command,description}`,
  `permission_suggestions` (no `tool_use_id`). `session_id` identifies the asking session — the safety key.

**Division of labor (mutually exclusive in time, no double-speak):**

| Path | Fires | Speaks |
|---|---|---|
| You answer by hotkey | `PermissionRequest` (speaks + blocks + answers) | the new C hook |
| You're away → ~2 min timeout | PermissionRequest returns nothing → dialog → `Notification(permission_prompt)` | the *existing* speak-hook (now the fallback announcer) |

### 16.2 The blocking IPC (outside the daemon lock)
- **Hook** `bin/sonari-hook PermissionRequest`: build a `PERMISSION_REQUEST` message (session, tool, summary)
  → `client.send(msg, expect_reply=True, timeout=~130s)` → **block** for the reply → print the decision JSON
  to stdout, or **print nothing** (fall through). ANY failure (daemon down, send error, socket timeout) →
  print nothing → fall through (graceful degradation to the normal terminal prompt).
- **Daemon `on_permission_request`** (runs UNDER the lock): play the `permission` earcon + enqueue the spoken
  prompt on the **asking** session as a decision item (`is_decision=True`, so ⌃⌘D lands on it); register a
  pending decision `self._pending_decisions[session_id] = {event: threading.Event(), behavior: None}`; return
  an **AWAIT sentinel**.
- **`_handle_message_guarded`** (host.py): after the `with self._state.transaction()` block EXITS (lock
  **released**), if the handler returned the AWAIT sentinel, call `_await_permission_decision(sid, ~120s)`
  which does `event.wait(timeout)` **outside the lock** and returns the reply `{decision: "allow"|"deny"|None}`.
  The daemon wait (~120s) is **strictly shorter** than the client socket timeout (~130s), so a timeout returns
  a fall-through reply BEFORE the socket closes. `server.py` is unchanged (it still just sends what dispatch
  returns — the wait lives in the host's guarded dispatch, on the connection thread, lock-free).
- **`on_answer_permission`** (UNDER the lock, from the ⌃⌘⏎/⎋ hotkey): `target = sessions.focused_session()
  or foreground()`; `pd = self._pending_decisions.get(target)`; if present → set `pd.behavior` + `pd.event.set()`
  + barge-in confirmation ("Approved."/"Denied."); if **absent** → `error` earcon (always-confirm-fired). The
  pending dict is mutated only under the lock; the `Event` is waited on only outside it — no deadlock with the
  speak loop or hotkey thread.

### 16.3 Safety — "only the focused session can be answered" (load-bearing)
A keypress resolves **only the focused session's own** pending decision. Pending decisions are keyed by the
asking `session_id` (== sonari's session key); the answer routes to `focused_session()`'s entry. Focused on a
session with no pending decision → no-op + error earcon, **never** an answer routed to another session. The
permission prompt does **not** auto-steal focus (that would re-introduce a focus-steal race right before a
keypress); the user navigates deliberately (⌃⌘D jumps to the decision, since the prompt is a decision item).
Single-session (the common case): focused == asking, so ⌃⌘⏎/⎋ "just works" with no nav.

### 16.4 Timeout (owner decision, Nima 2026-06-27): **fall through to the terminal**
On the daemon's own-wait expiry (~2 min) the hook returns **no decision** → Claude Code shows its normal
permission prompt for terminal answering (and `Notification` speaks it via the existing fallback). Nothing is
lost; **timeout never auto-allows.** (Verified: return-nothing → dialog appears + Notification fires.)

### 16.5 Bindings, collision-vet, protocol
- **Collision-vet (macOS, adversarial agent + web): ⌃⌘⏎ and ⌃⌘⎋ are both clear.** Force Quit is ⌘⌥⎋
  (Option, not Control); the Sequoia ⌃⏎ contextual menu lacks ⌘; neither chord is in Apple's reserved set;
  Carbon `RegisterEventHotKey` wins the chord. **KEEP both** as specced.
- **Keytables:** add `return`=36 and `escape`=53 to `keytables.py` KEY_CODES + the `hotkeys.py` display map
  ("Return"/"Esc"). Both use the standard ⌃⌘ chord → bind in `_DEFAULT_KEYS`.
- **Keymap actions:** `approve` → `{type: answer_permission, behavior: "allow"}`, `deny` →
  `{type: answer_permission, behavior: "deny"}` (one MsgType + a `behavior` field, mirroring `cycle_session`'s
  `direction`). Defaults: `approve`→"return", `deny`→"escape".
- **Protocol inventory 29 → 31** (`PERMISSION_REQUEST`, `ANSWER_PERMISSION`). Keep `assert_complete([...])` +
  its count comment + `test_daemon_registry` (ALL_29→ALL_31 + fn names) + `test_protocol` (BOTH dicts) in sync.
- **hooks.json:** add a `PermissionRequest` entry (empty matcher = all permission-eligible tools) →
  `${CLAUDE_PLUGIN_ROOT}/bin/sonari-hook PermissionRequest`.

### 16.6 Testing & validation boundary
Daemon + hook + blocking-IPC + safety keying are fully unit-testable behind the existing fakes
(`make_daemon`), including the threaded round-trip (request blocks on one thread; ⌃⌘⏎ answers on another →
reply returned) and the only-focused-session guarantee. The **sacrificial-HOME dogfood** exercises the FULL
round-trip by invoking `bin/sonari-hook PermissionRequest` directly against a real daemon (NOT via
`claude -p`, which bypasses PermissionRequest) and driving ⌃⌘⏎/⎋ over the socket. The **CC↔hook seam** (Claude
Code honors the printed decision and blocks for it) is verified empirically (2026-06-27) + the on-hardware
human gate.

### 16.7 Still owed (spec §9)
File the upstream Claude Code feature request: a hook/IPC that fires on **AskUserQuestion / ExitPlanMode** and
accepts the chosen option / approval from an external tool (PermissionRequest only covers allow/deny on a
tool — option-select and plan approval still have no safe channel). Draft + file as part of C.

**Out of scope for C** (sub-project D): spearcon synthesis + pitch-direction sound language — C uses the
existing earcons + plain spoken confirmations ("Approved."/"Denied.").

## 17. Sub-project D — sound language: resolved design & ear co-design (2026-06-27)

A + B + C shipped (PRs #70/#71/#72 → main @ 403e2a1). This is **sub-project D (sound language)** — the last
cockpit sub-project, implementing §8. Resolved from a recon workflow (sound-layer + feasibility) and an **ear
co-design with Nima** (he auditioned candidates via live `say`/`afplay` and chose). Zero new dependencies —
system `say` + `afplay` only (the same stack the daemon already uses).

### 17.1 Spearcons (time-compressed spoken session names)
Recon empirically confirmed macOS `say` is **pitch-preserving across rate** (F0 is a voice-model property, not
rate-dependent): `say -r 525 -o x.aiff "backend"` → ~0.23s vs ~0.78s at the default 175 WPM (~3.4×), same pitch.
- **Owner choices (by ear):** **rate = 525 WPM (~3.4×)**; **voice = a FIXED crisp voice** (config
  `spearcon_voice`, default "Samantha") so a spearcon reads as a recognizable "system" texture distinct from the
  reading voice. (If the configured voice is unavailable, fall back gracefully — session voice or no `-v`.)
- **Synthesis + cache:** `say -v <spearcon_voice> -r 525 -o <cache>.aiff "<label>"`, cached at
  `~/.sonari/spearcons/`, key = `sha256(voice|rate|label)[:16]` (avoids path injection from arbitrary names).
  **Pre-generate in the background** (`subprocess.Popen`, non-blocking) on SessionStart for known labels; generate
  on first need for a new label. Cleanup orphaned/stale files at daemon start. Generation **never** on the hot path.
- **Playback = queue-integrated** (NOT bare fire-and-forget), so the cue keeps its at_front ordering,
  `names_session` attribution-suppression, and barge-in: the standalone name cue remains a queued at_front item
  but carries an `audio_path`; the speak loop plays it via `afplay` (with the SAME `cancel_epoch` barge-in as
  `say`) instead of `say`. Generalize `Speaker.speak(text=None, audio_path=None, cancel_epoch=...)` — `audio_path`
  set → afplay that file; else say `text`. The afplay proc is the tracked `_current` proc, so `cancel()` interrupts
  it identically.
- **Scope:** spearcons replace the **standalone "which session" name cues** — `on_cycle_session` (⌃⌘Tab),
  `on_jump_waiting`, `on_jump_decision`'s folder cue (⌃⌘D), `on_where_am_i`'s folder part (⌃⌘W), and `on_nav`'s
  crossed-session folder cue. **The `_attributed_text` prefix** ("{folder}. {content}", spoken when the voice
  switches sessions mid-content) **stays full speech** — clarity before content outweighs speed there (controller
  merit call; flip later if wanted). Long-name truncation (first word, or ~12 chars) is a sensible default,
  ear-tunable at the live gate.

### 17.2 Pitch-direction cues
Two pre-baked WAV assets (Python `aifc` was removed in 3.13 → WAV, not AIFF), played fire-and-forget via `afplay`.
- **Owner choice (by ear): Set A — rising `pitch_up` 440→880 Hz, falling `pitch_down` 880→440 Hz, 200 ms** linear
  chirp, 5 ms cosine fades, 44100 Hz / 16-bit mono.
- **Assets + generator:** a committed `scripts/gen_pitch_tones.py` (stdlib `wave`+`struct`+`math`, no deps)
  produces `src/sonari/assets/pitch_up.wav` + `pitch_down.wav` (both committed).
- **Playback path:** resolve the asset path from the package (`importlib.resources` / `Path(__file__).parent`)
  and `afplay` it **directly** — NOT through the configurable earcons dict (`bootstrap.py` merges earcons with a
  whole-key guard, so new keys would silently no-op for any existing user who already has an `earcons` config key).
  Pitch cues are inherent to the direction grammar, not user-tunable, so direct asset playback is correct.
- **Directional call sites (4):** `on_cycle_session` (next→up / prev→down), `on_nav` within-response (next→up /
  prev→down; `first`/`last` get none — unbound per §15), `on_nav_response` between-responses (next_response→up /
  prev_response→down), `on_answer_permission` (allow→up / deny→down). **Composition:** the chirp fires first
  (establishes direction), the spearcon/spoken content follows (names the destination). Non-directional events
  (jump_decision, jump_waiting, where-am-i, stop_session/all) get **no** chirp.

### 17.3 Earcon set — shrink (§8) with one owner deviation
§8 keeps only `waiting` / `error` (blocked) / decision-alerts (`choice`/`plan`/`permission`). Applied:
- **DROP `ready`** (Glass, on `Notification` `idle_prompt`) — not a decision alert; the next prompt is its own cue.
  Remove it from the earcon map + the `idle_prompt` branch.
- **KEEP `turn_done`** (Tink, end-of-turn) — **owner decision (Nima 2026-06-27)**: a genuinely useful eyes-free
  turn-boundary marker. This deviates from §8's literal 3-category set; owner-approved. (Its prose-flush in
  `prose.py` rides the protocol message, independent of the earcon, so keeping the earcon is orthogonal.)
- Keep `waiting`, `error`, `choice`, `plan`, `permission`.

### 17.4 Testing & validation boundary
Daemon-side is fully unit-testable behind the fakes: spearcon cache-key + generation invocation (mocked `say`),
the queue-integrated `audio_path` playback path + its barge-in (cancel_epoch), the chirp dispatch at the 4
directional sites, and the earcon-shrink. The 2 permanent concurrency guards + barge-in tests must stay green
(the speak-loop `audio_path` change is concurrency-sensitive). The **sacrificial-HOME dogfood** exercises real
`say -r`/`afplay` generation + playback. The **sound itself** (does 525 feel right while cycling; chirp character;
long-name intelligibility) is Nima's **on-hardware listening gate** — candidates were auditioned during the
co-design; the live gate confirms them in context.

**Out of scope for D:** per-session distinct pitch offsets for similar-sounding names (defer — needs an
AVFoundation helper); afplay `-r` time-compression (unverified pitch behavior — `say -r` is the chosen path).
