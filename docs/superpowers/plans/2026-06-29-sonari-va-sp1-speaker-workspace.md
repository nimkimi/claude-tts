# SP1 — Speaker / Workspace Split + Deliberate-Action Raises — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the overloaded `foreground` pointer into named **speaker** (voice owner) and **workspace** (front window) seams, repoint the speak loop and the answer/nav targets accordingly, and make **cycle (⌃⌘Tab)** and **⌃⌘D (`jump_decision`)** raise the window — behavior-preserving except the two new raises — so SP2 (keep-going) has a clean seam.

**Architecture:** Add a distinct `_speaker` pointer to `SessionManager`, maintained alongside the existing `_foreground` by the deliberate setters; the speak loop reads `speaker()` instead of `foreground()`. Add a `workspace()` resolver (`focused_session()` else `foreground()`) and repoint answer/nav/jump-decision targets to it. Mirror the existing `on_jump_waiting` raise machinery onto `on_cycle_session` and `on_jump_decision`. In SP1 `_speaker == _foreground` always (no keep-going yet), so every observable is preserved except the two raises.

**Tech Stack:** Python 3, `pytest`, the existing daemon (`src/sonari/daemon/*`, `src/sonari/sessions.py`), the macOS `RaiseService` (`src/sonari/raise_service.py`) + `sonari-hotkeyd`.

## Global Constraints

- **Baseline:** `836 passed, 1 skipped` (`.venv/bin/python -m pytest -q`). Must end green (baseline + new tests).
- **The two permanent concurrency guards (`tests/test_concurrency_guards.py`) stay green at EVERY commit.** The speak-loop change (Task B2) must keep the pop+claim+cancel-epoch lock block **identical in shape** — only the read source changes (`foreground()` → `speaker()`).
- **Behavior-preserving EXCEPT the two raises (Tasks C1, C2).** If any rename turns out NOT to be behavior-preserving, STOP and record it as an open question; do not change behavior silently.
- **TDD:** red → green → commit, bite-sized (one action per step). DRY, YAGNI.
- **macOS-only; no new dependencies.** Python 3 / `say` / `afplay` / `sonari-hotkeyd`.
- **Scope fence:** SP1 only. NO keep-going, NO Policy-A preempt, NO voice-state machine, NO frontier/marker, NO persistence (those are SP2–SP6).
- **Decisions (binding):** the workspace is `focused_session()` with a `foreground()` fallback; `answer_permission` targets the **workspace**, never the (future) auto-advancing speaker.

## Test-harness facts (verified against the repo — use these exact shapes)

