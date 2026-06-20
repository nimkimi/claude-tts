# Sonari — OS Keyboard-Focus Follow on Session Jump

- **Date:** 2026-06-20
- **Status:** Design — awaiting review
- **Scope owner:** Nima
- **Builds on:** the per-session-streams campaign (PR #60, `88a570f`). `jump_waiting`
  today moves only the *voice* (`sessions.focus(target)` in the `JUMP_WAITING`
  handler); OS keyboard focus does not follow.

## 1. Problem

`jump_waiting` (`Ctrl+Cmd+J`) switches the spoken voice to a waiting background
session, but the target session's terminal window stays in the background. An
eyes-free user hears "Jumping to backend." and then has to *find and raise that
window by hand* before they can type a reply. Sonari's purpose is to let the user
act on what they hear; a voice-only jump strands them at the keyboard. This is
market-driven product work for Sonari's accessibility audience — not a personal
accommodation.

## 2. Goal & scope

When the user jumps to a waiting session, bring that session's **terminal
window/tab to the foreground with keyboard focus**, so they can type immediately.

- **In scope:** macOS, Apple Terminal.app (primary) and iTerm2. The `jump_waiting`
  action only. A config switch. A permission/doctor flow. Graceful voice-only
  fallback with a spoken cue.
- **Out of scope (now):** other terminals (kitty/WezTerm/Ghostty/Alacritty),
  tmux, Windows, and triggering focus-follow on anything other than `jump_waiting`
  (no auto-follow of background prompts). All are extension points, not built.

## 3. Viability findings (empirically established on the target box)

Everything below was **proven on Nima's machine** (macOS 26.5.1 Tahoe, Darwin
25.5.0) with throwaway probes that mirrored Sonari's production process context
(launchd LaunchAgent, `.accessory`, real Carbon global hotkey + a Python-under-
launchd raiser). These are the load-bearing facts the design rests on:

1. **The Cocoa path is dead on Tahoe.** `NSRunningApplication.activate(...)` from a
   background process returns `true` but does **not** raise the window (Apple's
   anti-focus-stealing change). Do not use it.
2. **The AppleScript path works.** `tell application "<Terminal>" to activate`
   raises the app, because the app activates *itself* (cooperative model), which
   the OS permits even from a background process. Reproduced across multiple
   presses, frontmost flipped from a non-terminal app (Slack/Outlook) to the
   terminal.
3. **A specific background window/tab can be raised** by matching the session's
   **tty** and running: `set selected of <tab> to true` → `set index of <window>
   to 1` → `activate`. **Do NOT** use `set frontmost of <window> to true` — it
   silently *reverts* the raise. Skip phantom windows with a `visible and (count
   of tabs) > 0` guard (Terminal keeps invisible 0-tab windows that throw `-10000`).
4. **The physical keypress is irrelevant to the raise.** The production raise ran
   from a Python LaunchAgent fired by a trigger file (no keypress) and still
   worked. So the *relay* topology is safe: hotkeyd catches the key → signals
   speechd → **speechd** performs the raise. The raiser need not be the
   key-receiver.
5. **Automation (TCC) permission is required** for the controlling AppleScript
   (reading `tty of tab`, `set selected`, `set index`). A one-time consent grant
   makes it work and persists. `activate` alone is consent-exempt but cannot pick
   a specific window.
6. **tty is the only join key for Terminal.app.** `TERM_SESSION_ID` has no
   scriptable handle; iTerm2's `ITERM_SESSION_ID` *is* a usable handle.
7. **The session tty is capturable at SessionStart.** The hook process has no
   controlling tty itself (`/dev/tty` → ENXIO), but its process ancestry includes
   the `claude` process, which carries the tab's real tty. `ps -o tty= -p
   <ancestor>` yields e.g. `ttys005`; normalize to `/dev/ttys005`. This matches
   what Terminal reports as `tty of tab`.

**Still unverified (flagged, not assumed):** that iTerm2's `iterm2:///reveal` URL
actually raises from a background process on Tahoe (it relies on `open`/
LaunchServices delivering to iTerm2; my spike isolated AppleScript self-activation,
not the URL path). This gets a one-press build-time confirmation (Task in the
plan), with the proven AppleScript path as the fallback.

