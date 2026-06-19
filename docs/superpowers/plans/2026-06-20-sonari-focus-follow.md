# Sonari OS Keyboard-Focus Follow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the user presses the jump-to-waiting hotkey (`Ctrl+Cmd+J`), bring the target session's terminal window/tab to the foreground with keyboard focus — not just the voice.

**Architecture:** The SessionStart hook captures the terminal's identity (`TERM_PROGRAM`, controlling tty, `ITERM_SESSION_ID`) and ships it in the `SESSION_START` message; `SessionManager` stores it per session. A new core `RaiseService` (config gate + jump-generation supersession + async dispatch) sits behind the daemon's `JUMP_WAITING` handler and calls a platform `RaiseBackend`. On macOS the backend execs a dedicated `sonari-raise` Swift helper (AppleScript raise of Terminal.app, holding a clean Automation grant) or `open`s an `iterm2:///reveal` URL. Every failure path falls back to the existing voice jump plus a spoken "bring it forward" cue.

**Tech Stack:** Python 3.9 (stdlib only), Swift (compiled with `swiftc`, like the existing `sonari-hotkeyd`), AppleScript via `NSAppleScript`, macOS `launchd` LaunchAgent context.

## Global Constraints

- **Python floor 3.9, stdlib only** in the core — no new pip dependencies. (Verified by running the suite on 3.9.)
- **macOS-only feature.** Windows/Linux get a no-op `RaiseBackend` (returns False → voice-only fallback). One `sys.platform` branch already exists in `platform/__init__.py`; do not add others.
- **Behavior-preserving for everything except `jump_waiting`.** The live daemon runs from a COPY at `~/.sonari/app`; nothing in this plan reaches it until a future `sonari install`. Do not run `sonari install` as part of the build.
- **The voice jump must never break.** Every raise failure (unsupported terminal, missing identity, helper missing, permission denied, window gone, timeout) degrades to the existing voice jump + a spoken cue. The raise never runs on the speak thread or blocks the message handler.
- **Test command:** `source .venv/bin/activate && python -m pytest -q --ignore=tests/test_kokoro.py` (the ignore avoids the numpy-dependent neural test that needs the `[kokoro]` extra). Per-test runs: `python -m pytest tests/<file>::<test> -v`.
- **`tests/test_config.py::test_defaults_has_documented_top_level_keys` asserts the EXACT DEFAULTS key-set** — adding `focus_follow` to `DEFAULTS` REQUIRES updating that test in the same task (this exact-set test broke unnoticed in two prior stages; it is called out explicitly in Task 4).
- **Commit messages:** plain Conventional-Commits; **never** include a `claude.ai/code/session_...` link in commit messages or the PR body (standing user rule).
- **Branch + PR flow with the push-guard:** all work on a feature branch `feat/focus-follow` created off `main` BEFORE Task 1 (its own `git checkout -b` call). Do not push until the user asks. When pushing: `git checkout -b`/checkout in its own call, then `git push -u origin feat/focus-follow` in a SEPARATE call, then `gh pr` separately.
- **The OS raise itself is verified empirically (Task 11), not in CI.** Everything around it is unit-tested behind the `RaiseBackend` seam. Do not attempt to unit-test "a window actually moved."
- **AppleScript recipe is fixed and proven** (do not "improve" it): match the target by tty; `set selected of <tab> to true` → `set index of <window> to 1` → `activate`. **Never** `set frontmost of <window> to true` (it reverts the raise). Skip phantom windows with `visible and (count of tabs) > 0`. Avoid AppleScript variable names that collide with class names (e.g. `reals`).

---

## File Structure

**New files:**
- `src/sonari/ttyutil.py` — derive the controlling tty of the current process via process ancestry. Pure logic + one injectable `ps` call.
- `src/sonari/raise_service.py` — core `RaiseService`: config gate, jump-generation supersession, async dispatch to a `RaiseBackend`.
- `src/sonari/platform/macos/raiser.py` — `MacRaiseBackend`: Terminal.app (helper exec) + iTerm2 (reveal URL) dispatch, grant check, helper build, doctor rows.
- `hotkeyd/sonari-raise.swift` — the dedicated Swift helper (`sonari-raise <tty>` and `sonari-raise --check`).
- Tests: `tests/test_ttyutil.py`, `tests/test_raise_service.py`, `tests/test_macos_raise.py`, `tests/test_raise_swift.py`.

**Modified files:**
- `src/sonari/hooks_entry.py` — SessionStart adds `term_program`, `tty`, `iterm_session_id`.
- `src/sonari/sessions.py` — `Identity` dataclass + `set_identity`/`identity`.
- `src/sonari/config.py` — `DEFAULTS["focus_follow"] = True`.
- `tests/test_config.py` — add `focus_follow` to the exact key-set assertion.
- `src/sonari/platform/base.py` — `RaiseBackend` ABC + `NoopRaiseBackend` + `PlatformBackend.raise_backend` field.
- `src/sonari/platform/macos/__init__.py` — wire `MacRaiseBackend`.
- `src/sonari/platform/windows/__init__.py` — wire `NoopRaiseBackend`.
- `src/sonari/paths.py` — `RAISE_BIN_PATH`.
- `src/sonari/daemon.py` — `__init__` raise-service slot + lazy getter; `SESSION_START` stores identity; `JUMP_WAITING` raise/cue/supersession/failure-cue.
- `src/sonari/cli.py` — `install` builds the helper + proactive grant; `doctor` adds raise rows.

---

## Task 1: tty derivation utility

**Files:**
- Create: `src/sonari/ttyutil.py`
- Test: `tests/test_ttyutil.py`

**Interfaces:**
- Produces: `controlling_tty(pid: "int | None" = None, ps_runner=None) -> str` — returns the controlling terminal device of the first ancestor that has one, e.g. `"/dev/ttys005"`; `""` when none/derivation fails. `ps_runner` is an injectable `callable(pid:int) -> "tuple[int, str]"` returning `(ppid, tty)` for a pid (tty is the raw `ps` value, e.g. `"ttys005"`, `"??"`, or `""`); default uses `subprocess`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ttyutil.py
from sonari import ttyutil


def _fake_ps(table):
    # table: {pid: (ppid, tty_raw)}
    def runner(pid):
        return table.get(pid, (0, "??"))
    return runner


def test_returns_first_ancestor_with_real_tty_normalized():
    # self(100,??) -> parent(200,??) -> claude(300, ttys005)
    table = {100: (200, "??"), 200: (300, "??"), 300: (1, "ttys005")}
    assert ttyutil.controlling_tty(pid=100, ps_runner=_fake_ps(table)) == "/dev/ttys005"


def test_already_prefixed_tty_not_double_prefixed():
    table = {100: (1, "/dev/ttys007")}
    assert ttyutil.controlling_tty(pid=100, ps_runner=_fake_ps(table)) == "/dev/ttys007"


def test_no_tty_anywhere_returns_empty():
    table = {100: (200, "??"), 200: (1, "??")}
    assert ttyutil.controlling_tty(pid=100, ps_runner=_fake_ps(table)) == ""


def test_walk_stops_at_pid_1_or_0_without_looping():
    table = {100: (1, "??"), 1: (0, "??")}
    assert ttyutil.controlling_tty(pid=100, ps_runner=_fake_ps(table)) == ""