- `from tests.daemon_helpers import make_daemon, stream_queue` — **`make_daemon(verbosity="everything", foreground="fg")` returns a 5-tuple `(daemon, queue, speaker, sessions, config)`.** Always unpack all five (use `_` for unused). `make_daemon` already `set_foreground`s the `foreground` arg, registering that session.
- The fake speaker is `FakeSpeaker` (returned as `speaker`): records spoken text in **`speaker.spoken`** (list), earcons in `speaker.earcons`, pitches in `speaker.pitches`, `speaker.cancels` (int). No real audio.
- Messages are built with the module-local `_msg(MsgType.X, session, **kw)` helper and driven via `daemon.handle_message(_msg(...))` (see any `tests/test_daemon_*.py`). Import `from sonari.protocol import MsgType` and `from sonari.sessions import Identity`.
- **The fake raise service is `RecordingRaiseService`** (defined in `tests/test_daemon_focus_follow.py`): `RecordingRaiseService(will=True)`, methods `will_attempt(identity)` (returns `will and identity is not None`), `bump_generation()`, `raise_async(identity, generation, on_failure=None)`; records calls in **`rs.attempts`** (list of `(identity, generation)`) and `rs.last_on_failure`. Inject with `daemon.raise_service = rs`. For the raise tests (C1/C2) **import it** (`from tests.test_daemon_focus_follow import RecordingRaiseService`) or copy the small class into the test module. There is **no** `fake_raise` pytest fixture — construct an `rs` explicitly. The canonical example to mirror is `test_jump_attempts_raise_with_target_identity` (`tests/test_daemon_focus_follow.py:49`).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/sonari/sessions.py` | Modify | Add `_speaker` storage + `speaker()` + `workspace()`; maintain `_speaker` in `set_foreground`/`focus`/`unregister`. |
| `src/sonari/daemon/host.py` | Modify (`:382`, `:413`) | Speak loop reads `speaker()` not `foreground()`. |
| `src/sonari/daemon/features/decisions.py` | Modify (`:185`) | `on_answer_permission` targets `workspace()`. |
| `src/sonari/daemon/features/navigation.py` | Modify (`:104`,`:107`) | `on_nav` targets `workspace()`. |
| `src/sonari/daemon/features/playback.py` | Modify (`:88-92`,`:85-114`) | `on_jump_decision` targets `workspace()` AND raises. |
| `src/sonari/daemon/features/focus.py` | Modify (`:91-113`) | `on_cycle_session` raises. |
| Phase-0 diagnosability files | Modify | Cherry-picked from `fix/cockpit-phase0-diagnosability` (plist, daemon bootstrap, STATUS handler + CLI, jump diag). |
| `tests/test_sessions.py`, `tests/test_daemon_*` | Add/Modify | Tests for `speaker()`/`workspace()`, the target repoints, and the two raises. |

**Task groups & order:** **A** = Phase-0 diagnosability (independent; first, so the rest is diagnosable). **B** = the accessor seam. **C** = the two raises. Within a group, tasks are ordered by dependency.

---

## Task Group A — Phase-0 diagnosability (cherry-pick / re-apply)

The diagnosability work already exists on branch `fix/cockpit-phase0-diagnosability` (HEAD `056be83`, off the same `main@2a1af7f`). Re-apply it so the build is debuggable. Take ONLY the diagnosability; if a commit also carries Phase-1 *fix* logic, leave the fix (Phase-1 fixes are folded later as spec-driven tests, not merged).

### Task A1: Enumerate and cherry-pick the diagnosability commits

**Files:** whatever the cherry-picks touch (plist template under `src/sonari/cli/install.py` or `src/sonari/platform/macos/supervisor.py`; `src/sonari/daemon/bootstrap.py` or `host.py` for faulthandler; `src/sonari/daemon/features/control.py` `on_status` for DIAG-3; `focus.py` for the FOCUS-1 line).

- [ ] **Step 1: List the candidate commits**

Run:
```bash
git log --oneline --no-merges main..fix/cockpit-phase0-diagnosability
```
Expected: a short list including DIAG-1 (`PYTHONUNBUFFERED`), DIAG-2 (`faulthandler`/SIGUSR1), DIAG-3 (richer STATUS + CLI), FOCUS-1 (jump diagnostic line). Note each sha + subject.

- [ ] **Step 2: Inspect each to confirm it is diagnosability-only**

Run `git show <sha>` for each. For any commit mixing a Phase-1 fix with diagnosability, plan to cherry-pick with `-n` and unstage the fix hunks (or re-apply the diag hunks by hand in a follow-up step). Record which are clean vs mixed.

- [ ] **Step 3: Cherry-pick the clean diagnosability commits**

```bash
git cherry-pick <diag-sha-1> <diag-sha-2> ...
```
If a pick conflicts (the surrounding code drifted on this branch), abort that pick (`git cherry-pick --abort`) and re-apply just the diagnosability hunk by hand in its own commit. Keep each logical diag change a separate commit.

- [ ] **Step 4: Run the full suite + the Phase-0 tests**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (836/1skip + any Phase-0 tests the picks bring). If a Phase-0 test references something SP1 hasn't built, mark it xfail with a `# SP-dependency` note rather than deleting it.

- [ ] **Step 5: Verify DIAG-3 STATUS by hand (sanity)**

Confirm `on_status` (`control.py`) now returns the heartbeat fields (`uptime`, `last_drain_age_s`, `current_item`, per-session queue). Run the relevant test, e.g.:
```bash
.venv/bin/python -m pytest tests/test_daemon_where_am_i.py tests/test_cli_control.py -q
```
Expected: PASS.

- [ ] **Step 6: Commit (if any hand re-application was needed)**

```bash
git add -A && git commit -m "chore(sp1): fold Phase-0 diagnosability (DIAG-1/2/3 + FOCUS-1)"
```

---

## Task Group B — The accessor seam

### Task B1: `speaker()` + `workspace()` on `SessionManager`

**Files:**
- Modify: `src/sonari/sessions.py` (`__init__` ~`:39-42`, `set_foreground` `:51-53`, `focus` `:108-112`, `unregister` `:71-77`)
- Test: `tests/test_sessions.py`