## 4. Architecture

Four units, each independently testable behind a seam:

```
SessionStart hook ──(SESSION_START + identity fields)──▶ daemon
                                                          │ stores on
                                                          ▼
                                                   SessionManager
                                                   (per-session identity)
JUMP_WAITING (Ctrl+Cmd+J)
   │ daemon handler:
   │   sessions.focus(target)        (voice — unchanged)
   │   enqueue preamble cue          (voice — adjusted)
   └─► RaiseService.raise_async(identity)   (NEW, off the speak thread)
            │ dispatch by terminal type
            ├─ Apple_Terminal → exec ~/.sonari/sonari-raise <tty>   (helper; AppleScript; needs grant)
            ├─ iTerm.app      → open "iterm2:///reveal?sessionid=<id>"  (no grant)
            └─ other/none     → return False  → spoken "bring it forward" cue
```

### 4.1 Identity capture (SessionStart)

- **`hooks_entry.handle_event` (SessionStart branch):** add three fields to the
  emitted `SESSION_START` message:
  - `term_program`: `os.environ.get("TERM_PROGRAM", "")` (e.g. `Apple_Terminal`,
    `iTerm.app`).
  - `iterm_session_id`: `os.environ.get("ITERM_SESSION_ID", "")`.
  - `tty`: derived (below). Empty string when not derivable.
- **tty derivation** — a new small, testable function (e.g. `sonari.ttyutil
  .controlling_tty()`): walk the process ancestry (`os.getppid()` then
  `ps -o ppid=,tty= -p <pid>`) returning the first ancestor whose tty is a real
  device (not `??`); normalize `ttysNNN` → `/dev/ttysNNN`. Returns `""` on failure.
  The `ps` call is the only I/O; isolate it so the parsing logic is unit-tested
  with canned `ps` output, and the whole function degrades to `""` (never raises).
- The hook runs inside the Claude Code process, so `os.environ` reflects the user's
  terminal. Capture is best-effort: any missing field is `""`, which the raise
  path treats as "can't follow → voice-only fallback".

### 4.2 SessionManager — store identity

- `SessionManager` currently maps `session_id → folder`. Add a parallel per-session
  identity record (a small dataclass or dict): `{term_program, tty,
  iterm_session_id}`, set from the `SESSION_START` handler.
- New accessor `identity(session) -> Identity | None`.
- `_record`/`register`/`unregister` keep identity in lockstep with the existing
  folder map (clear on unregister). Identity is updated only with non-empty values
  (same "don't clobber with empties" rule as `folder`).

### 4.3 RaiseService + RaiseBackend (platform seam)

- A portable `RaiseService` (core) that the daemon calls. It owns: the enabled/
  disabled check, dispatch by `term_program`, async execution, and translating the
  result into success/failure for the cue.
- A `RaiseBackend` abstraction in `platform/base.py` (alongside the existing
  backends), selected by `get_platform()`:
  - `raise_session(identity) -> bool` — perform the raise; return True on a
    confirmed raise, False otherwise (unsupported terminal, missing identity,
    helper failure, permission denied). Must be safe to call off the main thread.
  - `doctor_rows() -> list` — Automation-grant / helper-built status rows.
- **macOS `MacRaiseBackend`:**
  - `Apple_Terminal` → exec the `sonari-raise` helper (4.4) with the tty; success =
    exit code 0.
  - `iTerm.app` → `subprocess` `open "iterm2:///reveal?sessionid=<iterm_session_id>"`;
    success = `open` exit 0 (with the build-time caveat in §3).
  - anything else, or empty identity → `False`.
- **No-op default backend** (Linux/Windows/tests): `raise_session` returns `False`
  (→ fallback cue), `doctor_rows` empty. Keeps the core import-clean and the
  feature inert where unsupported.

### 4.4 The `sonari-raise` helper (clean, recognizable grant)