def test_runner_exception_degrades_to_empty():
    def boom(_pid):
        raise OSError("ps failed")
    assert ttyutil.controlling_tty(pid=100, ps_runner=boom) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ttyutil.py -v`
Expected: FAIL (ModuleNotFoundError: no module named `sonari.ttyutil`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/sonari/ttyutil.py
"""Derive the controlling tty of this process by walking process ancestry.

A Claude Code hook runs as a subprocess whose own stdin/stdout are pipes and
whose /dev/tty is not configured; but an ancestor (the `claude` process) carries
the terminal tab's real tty. We walk parents until we find one, then normalize to
a /dev/ttysNNN path that matches what Terminal.app reports as `tty of tab`.
"""
from __future__ import annotations

import os


def _default_ps(pid: int) -> "tuple[int, str]":
    """Return (ppid, tty_raw) for *pid* via `ps`. Raises on failure (caller guards)."""
    import subprocess
    out = subprocess.run(
        ["ps", "-o", "ppid=,tty=", "-p", str(pid)],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    if not out:
        return (0, "")
    parts = out.split(None, 1)
    ppid = int(parts[0])
    tty = parts[1].strip() if len(parts) > 1 else ""
    return (ppid, tty)


def _normalize(tty: str) -> str:
    """A real tty device name -> /dev/ttysNNN; '??'/'?'/'' -> ''."""
    tty = tty.strip()
    if not tty or tty in ("?", "??"):
        return ""
    if tty.startswith("/dev/"):
        return tty
    return "/dev/" + tty


def controlling_tty(pid: "int | None" = None, ps_runner=None) -> str:
    """First ancestor's real controlling tty as /dev/ttysNNN, else ''. Never raises."""
    runner = ps_runner or _default_ps
    cur = os.getpid() if pid is None else pid
    seen = set()
    try:
        for _ in range(32):  # bounded walk; cannot loop
            if cur in (0, 1) or cur in seen:
                return ""
            seen.add(cur)
            ppid, tty_raw = runner(cur)
            norm = _normalize(tty_raw)
            if norm:
                return norm
            cur = ppid
        return ""
    except Exception:  # noqa: BLE001 - best-effort; any failure -> no tty
        return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ttyutil.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/sonari/ttyutil.py tests/test_ttyutil.py
git commit -m "feat(ttyutil): derive controlling tty via process ancestry"
```

---

## Task 2: SessionStart hook captures terminal identity

**Files:**
- Modify: `src/sonari/hooks_entry.py` (the `SessionStart` branch, currently `hooks_entry.py:100-111`)
- Test: `tests/test_hooks_entry.py` (add cases; create if absent)

**Interfaces:**
- Consumes: `ttyutil.controlling_tty()` (Task 1).
- Produces: the `SESSION_START` message dict now also carries `term_program: str`, `tty: str`, `iterm_session_id: str` (all `""` when unavailable).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hooks_entry.py  (add these; keep existing tests)
from sonari import hooks_entry
from sonari.protocol import MsgType


def _session_start_msg(msgs):
    return next(m for m in msgs if m.get("type") == MsgType.SESSION_START)