**Interfaces:**
- Produces: `SessionManager.speaker() -> str | None` (the voice owner; in SP1 == `foreground()`), `SessionManager.workspace() -> str | None` (`focused_session()` if set, else `foreground()`). `set_foreground()`/`focus()` now also set `_speaker`; `unregister()` clears it.
- Consumes: existing `_foreground`, `focused_session()` (`:137-141`), `_record`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sessions.py (append)
def test_speaker_tracks_deliberate_setters():
    sm = SessionManager()
    assert sm.speaker() is None
    sm.set_foreground("a", cwd="/x/a")
    assert sm.speaker() == "a"            # set_foreground sets the speaker
    sm.focus("b", cwd="/x/b")
    assert sm.speaker() == "b"            # focus (jump/cycle/nav) sets the speaker

def test_speaker_equals_foreground_in_sp1():
    sm = SessionManager()
    sm.set_foreground("a")
    assert sm.speaker() == sm.foreground()   # SP1 invariant: no keep-going yet

def test_unregister_clears_speaker():
    sm = SessionManager()
    sm.set_foreground("a")
    sm.unregister("a")
    assert sm.speaker() is None

def test_workspace_prefers_os_focus_then_foreground():
    sm = SessionManager()
    sm.register("a", cwd="/x/a")
    sm.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sm.set_foreground("b")                       # b is the voice owner / last-acted
    assert sm.workspace() == "b"                 # no OS focus -> fallback to foreground
    sm.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")
    assert sm.workspace() == "a"                 # OS focus on a -> workspace is a
```
(Ensure `Identity` is imported in the test module — it is used by existing tests there.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sessions.py -k "speaker or workspace" -v`
Expected: FAIL with `AttributeError: 'SessionManager' object has no attribute 'speaker'`.

- [ ] **Step 3: Implement the accessors + maintain `_speaker`**

In `src/sonari/sessions.py`:
- In `__init__`, after `self._foreground = None` add:
```python
        self._speaker: "str | None" = None    # the VOICE owner (speak loop reads this).
        # SP1: kept == _foreground (deliberate setters move both). SP2's keep-going
        # will advance _speaker on its own, diverging from _foreground (= last-acted).
```
- In `set_foreground`, after `self._foreground = session` add `self._speaker = session`.
- In `focus`, after `self._foreground = session` add `self._speaker = session`.
- In `unregister`, alongside the `_foreground` clear add:
```python
        if self._speaker == session:
            self._speaker = None
```
- Add the accessors (near `foreground()`):
```python
    def speaker(self) -> "str | None":
        """The session the voice is reading (the speak loop plays this stream).
        SP1: == foreground(); SP2 keep-going advances it independently."""
        return self._speaker

    def workspace(self) -> "str | None":
        """The front terminal + keyboard: the OS-focused session if known, else the
        last deliberately-acted session (foreground). The spec's 'workspace' — where
        you answer and what raises. Independent of the speaker once keep-going lands."""
        return self.focused_session() or self._foreground
```

- [ ] **Step 4: Run to verify they pass + full suite green**

Run: `.venv/bin/python -m pytest tests/test_sessions.py -q && .venv/bin/python -m pytest tests/test_concurrency_guards.py -q`
Expected: PASS (guards untouched but confirm).

- [ ] **Step 5: Commit**

```bash
git add src/sonari/sessions.py tests/test_sessions.py
git commit -m "feat(sp1): add speaker()/workspace() seam to SessionManager"
```

### Task B2: Speak loop reads `speaker()`

**Files:**
- Modify: `src/sonari/daemon/host.py` (`_speak_loop_once`: `:382` `fg0 = self.sessions.foreground()`, `:413` `fg = self.sessions.foreground()`)
- Test: `tests/test_daemon_streams.py` (or the existing speak-loop test module)

**Interfaces:**
- Consumes: `SessionManager.speaker()` (Task B1).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_streams.py (append) — the loop plays the SPEAKER's stream
from tests.daemon_helpers import make_daemon

def test_speak_loop_plays_speaker_stream():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="s0")
    sessions.register("s1", cwd="/x/s1")
    sessions.focus("s1")                      # deliberate setter -> speaker()==s1
    daemon._enqueue("s1", "prose", "hello from s1", False)
    daemon._speak_loop_once()
    # substring-tolerant: _attributed_text may fold-prefix a folder label on a speaker change
    assert any(s and "hello from s1" in s for s in speaker.spoken)  # loop popped s1's stream (speaker()==s1)