Per the decision to avoid granting Automation control to the shared
`/usr/bin/osascript`, ship a dedicated helper so the consent dialog and the
System-Settings entry read as Sonari's, and the grant is narrow.

- A small Swift binary, built with `swiftc` exactly like `sonari-hotkeyd`
  (reuse the build pattern in `MacHotkeyBackend.build()`): source at
  `hotkeyd/sonari-raise.swift` (or a sibling dir), compiled to
  `~/.sonari/sonari-raise`. **Do not rebuild if the source hash is unchanged**
  (preserve the Automation grant across reinstalls — same `.srchash` trick
  hotkeyd uses).
- Interface:
  - `sonari-raise <tty>` — run the proven AppleScript recipe (find the visible,
    non-empty Terminal window whose selected tab's `tty` equals `<tty>`;
    `set selected` + `set index … to 1` + `activate`; verify the front window's tty
    now equals the target). Exit `0` on MATCH, non-zero otherwise.
  - `sonari-raise --check` — send **one harmless controlling Apple Event** to
    Terminal (e.g. read `count of windows`) purely to exercise the Automation gate.
    Exit `0` = grant in place; a distinct non-zero code = denied/not-yet-granted
    (`-1743`); other non-zero = Terminal-not-running/other. This is how `doctor`
    detects the grant indirectly (TCC is unreadable from outside) **and** how the
    proactive first-run flow (§4.7) triggers the consent dialog at a controlled
    moment.
  - Errors are swallowed into a non-zero exit (never hangs; the daemon also caps
    every invocation with a subprocess timeout).
- Built and its existence/permission surfaced by `sonari install` / `doctor`,
  consistent with hotkeyd. Absence (no swiftc) is non-fatal — the feature degrades
  to the voice-only fallback, like hotkeyd degrades global hotkeys.

### 4.5 Daemon wiring (`JUMP_WAITING` handler)

Today (unchanged parts kept): on `JUMP_WAITING`, find the target, `sessions.focus
(target)`, `speaker.cancel()`, enqueue the spoken preamble at front.

Add:
- **Synchronous cue decision:** ask the `RaiseService` whether a raise will be
  *attempted* for the target (enabled + identity present + supported terminal). If
  yes, keep the preamble "Jumping to {folder}."; if no, use "Jumping to {folder} —
  bring it forward to type." (so an eyes-free user knows focus won't follow).
- **Async raise:** after enqueuing the preamble, dispatch
  `RaiseService.raise_async(identity)` on a short-lived daemon thread (or a small
  single-worker executor) so the message handler returns immediately and the speak
  loop is never blocked by the ~0.4 s osascript/open call. Runtime performance is a
  hard constraint — the raise never runs on the speak thread or under a held lock
  for its full duration.
- **Stale-raise supersession (required, not optional).** The raise is slow and
  async, so a later jump can be issued before an earlier raise lands. Guard with a
  monotonic **jump generation** counter (the cancel-epoch pattern from the
  per-session-streams campaign): the `JUMP_WAITING` handler increments the
  generation under the daemon lock and passes the value into `raise_async`; the
  raise thread, just before applying focus (and again before the failure follow-up
  cue), checks it is still the latest generation and **no-ops if superseded**. This
  prevents the two desyncs this feature exists to avoid: (a) double-jump A→B where
  `raise(A)` completes after `raise(B)` and steals focus back to A while the voice
  is on B; (b) a single slow raise yanking the window forward after the user has
  already moved on. Voice and OS focus must never diverge.
- **Async-failure follow-up cue:** if a raise that was *attempted* returns False,
  the raise thread enqueues a follow-up "Bring {folder} forward to type." It must
  acquire the daemon lock the same way other enqueues do; this is the one genuine
  cross-thread enqueue and gets concurrency review.

### 4.6 Config

- Add `DEFAULTS["focus_follow"] = True`. When False, `RaiseService` never attempts
  a raise and the preamble stays the plain "Jumping to {folder}." (no nagging cue —
  the user opted out deliberately).
