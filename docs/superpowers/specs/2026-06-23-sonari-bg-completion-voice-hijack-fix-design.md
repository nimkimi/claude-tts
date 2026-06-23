# Sonari — Fix #65: background-completion voice hijack

- **Date:** 2026-06-23
- **Status:** Design — approved (chat), implementing
- **Scope owner:** Nima
- **Issue:** #65
- **Builds on:** the per-session-streams model (the voice plays the foreground
  session's stream) and focus-aware-nav (which added `os_focus` but left the
  voice-ownership decision untouched).

## 1. Problem

When session A is mid-speech (foreground, actively reading a reply) and a
*different* session B's background agent/workflow **completes** — or B is an
auto-mode/`/loop` session that **ticks** on a timer — B seizes the voice: A is cut
off and the loop switches to draining B's stream. The intended contract is that a
background session's completion makes it a `jump-to-waiting` target, **not** the
new voice owner.

Observed as "the auto-mode session keeps grabbing the voice + end-response earcon
between timer ticks" during focus-aware-nav acceptance.

## 2. Root cause (confirmed)

- The speak loop drains only `sessions.foreground()`'s stream. Voice ownership =
  `foreground()` = `_pinned or _foreground`.
- `_foreground` is set **unconditionally** by `on_set_foreground`
  (`daemon/features/lifecycle.py`), reached via the `SET_FOREGROUND` /
  `SESSION_START` protocol messages.
- `hooks_entry.py` emits `SET_FOREGROUND` only on the **`UserPromptSubmit`** and
  **`SessionStart`** Claude Code hooks. A background session's *programmatic
  re-invocation* (confirmed for `/loop` / scheduled ticks, which fire
  `UserPromptSubmit`) therefore emits `SET_FOREGROUND(B)` → `_foreground = B`,
  then `FLUSH(B)` → `on_flush` sees `foreground() == B == session` →
  `speaker.cancel()` cuts A's in-flight utterance.
- The daemon cannot distinguish a real user prompt typed in B from a background
  re-invocation of B, so **every** prompt event steals the voice.

This is already encoded — as *intended* behavior — by
`tests/test_daemon_control.py::test_new_prompt_cuts_a_different_sessions_current_utterance`.

## 3. Scoping decision

The hijack is only harmful **when another session is actively speaking**. When the
voice is idle, a prompt event moving foreground to the incoming session is fine and
stays as-is. (Owner decision.) This removes any need for an OS-focus discriminator
or the focus-watcher.

## 4. Design — one model rule

In `on_set_foreground`, move `_foreground` to the incoming session **only when no
*different* session is actively speaking**. "Actively speaking" = the current
voice-owner session (`foreground()`, pin-aware) has any of:

- an in-flight utterance (`_current_item.session` is that session), **or**
- queued backlog (`len(stream.queue) > 0`), **or**
- buffered, not-yet-flushed prose (`len(stream.prose_buffer) > 0`).

When the voice is busy elsewhere, the incoming session is still **registered**
(folder/cwd recorded) so it accumulates in its own stream, fires the existing
"waiting" earcon, and becomes a `jump_waiting` target — but it does **not** become
foreground. When the voice is idle (or it's the same session), behavior is
unchanged from today.

### 4.1 Units

- `host._voice_busy_elsewhere(session) -> bool` — the predicate above. Reads
  `_current_item` / `_streams` / `sessions.foreground()` under the daemon lock
  (the same lock the speak loop holds for pop+claim → race-free; no new locking).
- `on_set_foreground` — calls `sessions.set_foreground(...)` when
  `not _voice_busy_elsewhere(session)`, else `sessions.register(...)` (record the
  folder without taking the voice). The `SESSION_START` branch (register /
  set_identity / setup-guidance) is unchanged.

### 4.2 What does NOT change

- `on_flush` is untouched. With the steal gated, `foreground()` never becomes the
  background session, so the cross-session cut clause (`foreground() == session`)
  is unreachable for a background prompt. Same-session cut is preserved.
- `jump_waiting` / `nav` / pin / `os_focus` — unchanged. Explicit user actions
  still move the voice exactly as before.

## 5. Consequence

Cross-session cut-on-switch effectively reduces to **same-session only**: when the
voice is busy we don't switch (so nothing is cut); when it's idle there is nothing
to cut. This matches the #65 contract — voice ownership changes only on an explicit
user action (jump / nav / OS-focus raise) or when the voice is idle.

## 6. Testing (TDD, RED first)

- **Acceptance (e2e, `test_e2e_pipeline.py`):** A streams a long reply; B emits a
  completion (prose + `Stop`) while A speaks → A's utterance finishes uninterrupted,
  A's remaining backlog is preserved, B's text accumulates in **B's** stream,
  `foreground()` stays A; a subsequent explicit `jump_waiting` reaches B.
- **Unit:** `SET_FOREGROUND(b)` while A's item is **in-flight** → foreground stays
  A, `cancels == 0`.
- **Unit:** `SET_FOREGROUND(b)` while A has **queued backlog** (nothing in-flight)
  → foreground stays A.
- **Unit:** `SET_FOREGROUND(b)` while A is **idle/empty** → B becomes foreground
  (today's behavior preserved).
- **Rewrite:** `test_new_prompt_cuts_a_different_sessions_current_utterance` to the
  new contract (no cut; foreground preserved; B accumulates).
- Keep green: `test_new_prompt_same_session_still_cuts`,
  `test_pinned_session_keeps_voice_when_another_submits`,
  `test_owner_keeps_voice_across_interchunk_drain...`.

## 7. Out of scope (filed separately)

- **Pause-resume sibling bug:** a background session's `FLUSH` runs
  `_paused.clear()`, so a background tick can resume a *paused* foreground voice.
  Same root-cause family ("a background prompt event mutating foreground voice
  state"), distinct symptom not named in #65 → separate follow-up issue.
- OS-focus-gating of the voice; making the voice follow window focus.