```
(`make_daemon` is the 5-tuple helper; `speaker.spoken` is the FakeSpeaker record — see Test-harness facts.)

- [ ] **Step 2: Run to verify it passes already OR fails meaningfully**

Run: `.venv/bin/python -m pytest tests/test_daemon_streams.py -k speaker_stream -v`
Expected: PASS even before the edit (because `focus()` sets `_foreground` too, and the loop currently reads `foreground()`). This test PINS the behavior so the `:382`/`:413` edit is proven non-regressive. If the helper differs, make the test fail first by asserting on `speaker()` divergence is impossible in SP1 — keep it as a regression pin.

- [ ] **Step 3: Repoint the loop to `speaker()`**

In `host.py`, change line ~382 `fg0 = self.sessions.foreground()` → `fg0 = self.sessions.speaker()` and line ~413 `fg = self.sessions.foreground()` → `fg = self.sessions.speaker()`. **Do not change the lock block's shape** — only the accessor. Update the nearby docstring/comment that says "plays the FOREGROUND session's stream" to "plays the SPEAKER session's stream."

- [ ] **Step 4: Run the speak-loop tests + BOTH concurrency guards**

Run: `.venv/bin/python -m pytest tests/test_daemon_streams.py tests/test_concurrency_guards.py -q`
Expected: PASS. (The guards `set_foreground`/`focus` the speaker, so they still drive the loop.)

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/host.py tests/test_daemon_streams.py
git commit -m "refactor(sp1): speak loop reads speaker() not foreground()"
```

### Task B3: Repoint answer / nav / jump-decision targets to `workspace()`

**Files:**
- Modify: `src/sonari/daemon/features/decisions.py` (`on_answer_permission` `:185`), `src/sonari/daemon/features/navigation.py` (`on_nav` `:104`,`:107`), `src/sonari/daemon/features/playback.py` (`on_jump_decision` `:88-92`)
- Test: `tests/test_daemon_decisions.py`, `tests/test_daemon_focus_nav.py`

**Interfaces:**
- Consumes: `SessionManager.workspace()` (Task B1).

- [ ] **Step 1: Write the failing/pinning test**

```python
# tests/test_daemon_decisions.py (append)
import threading
from sonari.protocol import MsgType
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon

def _msg(t, session, **kw):       # if the module lacks one; otherwise reuse its helper
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}

def test_answer_targets_workspace():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttysB"))
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysB")  # workspace == B
    daemon._pending_decisions["B"] = {"event": threading.Event(), "behavior": None}
    daemon.handle_message(_msg(MsgType.ANSWER_PERMISSION, "", behavior="allow"))
    assert daemon._pending_decisions["B"]["behavior"] == "allow"   # answered B (workspace), not A
```
Mirror the existing `test_jump_decision_targets_the_focused_session_not_foreground` setup (`tests/test_daemon_decisions.py:182+`) — reuse that module's `_msg` helper if it already defines one rather than redefining it.

- [ ] **Step 2: Run to verify it passes (pin) — `focused_session() or foreground()` already resolves to B here**

Run: `.venv/bin/python -m pytest tests/test_daemon_decisions.py -k answer_targets_workspace -v`
Expected: PASS today (behavior-preserving). This pins the contract before the rename so SP2 can't regress it.

- [ ] **Step 3: Rename the three targets to `workspace()`**

- `decisions.py:185`: `target = host.sessions.focused_session() or host.sessions.foreground()` → `target = host.sessions.workspace()`.
- `navigation.py:104`: `target = sessions.focused_session() or sessions.foreground()` → `target = sessions.workspace()`. At `:107` `crossed = target != sessions.foreground()` → keep comparing against the voice owner: `crossed = target != sessions.speaker()` (crossed means "the workspace differs from who's speaking" — the cue/voice-move trigger). Confirm against the existing `test_daemon_focus_nav.py` expectations and adjust if the test encodes `foreground()`.
- `playback.py:88-92` (`on_jump_decision`): `fg = sessions.foreground()` stays as the voice-owner reference, but `target = sessions.focused_session() or fg` → `target = sessions.workspace()`; `crossed = target != sessions.speaker()`.

