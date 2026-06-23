# Sonari — Focus-Aware Per-Session Navigation

- **Date:** 2026-06-23
- **Status:** Design — awaiting review
- **Scope owner:** Nima
- **Builds on:**
  - The per-session-streams campaign (per-session history, nav cursor, and heard-marker already exist and are correct).
  - `2026-06-20-sonari-focus-follow-design.md` — the *outbound* focus model (voice jump → raise window via the `sonari-raise` helper). This spec adds the **inbound** direction, which that spec explicitly left out of scope (§2: "no auto-follow… triggering focus-follow on anything other than `jump_waiting`").

## 1. Problem

With two Claude sessions running in two terminal windows, the arrow-key
navigation hotkeys (`nav_next/prev/first/last`, `nav_prev_response/next_response`)
navigate **the wrong session**. The user focuses session A's window to review it,
presses an arrow, and hears session **B** — because navigation routes to "the last
session that submitted a prompt," not "the terminal I'm looking at."

### 1.1 Root cause (confirmed)

`on_nav` (`daemon/features/navigation.py:103`) resolves its target via
`ctx.host.sessions.foreground()`. `foreground()` (`sessions.py:46-49`) returns the
*pinned* session, else `_foreground`. `_foreground` is set **only** by
`SET_FOREGROUND`, which the hooks emit on **`UserPromptSubmit`** and
**`SessionStart`** (`hooks_entry.py:94, 101`) — i.e. prompt-submit / session-start
/ jump-to-waiting / pin. **Nothing in sonari ever reads the OS-focused terminal
window.** A whole-package grep for `frontmost`/`NSWorkspace`/`CGWindow`/
`AXUIElement` finds no read path; the focus-follow machinery only *pushes* focus
(raises a window), never reads it. So focusing a window without typing leaves
`_foreground` stale, and the arrows act on whichever session last prompted.

This is a **missing feature, not a regression**: `git log` shows `on_nav` has
always used `foreground()`; OS-focus-driven nav was never built.

### 1.2 What already works (do not touch)

- **Per-session history**: `SessionHistory` keys every map by `session`
  (`history.py:30-33`). No cross-session bleed.
- **Per-session spoken-marker**: the heard-marker is `HistoryEntry.heard`, per
  entry, per session; `nav_cursor`/`nav_turn` live on the per-session
  `SessionStream` (`session_stream.py:19-20`).

The bug is **purely which session the arrows select**. This spec changes only
that selection (and the minimal voice/cue consequences of it).

## 2. Goal & scope

When the user has focused a terminal window running a Claude session and presses a
navigation hotkey, navigate **that focused session's** transcript and read it
aloud.

- **In scope:** macOS, Apple Terminal.app (primary) and iTerm2. The **navigation
  hotkeys only**. A background focus-watcher that tells the daemon which session is
  OS-focused. Graceful fallback to today's `foreground()` behavior whenever focus
  is unknown/unmappable.