def test_session_start_captures_identity(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    monkeypatch.setenv("ITERM_SESSION_ID", "")
    monkeypatch.setattr(hooks_entry.ttyutil, "controlling_tty", lambda: "/dev/ttys005")
    msgs = hooks_entry.handle_event("SessionStart", {"session_id": "s1", "cwd": "/x"})
    m = _session_start_msg(msgs)
    assert m["term_program"] == "Apple_Terminal"
    assert m["tty"] == "/dev/ttys005"
    assert m["iterm_session_id"] == ""


def test_session_start_captures_iterm_id(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setenv("ITERM_SESSION_ID", "w0t0p0:ABC-123")
    monkeypatch.setattr(hooks_entry.ttyutil, "controlling_tty", lambda: "")
    m = _session_start_msg(
        hooks_entry.handle_event("SessionStart", {"session_id": "s1", "cwd": "/x"}))
    assert m["term_program"] == "iTerm.app"
    assert m["iterm_session_id"] == "w0t0p0:ABC-123"


def test_session_start_missing_env_yields_empty_strings(monkeypatch):
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
    monkeypatch.setattr(hooks_entry.ttyutil, "controlling_tty", lambda: "")
    m = _session_start_msg(
        hooks_entry.handle_event("SessionStart", {"session_id": "s1", "cwd": "/x"}))
    assert m["term_program"] == ""
    assert m["tty"] == ""
    assert m["iterm_session_id"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hooks_entry.py -v`
Expected: FAIL (KeyError: `term_program`, or AttributeError on `hooks_entry.ttyutil`).

- [ ] **Step 3: Write minimal implementation**

At the top of `src/sonari/hooks_entry.py`, add the import (next to `import os`):

```python
from sonari import ttyutil
```

Replace the `SessionStart` branch (currently `hooks_entry.py:100-111`) with:

```python
    if event == "SessionStart":
        return [
            _msg(type=MsgType.SET_FOREGROUND, session=session,
                 cwd=payload.get("cwd", "")),
            _msg(
                type=MsgType.SESSION_START,
                session=session,
                cwd=payload.get("cwd", ""),
                plugin_version=os.environ.get("CLAUDE_PLUGIN_VERSION", ""),
                plugin_root=os.environ.get("CLAUDE_PLUGIN_ROOT", ""),
                # Terminal identity for OS keyboard-focus-follow (best-effort; the
                # daemon runs under launchd and cannot derive these itself).
                term_program=os.environ.get("TERM_PROGRAM", ""),
                iterm_session_id=os.environ.get("ITERM_SESSION_ID", ""),
                tty=ttyutil.controlling_tty(),
            ),
        ]
```

Update the module docstring's "PURE: no I/O" note on `handle_event` to: "PURE except a best-effort tty probe in SessionStart."

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hooks_entry.py -v`
Expected: PASS. Also run the whole suite to confirm no existing hook test regressed: `python -m pytest tests/test_hooks_entry.py -q`.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/hooks_entry.py tests/test_hooks_entry.py
git commit -m "feat(hooks): capture terminal identity (term_program/tty/iterm id) at SessionStart"
```

---

## Task 3: SessionManager stores per-session identity

**Files:**
- Modify: `src/sonari/sessions.py`
- Test: `tests/test_sessions.py` (add cases; create if absent)

**Interfaces:**
- Produces:
  - `Identity` dataclass with fields `term_program: str = ""`, `tty: str = ""`, `iterm_session_id: str = ""`.
  - `SessionManager.set_identity(session: str, identity: "Identity") -> None`
  - `SessionManager.identity(session: str) -> "Identity | None"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sessions.py  (add these)
from sonari.sessions import SessionManager, Identity


def test_set_and_get_identity():
    sm = SessionManager()
    sm.set_identity("s1", Identity(term_program="Apple_Terminal", tty="/dev/ttys005"))
    ident = sm.identity("s1")
    assert ident is not None
    assert ident.term_program == "Apple_Terminal"
    assert ident.tty == "/dev/ttys005"
    assert ident.iterm_session_id == ""


def test_identity_absent_is_none():
    assert SessionManager().identity("nope") is None


def test_unregister_clears_identity():
    sm = SessionManager()
    sm.register("s1")
    sm.set_identity("s1", Identity(term_program="iTerm.app", iterm_session_id="X"))
    sm.unregister("s1")
    assert sm.identity("s1") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sessions.py -v`
Expected: FAIL (ImportError: cannot import `Identity`).

- [ ] **Step 3: Write minimal implementation**

At the top of `src/sonari/sessions.py` (after `from __future__ import annotations`):

```python
from dataclasses import dataclass


@dataclass
class Identity:
    """Terminal identity captured at SessionStart, used by focus-follow."""
    term_program: str = ""
    tty: str = ""
    iterm_session_id: str = ""
```

In `SessionManager.__init__`, after `self._pinned = None`, add:

```python
        self._identities: "dict[str, Identity]" = {}
```

Add these methods to `SessionManager`:

```python
    def set_identity(self, session: str, identity: "Identity") -> None:
        self._identities[session] = identity

    def identity(self, session: str) -> "Identity | None":
        return self._identities.get(session)
```

In `SessionManager.unregister`, after `self._sessions.pop(session, None)`, add:

```python
        self._identities.pop(session, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sessions.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/sessions.py tests/test_sessions.py
git commit -m "feat(sessions): store per-session terminal Identity"
```

---

## Task 4: Config `focus_follow` flag

**Files:**
- Modify: `src/sonari/config.py` (DEFAULTS, `config.py:9-17`)
- Modify: `tests/test_config.py` (the exact key-set assertion, `test_config.py:5-14`)

**Interfaces:**
- Produces: `DEFAULTS["focus_follow"] = True`.

- [ ] **Step 1: Update the failing test FIRST (it will fail until DEFAULTS changes)**

In `tests/test_config.py`, add `"focus_follow"` to the asserted set in `test_defaults_has_documented_top_level_keys`:

```python
def test_defaults_has_documented_top_level_keys():
    assert set(DEFAULTS.keys()) == {
        "voice",
        "rate",
        "verbosity",
        "background_policy",
        "history_cap",
        "backlog_cap",
        "minqueue",
        "focus_follow",
    }
```

Also add a value assertion:

```python
def test_focus_follow_defaults_on():
    assert DEFAULTS["focus_follow"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL (`focus_follow` not in DEFAULTS → set inequality + KeyError).

- [ ] **Step 3: Add the default**

In `src/sonari/config.py`, add to the `DEFAULTS` dict:

```python
    "focus_follow": True,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/config.py tests/test_config.py
git commit -m "feat(config): add focus_follow flag (default on)"
```

---

## Task 5: RaiseBackend seam (ABC + no-op + PlatformBackend field)

**Files:**
- Modify: `src/sonari/platform/base.py`
- Modify: `src/sonari/platform/macos/__init__.py`
- Modify: `src/sonari/platform/windows/__init__.py`
- Test: `tests/test_platform_raise_seam.py`

**Interfaces:**
- Produces:
  - `RaiseBackend` ABC: `raise_session(self, identity) -> bool` (abstract), `supports(self, identity) -> bool` (default `False`), `check_grant(self) -> str` (default `"unsupported"`), `doctor_rows(self) -> list` (default `[]`).
  - `NoopRaiseBackend(RaiseBackend)`: everything inert (`raise_session`→`False`).
  - `PlatformBackend.raise_backend: RaiseBackend` (new field).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_platform_raise_seam.py
from sonari.platform import get_platform
from sonari.platform.base import RaiseBackend, NoopRaiseBackend, PlatformBackend


def test_platformbackend_has_raise_backend_field():
    assert "raise_backend" in PlatformBackend.__dataclass_fields__


def test_get_platform_exposes_a_raise_backend():
    rb = get_platform().raise_backend
    assert isinstance(rb, RaiseBackend)


def test_noop_backend_is_inert():
    nb = NoopRaiseBackend()
    assert nb.supports(None) is False
    assert nb.raise_session(None) is False
    assert nb.check_grant() == "unsupported"
    assert nb.doctor_rows() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_platform_raise_seam.py -v`
Expected: FAIL (ImportError: `RaiseBackend`/`NoopRaiseBackend`; missing dataclass field).

- [ ] **Step 3: Implement**

In `src/sonari/platform/base.py`, add the ABC (after the `HotkeyBackend` class, before `SupervisorBackend`):

```python
class RaiseBackend(abc.ABC):
    """Bring a session's terminal window/tab to the foreground (focus-follow)."""

    @abc.abstractmethod
    def raise_session(self, identity) -> bool:
        """Raise the window/tab for *identity* (a sessions.Identity). Return True
        only on a confirmed raise; False for unsupported/missing/denied/failed.
        Safe to call off the main thread; must never raise or hang."""

    def supports(self, identity) -> bool:
        """True if this backend can attempt a raise for *identity* (right terminal
        + the needed handle present). Default: no."""
        return False

    def check_grant(self) -> str:
        """OS permission state for the raise mechanism: 'granted' | 'denied' |
        'unknown' | 'unsupported'. Default: 'unsupported'."""
        return "unsupported"

    def doctor_rows(self) -> "list":
        """Diagnostic [(name, ok, detail), ...] rows. Default: none."""
        return []


class NoopRaiseBackend(RaiseBackend):
    """Inert backend for platforms without focus-follow (Windows/Linux/tests)."""

    def raise_session(self, identity) -> bool:
        return False
```

Add the field to the `PlatformBackend` dataclass:

```python
@dataclass
class PlatformBackend:
    tts: TtsBackend
    earcon: EarconBackend
    hotkey: HotkeyBackend
    supervisor: SupervisorBackend
    raise_backend: RaiseBackend
```

In `src/sonari/platform/windows/__init__.py`, import and wire the no-op into `make_backend()`:

```python
from sonari.platform.base import NoopRaiseBackend
# ... inside make_backend(), add to the PlatformBackend(...) call:
        raise_backend=NoopRaiseBackend(),
```

In `src/sonari/platform/macos/__init__.py`, **temporarily** wire the no-op too (Task 9 swaps it for `MacRaiseBackend`):

```python
from sonari.platform.base import NoopRaiseBackend
# ... inside make_backend(), add to the PlatformBackend(...) call:
        raise_backend=NoopRaiseBackend(),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_platform_raise_seam.py -v`
Expected: PASS. Then the full suite — adding a required dataclass field can break any test that constructs `PlatformBackend(...)` directly: `python -m pytest -q --ignore=tests/test_kokoro.py`. If a test builds `PlatformBackend(...)` by hand, add `raise_backend=NoopRaiseBackend()` there.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/platform/base.py src/sonari/platform/macos/__init__.py src/sonari/platform/windows/__init__.py tests/test_platform_raise_seam.py
git commit -m "feat(platform): add RaiseBackend seam + PlatformBackend.raise_backend (no-op)"
```

---

## Task 6: RaiseService core (gate + supersession + async dispatch)

**Files:**
- Create: `src/sonari/raise_service.py`
- Test: `tests/test_raise_service.py`

**Interfaces:**
- Consumes: a `RaiseBackend` (Task 5); a config dict with `focus_follow` (Task 4); `sessions.Identity` (Task 3).
- Produces: `RaiseService(backend, config)` with:
  - `will_attempt(identity) -> bool` — `bool(config.get("focus_follow", True)) and identity is not None and backend.supports(identity)`.
  - `bump_generation() -> int` — increment + return the monotonic jump generation (call under the daemon lock).
  - `raise_async(identity, generation, on_failure=None) -> None` — spawn a daemon thread that no-ops if `generation` is stale, else calls `backend.raise_session(identity)`; on a falsey result that is still current, calls `on_failure()`.
  - `current_generation() -> int` — for tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_raise_service.py
import threading

from sonari.raise_service import RaiseService
from sonari.sessions import Identity


class FakeBackend:
    def __init__(self, supports=True, result=True, gate=None, entered=None):
        self._supports = supports
        self._result = result
        self._gate = gate          # threading.Event the call waits on, if set
        self._entered = entered    # threading.Event set when raise_session begins
        self.calls = []

    def supports(self, identity):
        return self._supports

    def raise_session(self, identity):
        self.calls.append(identity)
        if self._entered is not None:
            self._entered.set()
        if self._gate is not None:
            self._gate.wait(2.0)
        return self._result


def test_will_attempt_requires_flag_identity_and_support():
    ident = Identity(term_program="Apple_Terminal", tty="/dev/ttys1")
    assert RaiseService(FakeBackend(supports=True), {"focus_follow": True}).will_attempt(ident) is True
    assert RaiseService(FakeBackend(supports=True), {"focus_follow": False}).will_attempt(ident) is False
    assert RaiseService(FakeBackend(supports=False), {"focus_follow": True}).will_attempt(ident) is False
    assert RaiseService(FakeBackend(supports=True), {"focus_follow": True}).will_attempt(None) is False


def test_successful_raise_does_not_call_on_failure():
    be = FakeBackend(result=True)
    svc = RaiseService(be, {"focus_follow": True})
    called = []
    gen = svc.bump_generation()
    svc.raise_async(Identity(tty="/dev/ttys1"), gen, on_failure=lambda: called.append(1))
    svc.join(2.0)
    assert be.calls and not called


def test_failed_current_raise_calls_on_failure():
    be = FakeBackend(result=False)
    svc = RaiseService(be, {"focus_follow": True})
    called = []
    gen = svc.bump_generation()
    svc.raise_async(Identity(tty="/dev/ttys1"), gen, on_failure=lambda: called.append(1))
    svc.join(2.0)
    assert called == [1]


def test_stale_generation_aborts_before_raise():
    be = FakeBackend(result=False)
    svc = RaiseService(be, {"focus_follow": True})
    called = []
    stale = svc.bump_generation()   # gen 1
    svc.bump_generation()           # gen 2 (now current) — supersedes
    svc.raise_async(Identity(tty="/dev/ttys1"), stale, on_failure=lambda: called.append(1))
    svc.join(2.0)
    assert be.calls == []           # raise never attempted
    assert called == []             # no stale failure cue


def test_supersede_during_slow_raise_suppresses_failure_cue():
    gate, entered = threading.Event(), threading.Event()
    be = FakeBackend(result=False, gate=gate, entered=entered)
    svc = RaiseService(be, {"focus_follow": True})
    called = []
    gen = svc.bump_generation()
    svc.raise_async(Identity(tty="/dev/ttys1"), gen, on_failure=lambda: called.append(1))
    assert entered.wait(2.0)        # raise is in-flight
    svc.bump_generation()           # a newer jump arrives mid-raise
    gate.set()                      # let the slow raise finish (returns False)
    svc.join(2.0)
    assert be.calls                 # it did run
    assert called == []             # but the failure cue is suppressed (superseded)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raise_service.py -v`
Expected: FAIL (ModuleNotFoundError `sonari.raise_service`).

- [ ] **Step 3: Implement**

```python
# src/sonari/raise_service.py
"""Core focus-follow orchestration: config gate, jump-generation supersession,
and async dispatch to a platform RaiseBackend. The slow OS raise (~0.4s) runs on
a daemon thread so the message handler and speak loop are never blocked; a stale
raise (a newer jump superseded it) no-ops, so OS focus never diverges from voice.
"""
from __future__ import annotations

import threading


class RaiseService:
    def __init__(self, backend, config) -> None:
        self._backend = backend
        self._config = config
        self._generation = 0
        self._lock = threading.Lock()
        self._threads: "list[threading.Thread]" = []

    def will_attempt(self, identity) -> bool:
        if not bool(self._config.get("focus_follow", True)):
            return False
        if identity is None:
            return False
        return bool(self._backend.supports(identity))

    def bump_generation(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def current_generation(self) -> int:
        with self._lock:
            return self._generation

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation

    def raise_async(self, identity, generation: int, on_failure=None) -> None:
        def _run():
            if not self._is_current(generation):
                return
            try:
                ok = self._backend.raise_session(identity)
            except Exception:  # noqa: BLE001 - a backend bug must never crash the thread
                ok = False
            if not ok and on_failure is not None and self._is_current(generation):
                on_failure()
        t = threading.Thread(target=_run, name="sonari-raise", daemon=True)
        with self._lock:
            self._threads.append(t)
        t.start()

    def join(self, timeout: "float | None" = None) -> None:
        """Test helper: wait for spawned raise threads to finish."""
        for t in list(self._threads):
            t.join(timeout)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raise_service.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/sonari/raise_service.py tests/test_raise_service.py
git commit -m "feat(raise): RaiseService with config gate + jump-generation supersession"
```

---

## Task 7: Daemon wiring (store identity + raise on jump + cue + failure follow-up)

**Files:**
- Modify: `src/sonari/daemon.py` (`__init__` `daemon.py:38-58`; SESSION_START handler `daemon.py:442-447`; JUMP_WAITING handler `daemon.py:580-603`)
- Test: `tests/test_daemon_focus_follow.py`

**Interfaces:**
- Consumes: `RaiseService` (Task 6), `sessions.set_identity`/`identity` + `Identity` (Task 3), the new `SESSION_START` message fields (Task 2).
- Produces: `SpeechDaemon.__init__(self, speaker, sessions, config, raise_service=None)`; lazy `self._raise()`; identity stored on SESSION_START; on JUMP_WAITING the preamble varies by `will_attempt`, an async raise fires, and a failed-but-current raise enqueues a "Bring … forward to type." cue.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daemon_focus_follow.py
import threading

from sonari.protocol import MsgType
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon, stream_queue


def _msg(t, session, **kw):
    d = {"v": 1, "type": t, "session": session}
    d.update(kw)
    return d


class RecordingRaiseService:
    """Stands in for RaiseService; records calls, lets the test drive results."""
    def __init__(self, will=True):
        self._will = will
        self._gen = 0
        self.attempts = []        # (identity, generation)
        self.last_on_failure = None

    def will_attempt(self, identity):
        return self._will and identity is not None

    def bump_generation(self):
        self._gen += 1
        return self._gen

    def raise_async(self, identity, generation, on_failure=None):
        self.attempts.append((identity, generation))
        self.last_on_failure = on_failure


def _ident():
    return Identity(term_program="Apple_Terminal", tty="/dev/ttys9")


def test_session_start_stores_identity():
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    daemon._setup_health = lambda v: ("ok", None)
    daemon.handle_message(_msg(MsgType.SESSION_START, "s1", cwd="/x",
                               term_program="Apple_Terminal", tty="/dev/ttys9",
                               iterm_session_id=""))
    ident = sessions.identity("s1")
    assert ident is not None and ident.tty == "/dev/ttys9"


def test_jump_attempts_raise_with_target_identity():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("b", cwd="/work/backend")
    sessions.set_identity("b", _ident())
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    daemon.handle_message(_msg(MsgType.PROSE, "b", delta="hi. ", index=0, final=True))
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert sessions.foreground() == "b"
    assert len(rs.attempts) == 1
    ident, gen = rs.attempts[0]
    assert ident.tty == "/dev/ttys9" and gen >= 1
    # preamble unchanged when a raise will be attempted
    assert stream_queue(daemon, "b")._items[0].text == "Jumping to backend."


def test_jump_adds_cue_when_no_raise_will_happen():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("b", cwd="/work/backend")  # no identity set
    rs = RecordingRaiseService(will=False)
    daemon.raise_service = rs
    daemon.handle_message(_msg(MsgType.PROSE, "b", delta="hi. ", index=0, final=True))
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    assert rs.attempts == []
    assert stream_queue(daemon, "b")._items[0].text == \
        "Jumping to backend. Bring it forward to type."


def test_raise_failure_callback_enqueues_cue():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="a")
    sessions.register("b", cwd="/work/backend")
    sessions.set_identity("b", _ident())
    rs = RecordingRaiseService(will=True)
    daemon.raise_service = rs
    daemon.handle_message(_msg(MsgType.PROSE, "b", delta="hi. ", index=0, final=True))
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, "a"))
    # simulate the async raise reporting failure
    assert rs.last_on_failure is not None
    rs.last_on_failure()
    texts = [it.text for it in stream_queue(daemon, "b")._items]
    assert "Bring backend forward to type." in texts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_daemon_focus_follow.py -v`
Expected: FAIL (no `raise_service` attribute / SESSION_START doesn't store identity / preamble unchanged).

- [ ] **Step 3: Implement**

In `SpeechDaemon.__init__` (`daemon.py:38`), change the signature and add the slot:

```python
    def __init__(self, speaker, sessions, config, raise_service=None) -> None:
```

After `self._last_spoken_session = None`, add:

```python
        self.raise_service = raise_service        # lazily built on first jump
```

Add the lazy getter as a method on the class (near the other helpers):

```python
    def _raise(self):
        if self.raise_service is None:
            from sonari.raise_service import RaiseService
            from sonari.platform import get_platform
            self.raise_service = RaiseService(get_platform().raise_backend, self.config)
        return self.raise_service
```

In the `SESSION_START`/`SET_FOREGROUND` handler (`daemon.py:442-447`), store identity on SESSION_START:

```python
        if t in (MsgType.SET_FOREGROUND, MsgType.SESSION_START):
            self.sessions.set_foreground(session, cwd=msg.get("cwd"))
            if t == MsgType.SESSION_START:
                self.sessions.register(session, cwd=msg.get("cwd"))
                from sonari.sessions import Identity
                self.sessions.set_identity(session, Identity(
                    term_program=msg.get("term_program", ""),
                    tty=msg.get("tty", ""),
                    iterm_session_id=msg.get("iterm_session_id", ""),
                ))
                self._maybe_guide_setup(session, msg.get("plugin_version", ""))
            return None
```

In the `JUMP_WAITING` handler (`daemon.py:592-603`), replace the preamble/enqueue block (everything after `self.speaker.cancel()`) with:

```python
            folder = self.sessions.folder(target)
            identity = self.sessions.identity(target)
            will_raise = self._raise().will_attempt(identity)
            base = ("Jumping to {0}.".format(folder) if folder
                    else "Jumping to another session.")
            if not will_raise:
                base += " Bring it forward to type."
            self._enqueue(target, "prose", base, False,
                          mute_exempt=True, at_front=True, names_session=True)
            if will_raise:
                gen = self._raise().bump_generation()
                self._raise().raise_async(
                    identity, gen,
                    on_failure=lambda s=target, f=folder: self._raise_failed(s, f))
            return None
```

Add the failure-cue helper (it runs on the raise thread, so it takes the daemon lock the way handlers do):

```python
    def _raise_failed(self, session: str, folder) -> None:
        """Raise thread reported failure for a still-current jump: tell the user
        to bring the window forward by hand. Acquires the daemon lock (this runs
        off the message-handler path)."""
        text = ("Bring {0} forward to type.".format(folder) if folder
                else "Bring it forward to type.")
        with self._lock:
            self._enqueue(session, "prose", text, False,
                          mute_exempt=True, at_front=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_daemon_focus_follow.py -v`
Then the daemon suite for regressions (the JUMP_WAITING preamble assertions elsewhere still expect "Jumping to <folder>." when a raise will be attempted — which is the default since real tests have no identity unless set, BUT the lazy `_raise()` uses the real NoopRaiseBackend on macOS which returns `supports=False` → `will_attempt=False` → existing tests would get the cue appended and FAIL). To keep existing JUMP_WAITING tests green, they must continue to see the plain preamble. Because `will_attempt` is False without identity, existing tests WILL see the cue appended. **Fix:** update the existing JUMP_WAITING preamble assertions in `tests/test_daemon_streams.py` to expect the cue when no identity is set, OR set an identity in those tests. Prefer the latter only where the test is about the jump; otherwise assert the new text. Run `python -m pytest tests/test_daemon_streams.py -q` and reconcile each failing assertion to the documented new behavior (plain preamble only when a raise will be attempted).
Expected after reconciliation: PASS across `tests/test_daemon_focus_follow.py` and `tests/test_daemon_streams.py`.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/daemon.py tests/test_daemon_focus_follow.py tests/test_daemon_streams.py
git commit -m "feat(daemon): focus-follow on jump_waiting (identity store, async raise, supersession cue)"
```

---

## Task 8: `sonari-raise` Swift helper + build pipeline

**Files:**
- Create: `hotkeyd/sonari-raise.swift`
- Modify: `src/sonari/paths.py` (add `RAISE_BIN_PATH`)
- Test: `tests/test_raise_swift.py`

**Interfaces:**
- Produces: a compilable `sonari-raise` binary: `sonari-raise <tty>` (exit 0 = raised, 1 = not found/missed, 2 = usage, 3 = Automation denied, 4 = other AppleScript error); `sonari-raise --check` (exit 0 = granted, 3 = denied, 4 = other). `paths.RAISE_BIN_PATH = SONARI_DIR / "sonari-raise"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_raise_swift.py
import os
import shutil
import subprocess

import pytest

SWIFT_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "hotkeyd", "sonari-raise.swift")


def test_swift_source_exists():
    assert os.path.exists(SWIFT_SRC)


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="swiftc not available")
def test_swift_source_compiles(tmp_path):
    out = tmp_path / "sonari-raise"
    proc = subprocess.run(["swiftc", SWIFT_SRC, "-o", str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    assert "warning:" not in proc.stderr, proc.stderr


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="swiftc not available")
def test_usage_exit_code(tmp_path):
    out = tmp_path / "sonari-raise"
    subprocess.run(["swiftc", SWIFT_SRC, "-o", str(out)], check=True)
    r = subprocess.run([str(out)], capture_output=True, text=True)
    assert r.returncode == 2  # no args -> usage
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_raise_swift.py -v`
Expected: FAIL (`sonari-raise.swift` missing).

- [ ] **Step 3: Implement the Swift helper**

```swift
// hotkeyd/sonari-raise.swift
// Sonari focus-follow helper. Holds a dedicated, recognizable Automation grant
// (so the consent dialog reads as Sonari's, not a shared /usr/bin/osascript).
//
//   sonari-raise <tty>   raise the visible Terminal window whose selected tab's
//                        tty == <tty>. Exit: 0 raised, 1 not-found/missed,
//                        3 Automation denied (-1743), 4 other AppleScript error.
//   sonari-raise --check send one harmless controlling Apple Event to surface /
//                        test the Automation grant. Exit: 0 granted, 3 denied, 4 other.
//
// AppleScript recipe is the empirically proven one (spec §3): match by tty,
// `set selected` + `set index ... to 1` + `activate`. NEVER `set frontmost of
// window` (it reverts the raise). Skip phantom windows (visible + tabs > 0).
//
// Build: swiftc hotkeyd/sonari-raise.swift -o ~/.sonari/sonari-raise

import Foundation

// Run an AppleScript; return (stringResult, exitCode). exitCode: 0 ok, 3 denied
// (-1743), 4 other error, 2 could-not-build-script.
func runAppleScript(_ src: String) -> (String, Int32) {
    var err: NSDictionary?
    guard let script = NSAppleScript(source: src) else { return ("", 2) }
    let desc = script.executeAndReturnError(&err)
    if let e = err {
        let n = (e[NSAppleScript.errorNumber] as? Int) ?? 0
        return ("ERR\(n)", n == -1743 ? 3 : 4)
    }
    return (desc.stringValue ?? "", 0)
}

let args = CommandLine.arguments

if args.count >= 2 && args[1] == "--check" {
    let (_, code) = runAppleScript("tell application \"Terminal\" to count windows")
    exit(code)
}

guard args.count >= 2 else {
    FileHandle.standardError.write(
        "usage: sonari-raise <tty> | --check\n".data(using: .utf8)!)
    exit(2)
}

let target = args[1]
let recipe = """
try
    tell application "Terminal"
        set picked to missing value
        repeat with w in windows
            try
                if visible of w and (count of tabs of w) > 0 then
                    if (tty of selected tab of w) is "\(target)" then
                        set picked to w
                        exit repeat
                    end if
                end if
            end try
        end repeat
        if picked is missing value then return "NOTFOUND"
        set selected of (selected tab of picked) to true
        set index of picked to 1
        activate
        delay 0.2
        if (tty of selected tab of front window) is "\(target)" then
            return "OK"
        else
            return "MISS"
        end if
    end tell
on error e number n
    return "ERR" & n
end try
"""

let (result, code) = runAppleScript(recipe)
if code != 0 { exit(code) }
exit(result == "OK" ? 0 : 1)
```

In `src/sonari/paths.py`, add next to `HOTKEYD_BIN_PATH`:

```python
RAISE_BIN_PATH = SONARI_DIR / "sonari-raise"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_raise_swift.py -v`
Expected: PASS (compiles, zero warnings, usage exit 2). On a host without swiftc the compile tests skip — that's acceptable; the build environment for this feature has swiftc.

- [ ] **Step 5: Commit**

```bash
git add hotkeyd/sonari-raise.swift src/sonari/paths.py tests/test_raise_swift.py
git commit -m "feat(raise): sonari-raise Swift helper (tty-match raise + --check grant probe)"
```

---

## Task 9: MacRaiseBackend (dispatch + grant check + build + doctor)

**Files:**
- Create: `src/sonari/platform/macos/raiser.py`
- Modify: `src/sonari/platform/macos/__init__.py` (swap `NoopRaiseBackend` → `MacRaiseBackend`)
- Test: `tests/test_macos_raise.py`

**Interfaces:**
- Consumes: `paths.RAISE_BIN_PATH` (Task 8), `RaiseBackend` (Task 5), `sessions.Identity` (Task 3).
- Produces: `MacRaiseBackend(RaiseBackend)` with `supports`, `raise_session`, `check_grant`, `build`, `doctor_rows`. Injectable seams `_run(argv, timeout)` (default `subprocess.run`) and `_helper_exists()` so tests avoid real processes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_macos_raise.py
import sys

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS raise backend", allow_module_level=True)

from sonari.platform.macos.raiser import MacRaiseBackend
from sonari.sessions import Identity


class FakeProc:
    def __init__(self, rc):
        self.returncode = rc


def _backend(rc=0, exists=True, recorder=None):
    be = MacRaiseBackend()
    be._helper_exists = lambda: exists
    def run(argv, timeout=None):
        if recorder is not None:
            recorder.append(argv)
        return FakeProc(rc)
    be._run = run
    return be


def test_supports_terminal_needs_tty():
    be = MacRaiseBackend()
    assert be.supports(Identity("Apple_Terminal", tty="/dev/ttys1")) is True
    assert be.supports(Identity("Apple_Terminal", tty="")) is False


def test_supports_iterm_needs_session_id():
    be = MacRaiseBackend()
    assert be.supports(Identity("iTerm.app", iterm_session_id="w0:ID")) is True
    assert be.supports(Identity("iTerm.app", iterm_session_id="")) is False


def test_supports_unknown_terminal_false():
    assert MacRaiseBackend().supports(Identity("Ghostty", tty="/dev/ttys1")) is False


def test_raise_terminal_execs_helper_with_tty():
    rec = []
    be = _backend(rc=0, recorder=rec)
    assert be.raise_session(Identity("Apple_Terminal", tty="/dev/ttys5")) is True
    assert rec[0][0].endswith("sonari-raise")
    assert rec[0][1] == "/dev/ttys5"


def test_raise_terminal_nonzero_is_false():
    assert _backend(rc=1).raise_session(Identity("Apple_Terminal", tty="/dev/ttys5")) is False


def test_raise_missing_helper_is_false():
    assert _backend(exists=False).raise_session(
        Identity("Apple_Terminal", tty="/dev/ttys5")) is False


def test_raise_iterm_opens_reveal_url():
    rec = []
    be = _backend(rc=0, recorder=rec)
    assert be.raise_session(Identity("iTerm.app", iterm_session_id="w0t0p0:ID")) is True
    assert rec[0][0] == "open"
    assert rec[0][1] == "iterm2:///reveal?sessionid=w0t0p0:ID"


def test_check_grant_maps_exit_codes():
    assert _backend(rc=0).check_grant() == "granted"
    assert _backend(rc=3).check_grant() == "denied"
    assert _backend(rc=4).check_grant() == "unknown"
    assert _backend(exists=False).check_grant() == "unknown"


def test_doctor_rows_shape():
    rows = _backend(rc=0).doctor_rows()
    assert all(len(r) == 3 for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_macos_raise.py -v`
Expected: FAIL (ModuleNotFoundError `sonari.platform.macos.raise`).

- [ ] **Step 3: Implement**

```python
# src/sonari/platform/macos/raiser.py
"""macOS focus-follow backend. Terminal.app -> exec the sonari-raise helper
(AppleScript, holds the Automation grant). iTerm2 -> open an iterm2:///reveal URL
(no grant needed). Everything else -> unsupported."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

from sonari import paths
from sonari.platform.base import RaiseBackend

_HELPER_TIMEOUT = 6.0


class MacRaiseBackend(RaiseBackend):
    # --- injectable seams (overridden in tests) ---
    def _run(self, argv, timeout=None):
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)

    def _helper_exists(self) -> bool:
        return os.path.exists(str(paths.RAISE_BIN_PATH))

    # --- capability ---
    def supports(self, identity) -> bool:
        if identity is None:
            return False
        tp = identity.term_program
        if tp == "Apple_Terminal":
            return bool(identity.tty)
        if tp == "iTerm.app":
            return bool(identity.iterm_session_id)
        return False

    # --- the raise ---
    def raise_session(self, identity) -> bool:
        if not self.supports(identity):
            return False
        try:
            if identity.term_program == "Apple_Terminal":
                if not self._helper_exists():
                    return False
                rc = self._run([str(paths.RAISE_BIN_PATH), identity.tty],
                               timeout=_HELPER_TIMEOUT).returncode
                return rc == 0
            if identity.term_program == "iTerm.app":
                url = "iterm2:///reveal?sessionid=" + identity.iterm_session_id
                rc = self._run(["open", url], timeout=_HELPER_TIMEOUT).returncode
                return rc == 0
        except Exception:  # noqa: BLE001 - never raise/hang the raise thread
            return False
        return False

    # --- permission ---
    def check_grant(self) -> str:
        if not self._helper_exists():
            return "unknown"
        try:
            rc = self._run([str(paths.RAISE_BIN_PATH), "--check"],
                           timeout=_HELPER_TIMEOUT).returncode
        except Exception:  # noqa: BLE001
            return "unknown"
        if rc == 0:
            return "granted"
        if rc == 3:
            return "denied"
        return "unknown"

    # --- build (mirror MacHotkeyBackend.build: skip if source unchanged to keep
    #     the Automation grant, which is keyed to the binary's cdhash) ---
    def build(self):
        if shutil.which("swiftc") is None:
            return (False, "swiftc not found")
        src = os.path.join(paths.repo_root(), "hotkeyd", "sonari-raise.swift")
        try:
            with open(src, "rb") as fh:
                src_hash = hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            return (False, "cannot read sonari-raise source: {0}".format(exc))
        hash_path = str(paths.SONARI_DIR / ".raise.srchash")
        if os.path.exists(str(paths.RAISE_BIN_PATH)):
            try:
                with open(hash_path, "r", encoding="utf-8") as fh:
                    if fh.read().strip() == src_hash:
                        return (True, "{0} (unchanged; kept to preserve the "
                                "Automation grant)".format(paths.RAISE_BIN_PATH))
            except OSError:
                pass
        rc = subprocess.call(["swiftc", src, "-o", str(paths.RAISE_BIN_PATH)])
        if rc == 0:
            try:
                with open(hash_path, "w", encoding="utf-8") as fh:
                    fh.write(src_hash)
            except OSError:
                pass
            return (True, str(paths.RAISE_BIN_PATH))
        return (False, "swiftc exited {0}".format(rc))

    # --- diagnostics ---
    def doctor_rows(self) -> "list":
        rows = []
        built = self._helper_exists()
        rows.append(("focus-follow helper", built,
                     str(paths.RAISE_BIN_PATH) if built
                     else "not built; run 'sonari install'"))
        if built:
            grant = self.check_grant()
            ok = grant == "granted"
            detail = {
                "granted": "Automation granted",
                "denied": "Automation denied — allow 'sonari-raise' to control "
                          "Terminal in System Settings > Privacy & Security > Automation",
                "unknown": "grant unknown (Terminal not running, or not yet granted)",
            }.get(grant, grant)
            rows.append(("focus-follow permission", ok, detail))
        return rows
```

In `src/sonari/platform/macos/__init__.py`, replace the `NoopRaiseBackend` wiring from Task 5:

```python
from sonari.platform.macos.raiser import MacRaiseBackend
# ... in make_backend(): replace raise_backend=NoopRaiseBackend() with:
        raise_backend=MacRaiseBackend(),
```

(Remove the now-unused `NoopRaiseBackend` import from `macos/__init__.py` if nothing else uses it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_macos_raise.py -v` then `python -m pytest tests/test_platform_raise_seam.py -v` (still green — `get_platform().raise_backend` is now `MacRaiseBackend`, still a `RaiseBackend`).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/platform/macos/raiser.py src/sonari/platform/macos/__init__.py tests/test_macos_raise.py
git commit -m "feat(macos): MacRaiseBackend (Terminal helper exec + iTerm reveal URL + grant check)"
```

---

## Task 10: CLI install builds the helper + proactive grant; doctor rows

**Files:**
- Modify: `src/sonari/cli.py` (`install()` `cli.py:348-407`; `doctor()` `cli.py:178-251`)
- Test: `tests/test_cli_focus_follow.py`

**Interfaces:**
- Consumes: `_platform().raise_backend` (`build`, `check_grant`, `doctor_rows`), `_platform().tts.run` (speak guidance), `load_config` (`focus_follow`).
- Produces: `install()` builds `sonari-raise` and, when `focus_follow` is on and the grant is not yet granted, speaks guidance and surfaces the consent dialog via `--check`; `doctor()` includes the raise backend's rows.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_focus_follow.py
from sonari import cli


class FakeRaise:
    def __init__(self, grant="granted"):
        self._grant = grant
        self.built = False
        self.checked = 0
    def build(self):
        self.built = True
        return (True, "/tmp/sonari-raise")
    def check_grant(self):
        self.checked += 1
        return self._grant
    def doctor_rows(self):
        return [("focus-follow helper", True, "ok")]


def test_doctor_includes_raise_rows(monkeypatch):
    fake = FakeRaise()

    class P:
        raise_backend = fake
        class supervisor:
            @staticmethod
            def doctor_rows(): return []
            @staticmethod
            def hooks_doctor_row(): return ("hooks", True, "ok")
        class hotkey:
            @staticmethod
            def doctor_rows(): return []
    monkeypatch.setattr(cli, "_platform", lambda: P)
    # avoid the daemon-socket + neural rows touching the real system
    monkeypatch.setattr(cli, "_send", lambda *a, **k: {"ok": True})
    rows = cli.doctor()
    assert ("focus-follow helper", True, "ok") in rows


def test_install_grant_step_builds_and_checks(monkeypatch):
    fake = FakeRaise(grant="denied")
    spoken = []
    # build_focus_follow is the small, unit-testable helper install() calls
    monkeypatch.setattr(cli, "_speak_once", lambda text: spoken.append(text))
    cli._focus_follow_setup(fake, focus_follow=True)
    assert fake.built is True
    assert fake.checked >= 1
    assert spoken  # spoke guidance because grant was 'denied'


def test_install_grant_step_silent_when_already_granted(monkeypatch):
    fake = FakeRaise(grant="granted")
    spoken = []
    monkeypatch.setattr(cli, "_speak_once", lambda text: spoken.append(text))
    cli._focus_follow_setup(fake, focus_follow=True)
    assert fake.built is True
    assert spoken == []  # no nagging when already granted


def test_install_grant_step_skipped_when_focus_follow_off(monkeypatch):
    fake = FakeRaise(grant="denied")
    spoken = []
    monkeypatch.setattr(cli, "_speak_once", lambda text: spoken.append(text))
    cli._focus_follow_setup(fake, focus_follow=False)
    assert fake.built is True   # still build the helper
    assert spoken == []         # but never prompt when the feature is off
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_focus_follow.py -v`
Expected: FAIL (`cli._focus_follow_setup` / `cli._speak_once` not defined; doctor lacks raise rows).

- [ ] **Step 3: Implement**

In `src/sonari/cli.py`, add the helpers (near the other module-level helpers):

```python
def _speak_once(text: str) -> None:
    """Best-effort synchronous speech via the platform TTS backend (used by the
    install grant flow; the daemon isn't a reliable speaker mid-install)."""
    try:
        tts = _platform().tts
        proc = tts.run(text, tts.best_voice(), 200)
        if proc is not None:
            proc.wait(timeout=15)
    except Exception:  # noqa: BLE001 - guidance speech must never break install
        pass


def _focus_follow_setup(raise_backend, focus_follow: bool) -> None:
    """Build the sonari-raise helper and, when focus-follow is on and the
    Automation grant isn't in place, speak guidance and surface the consent
    dialog (eyes-free users can't discover a silent dialog on first jump)."""
    ok, detail = raise_backend.build()
    print("focus-follow helper: {0}".format(detail))
    if not ok or not focus_follow:
        return
    grant = raise_backend.check_grant()
    if grant == "granted":
        return
    try:
        subprocess.call(["afplay", "/System/Library/Sounds/Glass.aiff"])
    except Exception:  # noqa: BLE001
        pass
    _speak_once("Focus follow needs permission. A dialog will appear. "
                "Click Allow to let Sonari raise your terminal window.")
    raise_backend.check_grant()  # second call surfaces the dialog at this moment
    print("focus-follow: if window-raise doesn't work, grant Automation to "
          "'sonari-raise' in System Settings > Privacy & Security > Automation.")
```

Confirm `import subprocess` is present at the top of `cli.py` (add it if not).

In `install()` (`cli.py`), after step 6 (the hotkey install block) and before step 7 (voice check), add:

```python
    # 6b. Focus-follow: build the sonari-raise helper + proactive Automation grant.
    try:
        cfg = load_config()
        _focus_follow_setup(_platform().raise_backend,
                            bool(cfg.get("focus_follow", True)))
    except Exception:  # noqa: BLE001 - focus-follow setup must never break install
        pass
```

In `doctor()` (`cli.py`), after the `_platform().hotkey.doctor_rows()` line, add:

```python
    results.extend(_platform().raise_backend.doctor_rows())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_focus_follow.py -v` then `python -m pytest tests/test_cli_install.py tests/test_cli_doctor.py -q` (reconcile any doctor-row-count assertions to include the new rows).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sonari/cli.py tests/test_cli_focus_follow.py
git commit -m "feat(cli): build sonari-raise + proactive Automation grant on install; doctor rows"
```

---

## Task 11: Build-time empirical verification (manual protocol, no CI)

**Files:** none (verification only). Record the outcome in `docs/superpowers/specs/2026-06-20-sonari-focus-follow-design.md` §8 (replace the two "unverified" bullets with the observed result).

This confirms the two facts the spec flagged as unverified. It is run by the implementing author after `sonari install` on a Tahoe macOS box; the iTerm2 step needs one physical action from the user (a legitimate Glass cue). **Do not skip — the OS raise is not exercised by any unit test.**

- [ ] **Step 1: Full suite green + install**

Run: `python -m pytest -q --ignore=tests/test_kokoro.py` (all green). Then `sonari install` (builds `sonari-raise`, runs the proactive grant flow). Confirm `sonari doctor` shows the two focus-follow rows.

- [ ] **Step 2: Terminal.app raise (self-verifying, author-only)**

With ≥2 Terminal windows open and a non-Terminal app frontmost, capture a real background tab's tty (`osascript -e 'tell application "Terminal" to return tty of selected tab of window 2'`), then run `~/.sonari/sonari-raise <that-tty>` and confirm exit 0 and that Terminal came forward with the right tab. (This reconfirms the proven recipe through the SHIPPED helper, in the daemon-equivalent path.) Record PASS/FAIL.

- [ ] **Step 3: iTerm2 reveal-URL on Tahoe (the flagged unknown; one user action)**

Open Claude Code in an iTerm2 window; in a SECOND foreground app, run `open "iterm2:///reveal?sessionid=$ITERM_SESSION_ID_OF_THE_ITERM_SESSION"` from a background launchd-equivalent context and confirm iTerm2's correct session comes forward. If it does NOT raise on Tahoe (the `open`/LaunchServices path is fought), switch `MacRaiseBackend._raise_iterm` to the AppleScript-via-helper fallback (select session whose id == the GUID, then activate) — extend `sonari-raise.swift` with an `--iterm <id>` mode mirroring the Terminal recipe — and re-verify. Record the outcome and which path shipped.

- [ ] **Step 4: Update the spec + commit**

Edit §8 of the design spec to record the verified results (Terminal.app helper, iTerm2 path chosen, consent dialog name observed). Commit:

```bash
git add docs/superpowers/specs/2026-06-20-sonari-focus-follow-design.md
git commit -m "docs(focus-follow): record build-time verification results"
```

---

## Self-Review (completed by plan author)

**Spec coverage:** §3 findings → Tasks 1/2/8 (tty, identity, recipe); §4.1 capture → Tasks 1–2; §4.2 SessionManager → Task 3; §4.3 RaiseService/RaiseBackend → Tasks 5–6; §4.4 helper → Task 8 + build in Task 9; §4.5 daemon wiring + supersession → Tasks 6–7; §4.6 config → Task 4; §4.7 proactive grant/doctor → Tasks 9–10; §5 fallback → Task 7 (cue) + Tasks 6/9 (False paths); §6 testing → every task's tests + Task 11; §8 iTerm2/grant-name unknowns → Task 11. No uncovered requirement.

**Type consistency:** `Identity(term_program, tty, iterm_session_id)` used identically in Tasks 2/3/6/7/9. `RaiseBackend.{raise_session,supports,check_grant,doctor_rows}` consistent across Tasks 5/6/9/10. `RaiseService.{will_attempt,bump_generation,raise_async,current_generation,join}` consistent Tasks 6/7. `paths.RAISE_BIN_PATH` Tasks 8/9. Helper exit codes (0/1/2/3/4) consistent Tasks 8/9.

**Known reconciliations flagged for the implementer (not placeholders — they require reading the actual current assertions):** Task 5 Step 4 (any hand-built `PlatformBackend(...)` in tests needs the new field), Task 7 Step 4 (existing JUMP_WAITING preamble assertions in `test_daemon_streams.py` shift when `will_attempt` is False), Task 10 Step 4 (doctor row-count assertions). Each names the exact file and the exact behavior to reconcile to.