**Audit note (state in the plan, don't change):** `foreground()` callers that want the *voice owner* stay as-is — `control.py:53` (rate cue), `control.py:130+` (`where_am_i`), `focus.py:39` (`jump_waiting` `exclude=fg`), `decisions.py:202` (`reread_options`). Only the three *target-resolution* sites above move to `workspace()`. In SP1 all are equal; the distinction is documented for SP2.

- [ ] **Step 4: Run the affected suites + guards**

Run: `.venv/bin/python -m pytest tests/test_daemon_decisions.py tests/test_daemon_focus_nav.py tests/test_daemon_nav.py tests/test_concurrency_guards.py -q`
Expected: PASS. If a `focus_nav` test asserts `crossed` against `foreground()`, update it to `speaker()` (same value in SP1) with a comment.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/features/decisions.py src/sonari/daemon/features/navigation.py src/sonari/daemon/features/playback.py tests/
git commit -m "refactor(sp1): answer/nav/jump-decision target workspace() not foreground()"
```

---

## Task Group C — The two raises (the only behavior change)

Mirror the raise machinery in `on_jump_waiting` (`focus.py:53-88`): `identity = sessions.identity(target)`, `will_raise = host._raise().will_attempt(identity)`, `gen = host._raise().bump_generation()` (bump on EVERY landing, not only raising ones — see `focus.py:58-65`), and, when `will_raise`, `host._raise().raise_async(identity, gen, on_failure=lambda s=target, f=folder: host._raise_failed(s, f))`. Tests inject a fake `daemon.raise_service` (see `tests/test_daemon_cycle.py` / `tests/test_cli_focus_follow.py` for the established fake — it records `raise_async` calls).

### Task C1: Cycle (⌃⌘Tab) raises the window

**Files:**
- Modify: `src/sonari/daemon/features/focus.py` (`on_cycle_session` `:91-113`)
- Test: `tests/test_daemon_cycle.py`

**Interfaces:**
- Consumes: `host._raise()` → `RaiseService.will_attempt(identity)`, `.bump_generation()`, `.raise_async(identity, gen, on_failure)`; `host._raise_failed(session, folder)`; `sessions.identity(target)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_cycle.py (append)
from sonari.protocol import MsgType
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon
from tests.test_daemon_focus_follow import RecordingRaiseService

def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}

def test_cycle_raises_target_window():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    sessions.register("B", cwd="/x/B")
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttysB"))
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    assert sessions.speaker() == "B"                       # cycle moved the voice to B
    assert len(rs.attempts) == 1                            # cycle attempted a raise
    ident, gen = rs.attempts[0]
    assert ident.tty == "/dev/ttysB" and gen >= 1
```
(`will_attempt` returns True because `will=True` and B has an identity. Reuse the module's `_msg` if it defines one.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_daemon_cycle.py -k raises_target -v`
Expected: FAIL (`raise_calls` empty — cycle is a soft switch today).

- [ ] **Step 3: Add the raise to `on_cycle_session`**

In `focus.py on_cycle_session`, after `sessions.focus(target)` + the existing cue enqueue, mirror `on_jump_waiting`'s raise block:
```python
    identity = sessions.identity(target)
    will_raise = ctx.host._raise().will_attempt(identity)
    gen = ctx.host._raise().bump_generation()
    if will_raise:
        ctx.host._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: ctx.host._raise_failed(s, f))
```
Place the `bump_generation()` so it runs on EVERY cycle (not only raising ones) — same rationale as `focus.py:58-65` (a non-raising cycle must still supersede a prior in-flight raise). Update the docstring: it is no longer a "SOFT switch (no terminal-raise)."

- [ ] **Step 4: Run to verify it passes + the cycle/guard suites**

Run: `.venv/bin/python -m pytest tests/test_daemon_cycle.py tests/test_concurrency_guards.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/features/focus.py tests/test_daemon_cycle.py
git commit -m "feat(sp1): cycle (Ctrl-Cmd-Tab) raises the target window (R5/R12)"
```

### Task C2: ⌃⌘D (`jump_decision`) raises the window

**Files:**
- Modify: `src/sonari/daemon/features/playback.py` (`on_jump_decision` `:85-114`)
- Test: `tests/test_daemon_decisions.py`

**Interfaces:**
- Consumes: same raise machinery as C1.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_decisions.py (append) — RecordingRaiseService imported at top of module (see B3)
from tests.test_daemon_focus_follow import RecordingRaiseService