- `tests/test_config.py` asserts the exact DEFAULTS key-set — **add `focus_follow`
  there** (this exact-set test broke in Stage 5 and Stage 7 for the same reason;
  the plan must include it).

### 4.7 Permission / doctor UX (proactive — the grant cannot wait for first jump)

The naive "first jump triggers the prompt" flow **fails for an eyes-free user**: a
TCC dialog pops silently over some window, the user never sees it, and the daemon's
subprocess timeout kills the helper before they could click — so the grant never
saves, and focus-follow stays silently dark (and re-darkens whenever a `swiftc`
rebuild changes the helper's cdhash and drops the grant). The grant must be
acquired *deliberately, with spoken guidance*, and its state *actively detected*.

- **Active detection via `sonari-raise --check`** (§4.4): both `doctor` and the
  install flow call it to learn the real grant state — TCC is unreadable directly,
  so this controlled harmless Apple Event is the signal. This also auto-detects the
  cdhash-rebuild grant drop (the check starts failing) instead of leaving the user
  to discover focus-follow broke.
- **Proactive grant during `install` / `doctor`:** when `--check` reports
  not-granted and `focus_follow` is on, Sonari **speaks** the instruction *before*
  triggering the dialog (the established `afplay Glass.aiff` + spoken-cue "asking
  for hands" pattern): e.g. "Focus-follow needs permission. A dialog will appear —
  click Allow to let Sonari raise your terminal window." Then invoke `sonari-raise
  --check` once more to surface the dialog at this known moment (no subprocess
  timeout racing the user — the install/doctor invocation waits).
- **`doctor` rows** from `RaiseBackend.doctor_rows()`:
  - helper built? (`~/.sonari/sonari-raise` present / `swiftc` available)
  - Automation grant (from `--check`): granted / **not granted — run the prompt
    flow** / Terminal-not-running. Reported actionably (same honesty pattern as the
    Windows hotkey doctor row), never asserting a state we can't observe.

## 5. Error handling & fallback

The voice jump is the floor and must never break. Every raise failure mode →
voice-only + the spoken "bring it forward" cue, never a crash, never a hang:

- missing/empty identity (capture failed, unknown terminal) → `False` synchronously
  → cue.
- unsupported `term_program` → `False` → cue.
- helper missing / not built → `False` → cue.
- helper non-zero / timeout / target window closed / permission denied → `False`
  from the async path → follow-up cue.
- iTerm2 reveal URL fails → `False` → cue (and the build-time check decides whether
  to switch iTerm2 to the helper-AppleScript path).

## 6. Testing strategy

The OS-side raise can't be unit-tested (it moves real windows); everything around
it can, via the `RaiseBackend` seam. TDD applies to all of the latter.

- **tty derivation:** unit-test the ancestry/normalization parsing with canned
  `ps` output (real device vs `??` vs missing); confirm `""` on failure.
- **hook:** `handle_event` SessionStart returns the three new fields from a faked
  env + a faked tty function.
- **SessionManager:** identity stored/updated/cleared in lockstep with folder; the
  "don't clobber with empties" rule.
- **RaiseService dispatch:** with a fake backend, assert correct strategy selection
  by `term_program`, the enabled/disabled gate, and result→cue mapping.
- **Daemon wiring (fake `RaiseBackend`):** `JUMP_WAITING` calls `raise_async` with
  the target's identity exactly once, only when enabled; the call is dispatched off
  the speak thread (assert it doesn't block / runs on another thread); the preamble
  text matches the synchronous attempt-decision; a failed attempt enqueues the
  follow-up cue (thread-safely).
- **Supersession:** with a controllable fake backend (gate the raise on an event),
  a second `JUMP_WAITING` before the first raise applies → the first raise no-ops
  (focus/cue land only for the latest generation). Drive it deterministically; this
  is the concurrency-critical test and gets opus review.
- **Grant detection:** `RaiseService`/doctor maps `sonari-raise --check` exit codes
  (granted / denied / terminal-absent) to the right doctor row and the right
  proactive-prompt decision, with the helper exec faked.
- **Config:** `focus_follow` default present (exact key-set test updated); toggling
  it suppresses attempts.
- **Empirical, build-time (documented, not CI) — DONE, see §8:** the Terminal.app
  recipe was re-confirmed via the shipped helper; the iTerm2 reveal-URL was tested and
  found broken on Tahoe (wrong session) and replaced by the verified `--iterm`
  AppleScript path (confirmed against a real captured `ITERM_SESSION_ID`). All
  self-verifying via osascript readback (not Nima-as-harness).

## 7. Decisions (resolved in brainstorm)

- **Terminals:** Terminal.app + iTerm2 now; abstraction open for more later.
- **Trigger:** `jump_waiting` only. No auto-follow of background prompts.
- **Switch:** `focus_follow` config, default ON.
- **AppleScript execution:** dedicated `sonari-raise` Swift helper for a clean,
  recognizable, narrow Automation grant (not shared `osascript`).
- **iTerm2 mechanism:** ~~`iterm2:///reveal` URL (no grant) primary~~ — **REVISED at build
  time (Task 11):** the reveal URL is BROKEN on Tahoe (activates iTerm2 but lands on the
  wrong session). Shipped path is the **helper-AppleScript fallback** (`sonari-raise --iterm
  <id>`: strip `wNtNpN:` → match bare GUID → select session/tab + `set index of window to 1`
  + `activate`, never window-select/`set frontmost`, `delay 0.8`). Needs an Automation grant
  like Terminal (surfaced via `--check-iterm`, targeted by `TERM_PROGRAM`).
- **Fallback:** always keep the voice jump; add a spoken "bring it forward" cue
  when focus can't/ won't follow.
- **Join key:** tty for Terminal.app; `ITERM_SESSION_ID` for iTerm2.
- **Raiser location:** speechd (relay-safe; proven keypress-independent).

## 8. Build-time verification results (Task 11 — RESOLVED 2026-06-20 on Nima's Tahoe box)

Verified end-to-end through the **shipped `sonari-raise` binary** (built from this branch into
`~/.sonari/sonari-raise`), each self-verified via independent osascript readback:

- **Terminal.app `<tty>` raise — ✅ PASS.** Raised a background window forward from among 6 open
  Terminal windows; correct front tty after; bogus tty → NOTFOUND (exit 1). The proven recipe
  holds through the shipped helper.
- **iTerm2 `iterm2:///reveal?sessionid=` URL — ❌ BROKEN on Tahoe.** Brings iTerm2 to the front
  but stays on the *previously-active* session (would land an eyes-free user in the wrong
  session). Abandoned.
- **iTerm2 `--iterm <id>` AppleScript fallback — ✅ PASS (shipped path).** All 3 sessions raised
  correctly (cross-window, cross-tab, and back); `wNtNpN:` prefix-stripping confirmed; unknown
  id → NOTFOUND (exit 1). Recipe = select session/tab + `set index of window to 1` + `activate`
  (no window-select/`set frontmost`; `delay 0.8` required — 0.3 gave a false MISS).
- **Automation grants — ✅ confirmed.** `sonari-raise --check` (Terminal) and `--check-iterm`
  (iTerm2) both returned granted (exit 0); the consent flow works for the unsigned helper. The
  grant is per target-app; install/doctor target it by `TERM_PROGRAM`. (Exact displayed consent
  string not separately transcribed; doctor copy points users to System Settings → Privacy &
  Security → Automation → `sonari-raise`.)

### Still deferred
- **Other terminals / tmux / Windows** — extension points; not built.
- **Multi-window-per-session / tab disambiguation beyond tty** — tty already
  resolves the tab; no further work needed for the supported terminals.
- **Re-pin on jump?** Out of scope — `focus()` semantics unchanged.

## 9. Cross-platform note

The `RaiseBackend` seam mirrors the existing per-platform backend pattern. A future
Windows backend (`SetForegroundWindow` + window identity from the win port) plugs
in without touching the core or the daemon wiring. macOS only for this spec.