- **Out of scope (now):**
  - Making **any non-navigation hotkey** follow OS focus (pause/cancel/mute/
    decisions/pin/jump all keep `foreground()`). *(Decision: "navigation follows
    focus; voice/controls stay.")*
  - Making the voice follow *mere window focus* (focusing a window without pressing
    an arrow must NOT move the voice — see §4.4).
  - Other terminals (kitty/WezTerm/Ghostty/Alacritty), tmux, ssh, Windows. These
    fall back to `foreground()` and are extension points, not built.

## 3. Decisions (resolved in brainstorm)

- **Symptom confirmed:** wrong-session nav happens when reading a session that
  wasn't the last to prompt (not a foreground-update bug, not history bleed).
- **Scope:** navigation follows OS focus; voice and all other hotkeys keep
  following prompts/`foreground()`.
- **Detection mechanism:** a **background focus-watcher** (no per-keypress latency),
  using **polling** gated to "a supported terminal is frontmost." Polling — not the
  Accessibility API — because it catches window **and** tab switches uniformly and
  needs no Accessibility ("control your computer") grant.
- **Permission:** the Apple Event that reads the front tab's tty needs the
  **Automation** grant, which is keyed to the **requesting binary**. Reuse
  `sonari-raise`'s existing grant by adding a read subcommand to it, rather than
  granting a second binary. (Rebuilding `sonari-raise` triggers **one** re-grant of
  the *existing* Automation permission at install — not a new permission type.)
- **Voice on nav:** pressing an arrow while focused on a different session moves the
  voice to that session (so its transcript is audible) and leads with a short folder
  cue. Merely focusing a window does not. Cross-session nav overrides a pin (same as
  jump-to-waiting); within-session nav preserves it.

## 4. Architecture

Five units behind clear seams. The entire daemon-side path is unit-testable
**without** the Swift watcher — the watcher only produces `os_focus` messages.

```
NSWorkspace frontmost app   ──(cheap, no grant)──┐
  (terminal? yes/no)                              │   sonari-hotkeyd
poll ~500ms while terminal frontmost              │   (focus-watcher, NEW)
  └─ exec sonari-raise --front-tty / --front-iterm│   (Apple Event read,
        (reuses sonari-raise's Automation grant)  │    reused grant)
  on CHANGE ──► send os_focus {term_program, tty, iterm_session_id}
              or os_focus {focused:false}  ───────┘
                         │ daemon
                         ▼
              SessionManager.set_os_focus(identity)
                 resolve identity → live session (tty / iterm id match)
                 store _os_focused_session  (None if no match / not focused)
                         │
NAV hotkey ──► on_nav:  target = focused_session() or foreground()
                 if target != foreground(): sessions.focus(target)   # move voice
                 if cross-session: lead with "<folder>." cue
                 _nav / _nav_response(ctx, target, to)               # unchanged
```

### 4.1 Focus-watcher (in `sonari-hotkeyd.swift`)

The watcher lives **inside the existing `sonari-hotkeyd`** `.accessory`
NSApplication — no new process or LaunchAgent, and navigation cannot happen
without hotkeyd anyway. It adds:

- An `NSWorkspace.didActivateApplicationNotification` observer + a repeating
  `Timer` (~500 ms, tunable). On each tick:
  1. Read `NSWorkspace.shared.frontmostApplication?.bundleIdentifier` — **free, no
     grant**. Map bundle id → `term_program` (`com.apple.Terminal` →
     `Apple_Terminal`, `com.googlecode.iterm2` → `iTerm.app`).
  2. If the frontmost app is **not** a supported terminal: if the last-sent state
     wasn't already "none", send `os_focus {focused:false}` once. Do **not** read
     any Apple Event. (So zero Apple-Event cost while in a browser/editor.)
  3. If it **is** a supported terminal: `exec ~/.sonari/sonari-raise --front-tty`
     (Terminal) or `--front-iterm` (iTerm2) to read the front tab's identity.
- **Change-detection:** keep the last identity sent; only `sendMessage(os_focus …)`
  when it changes. Switching windows/tabs changes the front tab's tty → a fresh
  `os_focus`; idle in the same tab → no traffic. (The first tick after watcher start
  always sends, since "last sent" begins nil. A **daemon** restart while the watcher
  keeps running leaves the daemon's `_os_focused_session` `None` until the next focus
  change — covered by the §5 fallback to `foreground()`; re-asserting focus on socket
  reconnect is an optional refinement, not required.)
- Reuses the existing `sendMessage` (token + newline-JSON over the daemon's
  localhost-TCP listener). Best-effort; errors ignored, like hotkeys.

**Why polling, not Accessibility AX:** switching between two windows (or two tabs)
of the **same** Terminal.app does not fire any `NSWorkspace` notification (same
app), so app-activation alone is insufficient. `kAXFocusedWindowChangedNotification`
would catch window switches but **needs the Accessibility grant** and still
wouldn't reliably catch **tab** switches within one window. Polling the front
tab's tty handles windows and tabs identically and needs no new permission type.

**Runtime cost (for the hard-constraint review):** while a supported terminal is
frontmost, the watcher spawns one short `sonari-raise --front-tty` process per
poll (~2/sec at 500 ms). It is **off every hot path** — not the keystroke path, not
the speak loop — and produces zero Apple Events while you're not in a terminal. If
measured cost ever matters, a future optimization is an in-process read (which would
require `sonari-hotkeyd` to hold its own Automation grant); deferred under YAGNI.

### 4.2 `sonari-raise` read subcommands (NEW)

Add two read-only modes to `hotkeyd/sonari-raise.swift`, reusing `runAppleScript`:

- `sonari-raise --front-tty` → `tell application "Terminal" to get tty of selected
  tab of front window`; print the tty (e.g. `/dev/ttys003`) to stdout, exit 0.
  Guard against phantom windows (`visible and (count of tabs) > 0`, per the
  focus-follow spec §3.3); print nothing + non-zero if there's no real front window.
- `sonari-raise --front-iterm` → `tell application "iTerm2" to get id of current
  session of current tab of current window`; print the bare GUID, exit 0.
- Both: any AppleScript error → non-zero exit, no stdout (never hangs; the watcher
  also caps the exec with a timeout).

Adding these changes `sonari-raise`'s source → cdhash → **the existing Automation
grant is dropped and must be re-granted once**. Surface this at `install` with the
established spoken-guidance pattern (and `afplay Glass.aiff`, since it asks for the
user's hands): one line — "the first navigation into a Terminal/iTerm2 window will
ask to re-allow `sonari-raise` to control it; click Allow (same one-time grant)."
Preserve the `.srchash` "don't rebuild if unchanged" trick so it isn't dropped
again on every reinstall.

### 4.3 Protocol + daemon handler

- **Protocol** (`protocol.py`): add `OS_FOCUS = "os_focus"`. Payload is **not** tied
  to `ctx.session`; it carries the front window's identity:
  `{type:"os_focus", term_program, tty, iterm_session_id}` or
  `{type:"os_focus", focused:false}`.
- **Handler** `on_os_focus` (in `daemon/features/focus.py`): call
  `ctx.host.sessions.set_os_focus(...)` with the payload. No speaking, no side
  effects beyond updating the resolved focused session.

### 4.4 `SessionManager` — resolve & store OS focus

- New field `_os_focused_session: str | None = None`.
- `set_os_focus(term_program="", tty="", iterm_session_id="", focused=True)`:
  - `focused=False` (or all identity fields empty) → `_os_focused_session = None`.
  - Else resolve against the captured `_identities`:
    - `Apple_Terminal` → first live session whose `Identity.tty == tty`
      (**non-empty** equality only — an empty incoming tty never matches; mirrors
      the existing "don't clobber with empties" rule).
    - `iTerm.app` → first live session whose `Identity.iterm_session_id`'s bare
      GUID matches.
  - No match (front terminal isn't a registered Claude session) →
    `_os_focused_session = None`.
- `focused_session() -> str | None`: return `_os_focused_session` **iff** it is
  still a registered session (`in self._sessions`), else `None`.
- `unregister(session)`: also clear `_os_focused_session` if it equals `session`.

`focused_session()` is deliberately distinct from `foreground()`: focus = "the
window I'm looking at," foreground = "who owns the voice." They coincide in the
common case and diverge exactly when this feature matters.

### 4.5 Routing — the only behavior change (`on_nav`)

```python
@handler(MsgType.NAV)
def on_nav(ctx, msg):
    sessions = ctx.host.sessions
    target = sessions.focused_session() or sessions.foreground()
    if target is None:
        return None
    crossed = target != sessions.foreground()    # compute BEFORE focus() moves it
    if crossed:
        sessions.focus(target)                    # move the voice to what we navigate
    to = msg.get("to", "prev")
    if to in ("prev_response", "next_response"):
        _nav_response(ctx, target, to)            # clears target queue, enqueues transcript
    else:
        _nav(ctx, target, to)
    if crossed:                                   # AFTER nav populates: prepend folder cue
        folder = sessions.folder(target)          # (an earlier enqueue would be dropped by
        if folder:                                #  _nav's queue.clear())
            ctx.host._enqueue(target, "prose", folder + ".", False,
                              mute_exempt=True, at_front=True, names_session=True)
    return None
```

- `target = focused_session() or foreground()` is the whole fix. When no terminal
  is focused / it's unmapped, `focused_session()` is `None` → falls back to today's
  `foreground()` exactly.
- `crossed` ⇒ this is a different session than the current voice owner: move the
  voice with `sessions.focus(target)` (so the nav output is actually spoken — the
  speak loop drains `foreground()`'s stream) and lead with a short `"<folder>."`
  cue so an eyes-free user knows the voice jumped. The cue is enqueued **after**
  `_nav`/`_nav_response` (both call `queue.clear()`, which would drop a cue queued
  before them) with `at_front=True` so it still plays first. `focus()` also clears
  any pin (explicit cross-session nav overrides a pin, identical to `jump_waiting`).
- Within-session nav (`crossed` false) is byte-for-byte today's behavior: no
  `focus()` call (pin preserved), no folder cue, just `_nav`/`_nav_response`.
- The invariant "nav target == `foreground()` == the session the speak loop plays"
  is preserved (we move foreground to the target before navigating).

No other handler changes. `pause`, `mute`, `stop`, `skip`, `jump_decision`,
`jump_waiting`, `pin_toggle`, `reread_options` all keep `foreground()`.

## 5. Error handling & fallback

Navigation must never break or go silent. Every unknown-focus path → today's
`foreground()` routing:

- watcher not built / hotkeyd absent (speech-only user) → no `os_focus` ever sent →
  `focused_session()` always `None` → `foreground()`. (Such a user has no nav
  hotkeys anyway.)
- frontmost app isn't a supported terminal → `os_focus {focused:false}` → `None` →
  `foreground()`.
- front terminal isn't a registered Claude session (plain shell) → no identity match
  → `None` → `foreground()`.
- tty not captured for the focused session (tmux/ssh/derivation failure) → no match
  → `foreground()`. *(Known limitation; listed.)*
- `sonari-raise --front-*` non-zero/timeout/Automation-denied → watcher sends
  nothing (stale or no focus) → `foreground()`. A denied grant simply means
  focus-aware nav stays dark and nav behaves as it does today — never a crash.

## 6. Testing strategy

The OS-side read/watcher can't be unit-tested (it reads real windows); everything
behind the `os_focus` seam can, and is TDD-first.

- **Headline failing test (write first, must fail today):** register A
  (`tty=/dev/ttys001`) and B (`tty=/dev/ttys002`); submit a prompt in B (B becomes
  `foreground`); deliver `os_focus {term_program:Apple_Terminal, tty:/dev/ttys001}`;
  send `NAV`. Assert the nav acted on **A**'s stream/history (A's `nav_cursor`
  moved, A's entries enqueued) and **not** B's. Fails today (routes to B).
- **Identity resolution** (`SessionManager.set_os_focus`): Terminal tty match;
  iTerm bare-GUID match; non-empty-only matching (empty tty matches nothing);
  no-match → `None`; `focused:false` → `None`; `focused_session()` returns `None`
  once the matched session is unregistered.
- **Voice move + cue:** cross-session nav calls `focus(target)` and enqueues the
  `"<folder>."` cue at front (`names_session`, `mute_exempt`); within-session nav
  does neither and leaves a pin intact.
- **Fallback:** with `_os_focused_session = None`, `on_nav` targets `foreground()`
  for every `to` value (parity with today).
- **Pin interaction:** pinned to B + focused A + nav → `focus(A)` clears the pin
  (cross-session); pinned to B + focused B + nav → pin preserved.
- **Handler:** `on_os_focus` forwards the payload to `set_os_focus` and speaks
  nothing.
- **Empirical, build-time (documented, not CI):** `sonari-raise --front-tty` /
  `--front-iterm` return the correct front identity on the target box (self-verified
  via independent osascript readback, not Nima-as-harness). A final **on-hardware
  acceptance** with two real sessions in two Terminal windows, run against a
  built-but-not-installed watcher / sacrificial setup (never clobbering the live
  install), is the human gate — consistent with how the M2 human gate caught
  defects the synthetic tests missed.

## 7. Out of scope / future

- Non-navigation hotkeys following focus (audio controls stay on the voice owner).
- Voice following mere window focus (only an arrow press moves it).
- Other terminals / tmux / ssh / Windows (graceful `foreground()` fallback).
- In-process Apple-Event read in `sonari-hotkeyd` (would need its own Automation
  grant) as a polling-cost optimization — deferred until measured need.
- A `doctor` row for focus-aware nav — optional; can piggyback on the existing
  focus-follow helper row.