def test_jump_decision_raises_target_window():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    sessions.register("B", cwd="/x/B")
    sessions.set_identity("B", Identity(term_program="Apple_Terminal", tty="/dev/ttysB"))
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysB")  # workspace/target == B
    daemon._enqueue("B", "permission", "Allow X?", True)   # a decision for it to land on
    daemon.handle_message(_msg(MsgType.JUMP_DECISION, ""))
    assert len(rs.attempts) == 1                            # jump_decision attempted a raise
    ident, gen = rs.attempts[0]
    assert ident.tty == "/dev/ttysB" and gen >= 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_daemon_decisions.py -k jump_decision_raises -v`
Expected: FAIL (`raise_calls` empty — `on_jump_decision` never raises today).

- [ ] **Step 3: Add the raise to `on_jump_decision`**

In `playback.py on_jump_decision`, after the existing `crossed`/focus/cancel/cue logic, add the raise for the `target` (mirror C1), guarded so it only attempts when there is a real identity to raise:
```python
    identity = sessions.identity(target)
    will_raise = ctx.host._raise().will_attempt(identity)
    gen = ctx.host._raise().bump_generation()
    if will_raise:
        folder = sessions.folder(target)
        ctx.host._raise().raise_async(
            identity, gen,
            on_failure=lambda s=target, f=folder: ctx.host._raise_failed(s, f))
```
Keep the existing `jump_to_decision()` queue trim and the crossed-folder spearcon cue. Ensure `bump_generation()` runs on every invocation. (If `folder` is already computed in the crossed branch, reuse it rather than recomputing — DRY.)

- [ ] **Step 4: Run to verify it passes + suites**

Run: `.venv/bin/python -m pytest tests/test_daemon_decisions.py tests/test_concurrency_guards.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon/features/playback.py tests/test_daemon_decisions.py
git commit -m "feat(sp1): jump_decision (Ctrl-Cmd-D) raises the target window (R5/R9 — C2 fix)"
```

---

## Final: full suite + guard sweep

- [ ] **Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — baseline 836/1skip + the new SP1 tests, 0 failures. Both concurrency guards green.

- [ ] **Confirm the SP1 invariant holds**

Spot-check: with no keep-going, `sessions.speaker() == sessions.foreground()` after every deliberate action in the test suite. (This invariant is what SP2 deliberately breaks.)

---

## Self-Review

**1. Spec coverage:**
- **R5** (deliberate move → speaker + workspace): Tasks B1/B2 (speaker), C1/C2 (workspace raises), B3 (workspace target). ✓
- **R8** (announce on speaker change): unchanged — `_attributed_text` still keys off `_last_spoken_session`; speaker rename doesn't touch it. ✓ (kept)
- **R10** (answer the workspace, never the wrong session): Task B3 (`answer_permission` → `workspace()`). ✓
- **R12** (window follows submit/jump/cycle): jump already raises; C1 adds cycle; C2 adds ⌃⌘D; submit's workspace follows via the OS focus-watcher (unchanged). ✓
- **§8 rows:** cycle CHANGE (C1), ⌃⌘D CHANGE (C2), ⌃⌘J KEEP (untouched), answer KEEP-with-target-fix (B3). ✓
- **Out of scope (correctly absent):** keep-going (R4), Policy-A preempt (R6), voice-state (R7), frontier (R3/§10), persistence (R11) — all SP2–SP6.

**2. Placeholder scan:** every code step has real code; the only deliberately-deferred specifics are the repo's existing test fixtures (`make_daemon`, `fake_raise`) — the build subagent reads `tests/daemon_helpers.py` + `tests/test_daemon_cycle.py` to match their exact shape (called out in each test step).

**3. Type/name consistency:** `speaker() -> str | None` and `workspace() -> str | None` are defined in B1 and consumed unchanged in B2/B3; the raise machinery (`will_attempt`/`bump_generation`/`raise_async`/`_raise_failed`) is named identically in C1/C2, mirroring `on_jump_waiting`.

**Open question for the build (flag, don't guess):** if `test_daemon_focus_nav.py` encodes `crossed` against `foreground()` rather than `speaker()`, the rename in B3 is still behavior-preserving in SP1 (equal values) but update the assertion to `speaker()` with a comment so SP2's divergence is correct. If any existing test breaks in a way that is NOT a pure rename, STOP and surface it.
