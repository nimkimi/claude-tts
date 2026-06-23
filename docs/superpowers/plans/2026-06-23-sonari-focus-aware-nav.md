# Focus-Aware Per-Session Navigation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the arrow-navigation hotkeys to the session in the OS-focused terminal window (not the last-prompted session), so reading the window you're looking at navigates *that* session's transcript.

**Architecture:** A background focus-watcher inside `sonari-hotkeyd` reports the frontmost terminal's identity to the daemon as a new `os_focus` message. `SessionManager` resolves that identity to a live session; `on_nav` targets `focused_session() or foreground()` — focus overrides, absence falls back to today's behavior. The Apple-Event read reuses `sonari-raise`'s existing Automation grant via two new read subcommands.

**Tech Stack:** Python 3 (daemon, pytest), Swift (`swiftc`, Carbon/Cocoa) for the two macOS helper binaries. No new dependencies.

## Global Constraints

- **macOS only**; supported terminals are **Apple Terminal.app** and **iTerm2**. Anything else → fall back to `foreground()`.
- **Navigation hotkeys ONLY** follow OS focus. `pause`, `mute`, `stop`, `skip`, `jump_decision`, `jump_waiting`, `pin_toggle`, `reread_options`, etc. keep routing on `foreground()`. Do not touch them.
- **Routing is ADDITIVE:** `target = focused_session() or foreground()`. `focused_session()` defaults to `None`, so with no OS-focus signal every existing nav test still routes to `foreground()` and stays green. Never make nav route *purely* on focus.
- **Non-empty identity matching only:** an empty incoming `tty`/`iterm_session_id` matches no session (mirrors the existing "don't clobber with empties" rule).
- **No new permission type, no Accessibility.** The front-tab read goes through `sonari-raise` (reuses its Automation grant). Editing `sonari-raise.swift` changes its cdhash → the grant is dropped → **one** re-grant at next `sonari install` (the *same* Automation permission, surfaced with a note on the recompiled path only).
- **Handlers run inside the daemon lock** (via `_state.transaction()` on both the socket and hotkey paths). `_enqueue` does **not** take the lock — callers already hold it. **Never** re-acquire `self._lock` inside `on_nav`/`on_os_focus`.
- **Wire `type` value is the string** `"os_focus"` (the value of `MsgType.OS_FOCUS`), not `"OS_FOCUS"`.
- **Adding a MsgType touches FOUR places** or the package fails at import: `protocol.py`, the `assert_complete([...])` list in `daemon/__init__.py`, the side-effect import in `host.py` (only if the handler lives in a *new* module — we reuse `focus.py`, already imported, so no `host.py` change), and `tests/test_daemon_registry.py`'s `ALL_27` list (→ 28).
- **Run tests:** `.venv/bin/python -m pytest -q` from the repo root.
- **Commit messages:** do NOT include any `claude.ai/code/session_...` footer (standing repo rule).

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/sonari/sessions.py` | Resolve & store the OS-focused session (`set_os_focus`, `focused_session`, `_bare_iterm_guid`) | Modify |
| `src/sonari/protocol.py` | Add `OS_FOCUS` message type | Modify |
| `src/sonari/daemon/features/focus.py` | New `@handler(MsgType.OS_FOCUS) on_os_focus` (session-less, mirrors `on_jump_waiting`) | Modify |
| `src/sonari/daemon/__init__.py` | Add `MsgType.OS_FOCUS` to the `assert_complete` completeness guard | Modify |
| `src/sonari/daemon/features/navigation.py` | `on_nav` → `focused_session() or foreground()` + cross-session voice move + folder cue | Modify |
| `hotkeyd/sonari-raise.swift` | `--front-tty` / `--front-iterm` read subcommands (reuse Automation grant) | Modify |
| `hotkeyd/sonari-hotkeyd.swift` | Background focus-watcher (NSWorkspace gate + poll + exec sonari-raise + send `os_focus`) | Modify |
| `src/sonari/cli/__init__.py` | `_build_raise_helper`: print the re-grant note on the recompiled path | Modify |
| `tests/test_sessions.py` | `set_os_focus`/`focused_session` unit tests | Modify |
| `tests/test_daemon_focus_nav.py` | `on_os_focus` handler + `on_nav` focus-aware routing tests | Create |
| `tests/test_daemon_registry.py` | Add `OS_FOCUS` to `ALL_27` (→ `ALL_28`) | Modify |
| `tests/test_cli_install_notes.py` | `_build_raise_helper` re-grant-note test | Create |

Tasks 1–4 + the Python part of Task 4 are fully TDD-verifiable by the implementer. Tasks 5–6 (Swift watcher + acceptance) are verified by a build-time spike and a final on-hardware human gate — never with the user as a test harness.

---

### Task 1: `SessionManager` — resolve & store OS focus

**Files:**
- Modify: `src/sonari/sessions.py`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Produces: `SessionManager.set_os_focus(term_program="", tty="", iterm_session_id="", focused=True) -> None`; `SessionManager.focused_session() -> "str | None"`; module fn `_bare_iterm_guid(s: str) -> str`.
- Consumes: existing `self._sessions` (`dict[str, str|None]`), `self._identities` (`dict[str, Identity]`), `Identity(term_program, tty, iterm_session_id)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_sessions.py` (append; the file already imports `from sonari.sessions import SessionManager, Identity`):

```python
def test_os_focus_starts_none():
    sm = SessionManager()
    assert sm.focused_session() is None


def test_os_focus_resolves_terminal_by_tty():
    sm = SessionManager()
    sm.register("a"); sm.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sm.register("b"); sm.set_identity("b", Identity(term_program="Apple_Terminal", tty="/dev/ttys002"))
    sm.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys002")
    assert sm.focused_session() == "b"


def test_os_focus_empty_tty_matches_nothing():
    sm = SessionManager()
    sm.register("a"); sm.set_identity("a", Identity(term_program="Apple_Terminal", tty=""))
    sm.set_os_focus(term_program="Apple_Terminal", tty="")
    assert sm.focused_session() is None


def test_os_focus_no_match_returns_none():
    sm = SessionManager()
    sm.register("a"); sm.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sm.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys999")
    assert sm.focused_session() is None


def test_os_focus_false_clears():
    sm = SessionManager()
    sm.register("a"); sm.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sm.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")
    assert sm.focused_session() == "a"
    sm.set_os_focus(focused=False)
    assert sm.focused_session() is None


def test_os_focus_resolves_iterm_by_bare_guid():
    sm = SessionManager()
    sm.register("a")
    sm.set_identity("a", Identity(term_program="iTerm.app", iterm_session_id="w0t1p2:ABCD-1234"))
    # watcher sends the BARE guid from `id of current session`
    sm.set_os_focus(term_program="iTerm.app", iterm_session_id="ABCD-1234")
    assert sm.focused_session() == "a"


def test_focused_session_none_after_unregister():
    sm = SessionManager()
    sm.register("a"); sm.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sm.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")
    assert sm.focused_session() == "a"
    sm.unregister("a")
    assert sm.focused_session() is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_sessions.py -k os_focus`
Expected: FAIL — `AttributeError: 'SessionManager' object has no attribute 'set_os_focus'`.

- [ ] **Step 3: Implement**. Add the module-level helper (top of `sessions.py`, near `_basename`):

```python
def _bare_iterm_guid(s: str) -> str:
    """iTerm2's ITERM_SESSION_ID is 'wNtNpN:GUID'; the scriptable `id of session`
    is the bare GUID after the last ':'. Return the part after the last ':', else s."""
    if not s:
        return ""
    tail = s.rpartition(":")[2]
    return tail or s
```

In `SessionManager.__init__`, add the field (next to `self._pinned`):

```python
        self._os_focused_session: "str | None" = None    # session in the OS-focused terminal
```

In `unregister`, add (after the `_pinned` clear):

```python
        if self._os_focused_session == session:
            self._os_focused_session = None
```

Add the two methods (after `focus`):

```python
    def set_os_focus(self, term_program: str = "", tty: str = "",
                     iterm_session_id: str = "", focused: bool = True) -> None:
        """Record which terminal currently has OS keyboard focus, resolved to a live
        session. `focused=False` (or an unresolvable identity) clears it. Match is by
        NON-EMPTY identity only: tty for Apple_Terminal, bare GUID for iTerm.app. This
        is the INBOUND focus signal — distinct from focus()/foreground() (the voice)."""
        if not focused:
            self._os_focused_session = None
            return
        match = None
        if term_program == "Apple_Terminal" and tty:
            for sess, ident in self._identities.items():
                if ident.tty and ident.tty == tty:
                    match = sess
                    break
        elif term_program == "iTerm.app" and iterm_session_id:
            want = _bare_iterm_guid(iterm_session_id)
            for sess, ident in self._identities.items():
                if ident.iterm_session_id and _bare_iterm_guid(ident.iterm_session_id) == want:
                    match = sess
                    break
        self._os_focused_session = match

    def focused_session(self) -> "str | None":
        """The session whose terminal has OS keyboard focus, iff still registered.
        Returns None when focus is unknown/unmapped — callers fall back to foreground()."""
        s = self._os_focused_session
        return s if (s is not None and s in self._sessions) else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_sessions.py`
Expected: PASS (all, including the pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/sonari/sessions.py tests/test_sessions.py
git commit -m "feat(sessions): resolve OS-focused session by terminal identity

set_os_focus(term_program, tty/iterm_session_id) maps the frontmost
terminal to a live session (non-empty tty / bare iTerm GUID match);
focused_session() returns it iff registered, else None for fallback."
```

---

### Task 2: Protocol + `on_os_focus` handler + completeness guard

**Files:**
- Modify: `src/sonari/protocol.py`, `src/sonari/daemon/features/focus.py`, `src/sonari/daemon/__init__.py`
- Test: `tests/test_daemon_registry.py`, `tests/test_daemon_focus_nav.py` (create)

**Interfaces:**
- Produces: `MsgType.OS_FOCUS == "os_focus"`; `@handler(MsgType.OS_FOCUS) def on_os_focus(ctx, msg)` (returns `None`).
- Consumes: `ctx.host.sessions.set_os_focus(...)` from Task 1; `make_daemon`, `stream_queue` from `tests.daemon_helpers`; `Identity` from `sonari.sessions`.

- [ ] **Step 1: Write the failing tests**. Create `tests/test_daemon_focus_nav.py`:

```python
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon


def _drain(queue):
    items = []
    while True:
        it = queue.pop_next()
        if it is None:
            break
        items.append(it)
    return items


def test_os_focus_message_resolves_focused_session():
    daemon, _q, _s, sessions, _c = make_daemon(foreground="a")
    sessions.register("a", cwd="/work/frontend")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    daemon.handle_message({"type": "os_focus",
                           "term_program": "Apple_Terminal", "tty": "/dev/ttys001"})
    assert sessions.focused_session() == "a"


def test_os_focus_false_message_clears_focus():
    daemon, _q, _s, sessions, _c = make_daemon(foreground="a")
    sessions.register("a", cwd="/work/frontend")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    daemon.handle_message({"type": "os_focus",
                           "term_program": "Apple_Terminal", "tty": "/dev/ttys001"})
    daemon.handle_message({"type": "os_focus", "focused": False})
    assert sessions.focused_session() is None
```

Also extend `tests/test_daemon_registry.py`: add `_MsgType.OS_FOCUS,` to the `ALL_27` list (line ~115), rename the list `ALL_27` → `ALL_28` and the function `test_all_27_msgtypes_registered` → `test_all_28_msgtypes_registered`, and update both in-body references (`for t in ALL_27` → `ALL_28`, and `reg.assert_complete(ALL_27)` → `ALL_28` in `test_negative_assert_complete_names_missing_type`).

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_daemon_focus_nav.py tests/test_daemon_registry.py`
Expected: FAIL — handler test no-ops (unknown `os_focus` type → `_ignore`, `focused_session()` stays None); and importing `sonari.daemon` still has 27 handlers so `ALL_28` lists a type with no handler.

- [ ] **Step 3: Implement**. In `src/sonari/protocol.py`, add to `class MsgType` (after `RELOAD_KEYMAP`):

```python
    OS_FOCUS = "os_focus"   # focus-watcher: which terminal (tty / iterm id) has OS keyboard focus
```

In `src/sonari/daemon/features/focus.py`, add the handler (it already imports `MsgType` and `handler`):

```python
@handler(MsgType.OS_FOCUS)
def on_os_focus(ctx, msg):
    """Inbound OS-focus signal from the focus-watcher. Session-less: reads the front
    terminal's identity off the message and resolves it to a session. Fire-and-forget."""
    ctx.host.sessions.set_os_focus(
        term_program=msg.get("term_program", ""),
        tty=msg.get("tty", ""),
        iterm_session_id=msg.get("iterm_session_id", ""),
        focused=msg.get("focused", True),
    )
    return None
```

In `src/sonari/daemon/__init__.py`, add `MsgType.OS_FOCUS,` to the `assert_complete([...])` list and update the comment count `27` → `28`.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q tests/test_daemon_focus_nav.py tests/test_daemon_registry.py`
Expected: PASS.

- [ ] **Step 5: Full-suite regression check** (the registry guard runs at import; confirm nothing else broke)

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (full suite).

- [ ] **Step 6: Commit**

```bash
git add src/sonari/protocol.py src/sonari/daemon/features/focus.py src/sonari/daemon/__init__.py tests/test_daemon_registry.py tests/test_daemon_focus_nav.py
git commit -m "feat(daemon): os_focus message + on_os_focus handler

New MsgType.OS_FOCUS carries the frontmost terminal identity; the
session-less handler forwards it to SessionManager.set_os_focus. Added
to the assert_complete completeness guard and the registry test (28)."
```

---

### Task 3: `on_nav` — focus-aware routing + voice move + folder cue

**Files:**
- Modify: `src/sonari/daemon/features/navigation.py`
- Test: `tests/test_daemon_focus_nav.py`

**Interfaces:**
- Consumes: `sessions.focused_session()`/`foreground()`/`focus()`/`folder()` (Tasks 1 + existing); `ctx.host._enqueue(session, kind, text, is_decision, ..., mute_exempt, at_front, names_session)`; existing `_nav`/`_nav_response`.
- Produces: the only behavior change — nav targets the OS-focused session when one is mapped.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_daemon_focus_nav.py`; `_drain` and imports already present):

```python
def _seed(daemon, session):
    h = daemon.history
    h.record(session, "prose", session + "-m0"); h.end_message(session)
    h.record(session, "prose", session + "-m1")


def test_nav_targets_os_focused_session_not_foreground():
    # B last-prompted (foreground/voice), but A's terminal is OS-focused.
    daemon, _q, _s, sessions, _c = make_daemon(foreground="a")
    sessions.register("a", cwd="/work/frontend")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sessions.register("b", cwd="/work/api")
    sessions.set_identity("b", Identity(term_program="Apple_Terminal", tty="/dev/ttys002"))
    sessions.set_foreground("b")
    _seed(daemon, "a")
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")

    daemon.handle_message({"type": "nav", "to": "prev"})

    a_texts = [it.text for it in _drain(daemon._stream("a").queue)]
    assert a_texts and a_texts[-1] == "a-m1"          # A was navigated
    assert _drain(daemon._stream("b").queue) == []     # B untouched
    assert sessions.foreground() == "a"                # voice moved to A
    assert a_texts[0] == "frontend."                   # cross-session folder cue, first


def test_nav_falls_back_to_foreground_when_no_os_focus():
    daemon, queue, _s, sessions, _c = make_daemon(foreground="fg")
    _seed(daemon, "fg")
    # no set_os_focus -> focused_session() is None
    daemon.handle_message({"type": "nav", "to": "prev"})
    assert [it.text for it in _drain(queue)] == ["fg-m1"]
    assert sessions.foreground() == "fg"


def test_within_focused_session_nav_no_voice_move_no_cue():
    daemon, queue, _s, sessions, _c = make_daemon(foreground="fg")
    sessions.register("fg", cwd="/work/frontend")
    sessions.set_identity("fg", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sessions.set_foreground("fg")
    sessions.pin_toggle()                              # pin fg
    _seed(daemon, "fg")
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")  # focus == voice
    daemon.handle_message({"type": "nav", "to": "prev"})
    texts = [it.text for it in _drain(queue)]
    assert texts == ["fg-m1"]                          # no "frontend." cue prepended
    assert sessions.pinned() == "fg"                   # within-session nav preserves the pin


def test_cross_session_nav_overrides_pin():
    daemon, _q, _s, sessions, _c = make_daemon(foreground="b")
    sessions.register("a", cwd="/work/frontend")
    sessions.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sessions.register("b", cwd="/work/api")
    sessions.set_foreground("b"); sessions.pin_toggle()   # pin b
    assert sessions.pinned() == "b"
    _seed(daemon, "a")
    sessions.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")
    daemon.handle_message({"type": "nav", "to": "prev"})
    assert sessions.foreground() == "a"
    assert sessions.pinned() is None                   # cross-session nav clears the pin (like jump)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_daemon_focus_nav.py -k nav`
Expected: FAIL — current `on_nav` routes to `foreground()` (= "b"), so A's queue is empty and `a_texts` is falsy; `foreground()` stays "b".

- [ ] **Step 3: Implement**. Replace `on_nav` in `src/sonari/daemon/features/navigation.py` (the current body is `fg = ctx.host.sessions.foreground(); if fg is None: return None; ...`):

```python
@handler(MsgType.NAV)
def on_nav(ctx, msg):
    sessions = ctx.host.sessions
    target = sessions.focused_session() or sessions.foreground()
    if target is None:
        return None
    crossed = target != sessions.foreground()     # compute BEFORE focus() moves it
    if crossed:
        sessions.focus(target)                     # move the voice to the navigated session
    to = msg.get("to", "prev")
    if to in ("prev_response", "next_response"):
        _nav_response(ctx, target, to)             # both clear target queue, then enqueue transcript
    else:
        _nav(ctx, target, to)
    if crossed:
        # Lead with a short folder cue so an eyes-free user knows the voice jumped.
        # Enqueue AFTER _nav (its queue.clear() would drop an earlier enqueue); at_front
        # so it still plays first. names_session claims the session, suppressing the
        # auto folder-prefix on the following item (no double-announce) — mirrors on_jump_waiting.
        folder = sessions.folder(target)
        if folder:
            ctx.host._enqueue(target, "prose", folder + ".", False,
                              mute_exempt=True, at_front=True, names_session=True)
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q tests/test_daemon_focus_nav.py`
Expected: PASS.

- [ ] **Step 5: Full nav-suite regression** (the additive-routing invariant — existing nav tests must stay green)

Run: `.venv/bin/python -m pytest -q tests/test_daemon_nav.py`
Expected: PASS (unchanged — no `os_focus` set → `focused_session()` None → `foreground()`).

- [ ] **Step 6: Commit**

```bash
git add src/sonari/daemon/features/navigation.py tests/test_daemon_focus_nav.py
git commit -m "feat(nav): navigate the OS-focused session, not just foreground

on_nav now targets focused_session() or foreground() (additive: focus
overrides, absence falls back). Cross-session nav moves the voice to the
target and leads with a folder cue; within-session nav is unchanged."
```

---

### Task 4: `sonari-raise` read subcommands + install re-grant note

**Files:**
- Modify: `hotkeyd/sonari-raise.swift`, `src/sonari/cli/__init__.py`
- Test: `tests/test_cli_install_notes.py` (create) — Python note only; the Swift is spike-verified.

**Interfaces:**
- Produces: `sonari-raise --front-tty` → prints front Terminal tab's tty (`/dev/ttysNNN`) to stdout, exit 0; `sonari-raise --front-iterm` → prints the bare GUID of iTerm's current session, exit 0. Non-zero + no stdout on any failure.
- Consumes: existing `runAppleScript(_:) -> (String, Int32)` in `sonari-raise.swift`; `paths.RAISE_BIN_PATH`; existing `raise_backend.build() -> (ok, detail)`.

- [ ] **Step 1: Add the read subcommands** to `hotkeyd/sonari-raise.swift`. Insert immediately after the `--check-iterm` block (before the `guard args.count >= 2` for the `<tty>` path):

```swift
if args.count >= 2 && args[1] == "--front-tty" {
    // Read the selected tab's tty of Terminal's front window (the OS-focused tab).
    let (out, code) = runAppleScript(
        "tell application \"Terminal\" to get tty of selected tab of front window")
    if code != 0 { exit(code) }
    let tty = out.trimmingCharacters(in: .whitespacesAndNewlines)
    if tty.isEmpty { exit(1) }
    print(tty)
    exit(0)
}

if args.count >= 2 && args[1] == "--front-iterm" {
    // Read the bare GUID of iTerm2's current session (matches the captured ITERM_SESSION_ID tail).
    let (out, code) = runAppleScript(
        "tell application \"iTerm2\" to get id of current session of current tab of current window")
    if code != 0 { exit(code) }
    let gid = out.trimmingCharacters(in: .whitespacesAndNewlines)
    if gid.isEmpty { exit(1) }
    print(gid)
    exit(0)
}
```

Also update the `usage:` strings in the file to mention `--front-tty | --front-iterm`.

- [ ] **Step 2: Build & spike-verify the read** (self-verified via independent osascript readback — NOT Nima-as-harness). On the dev box:

```bash
swiftc hotkeyd/sonari-raise.swift -o /tmp/sonari-raise-spike
/tmp/sonari-raise-spike --front-tty
# Expected: prints the CURRENT front Terminal window's selected-tab tty, e.g. /dev/ttys003
# Cross-check it matches AppleScript directly:
osascript -e 'tell application "Terminal" to get tty of selected tab of front window'
# The two must be identical. (--front-iterm only if iTerm2 is installed/running.)
```

Expected: `--front-tty` output equals the osascript readback. Document the observed value in the commit message. (This may surface a one-time Automation prompt for `/tmp/sonari-raise-spike`; that's fine — the spike binary is throwaway.)

- [ ] **Step 3: Write the failing test** for the install re-grant note. Create `tests/test_cli_install_notes.py`:

```python
from sonari import paths
from sonari.cli import _build_raise_helper


class _Backend:
    def __init__(self, detail):
        self._detail = detail

    def build(self):
        return (True, self._detail)


def test_regrant_note_printed_on_recompile(capsys):
    # build() returns detail == str(out) when it actually recompiled (cdhash changed).
    _build_raise_helper(_Backend(str(paths.RAISE_BIN_PATH)))
    out = capsys.readouterr().out
    assert "re-allow 'sonari-raise'" in out


def test_no_regrant_note_when_unchanged(capsys):
    _build_raise_helper(_Backend(str(paths.RAISE_BIN_PATH)
                                 + " (unchanged; kept to preserve the Automation grant)"))
    out = capsys.readouterr().out
    assert "re-allow 'sonari-raise'" not in out
```

- [ ] **Step 4: Run to verify failure**

Run: `.venv/bin/python -m pytest -q tests/test_cli_install_notes.py`
Expected: FAIL — current `_build_raise_helper` prints only the first-jump note, never "re-allow".

- [ ] **Step 5: Implement** the note. Replace `_build_raise_helper` in `src/sonari/cli/__init__.py`:

```python
def _build_raise_helper(raise_backend) -> None:
    """Build the sonari-raise helper. macOS asks for Automation permission the first
    time it controls a window - one-time, per app, with a safe voice fallback. Editing
    the helper changes its cdhash and DROPS the grant, so on a recompile we tell the
    user to re-allow it (focus-follow AND focus-aware navigation both depend on it)."""
    from sonari import paths
    ok, detail = raise_backend.build()
    print("focus-follow helper: {0}".format(detail))
    if not ok:
        return
    recompiled = (detail == str(paths.RAISE_BIN_PATH))
    if recompiled:
        print("focus-follow + focus-aware navigation: macOS will ask to re-allow "
              "'sonari-raise' to control Terminal/iTerm2 - click Allow (the same "
              "one-time grant).")
    else:
        print("focus-follow: the first time you jump into a Terminal or iTerm2 "
              "window, macOS will ask to allow 'sonari-raise' to control it - "
              "click Allow. One-time, per app.")
```

- [ ] **Step 6: Run to verify pass**

Run: `.venv/bin/python -m pytest -q tests/test_cli_install_notes.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add hotkeyd/sonari-raise.swift src/sonari/cli/__init__.py tests/test_cli_install_notes.py
git commit -m "feat(raise): --front-tty/--front-iterm read modes + install re-grant note

sonari-raise gains read-only front-window subcommands (reused by the
focus-watcher, reusing its Automation grant). Editing the helper drops
the cdhash grant, so install prints a re-grant note on the recompiled path.
Spike: --front-tty == osascript readback on the dev box."
```

---

### Task 5: `sonari-hotkeyd` focus-watcher

**Files:**
- Modify: `hotkeyd/sonari-hotkeyd.swift`

**Interfaces:**
- Consumes: existing `sendMessage(_:)`, `sonariDir()`; `sonari-raise --front-tty/--front-iterm` (Task 4); `NSWorkspace`.
- Produces: `os_focus` JSON lines to the daemon on every front-tab change; `{"focused":false}` when no supported terminal is frontmost. No unit tests (OS I/O) — spike + Task 6 acceptance.

- [ ] **Step 1: Add the watcher** to `hotkeyd/sonari-hotkeyd.swift`. Insert the helpers before the run-loop section (after `sendMessage`), then wire the observer + timer just before `app.run()`:

```swift
// --- Focus-watcher: report which terminal (tty / iTerm id) has OS keyboard focus.
// Cheap NSWorkspace gate (no permission); the Apple-Event read is delegated to
// sonari-raise (reuses its Automation grant) on a background queue so the slow read
// never blocks the hotkey run loop. Sends only on change. ---
let terminalBundles: [String: String] = [
    "com.apple.Terminal": "Apple_Terminal",
    "com.googlecode.iterm2": "iTerm.app",
]
let focusQueue = DispatchQueue(label: "sonari.focuswatch")
var lastFocusLine: String? = nil   // touched only on focusQueue

func raiseBinPath() -> String {
    return (sonariDir() as NSString).appendingPathComponent("sonari-raise")
}

// Run `sonari-raise <arg>`, return trimmed stdout, or nil on non-zero/failure.
func readFront(_ arg: String) -> String? {
    let bin = raiseBinPath()
    guard FileManager.default.isExecutableFile(atPath: bin) else { return nil }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: bin)
    p.arguments = [arg]
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = FileHandle.nullDevice
    do { try p.run() } catch { return nil }
    p.waitUntilExit()
    if p.terminationStatus != 0 { return nil }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    let s = String(data: data, encoding: .utf8)?
        .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    return s.isEmpty ? nil : s
}

func jsonLine(_ obj: [String: Any]) -> String? {
    guard let d = try? JSONSerialization.data(withJSONObject: obj) else { return nil }
    return String(data: d, encoding: .utf8)
}

// Read frontmost app on the main thread (AppKit), then do the slow read off-thread.
func pollFocus() {
    let bundle = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
    let term = terminalBundles[bundle]
    focusQueue.async {
        var msg: [String: Any]
        if term == "Apple_Terminal", let tty = readFront("--front-tty") {
            msg = ["type": "os_focus", "term_program": "Apple_Terminal", "tty": tty]
        } else if term == "iTerm.app", let gid = readFront("--front-iterm") {
            msg = ["type": "os_focus", "term_program": "iTerm.app", "iterm_session_id": gid]
        } else if term == nil {
            msg = ["type": "os_focus", "focused": false]
        } else {
            return   // terminal frontmost but read failed -> keep last state (stale-safe)
        }
        guard let line = jsonLine(msg) else { return }
        if line == lastFocusLine { return }     // change-detection: no idle traffic
        lastFocusLine = line
        sendMessage(line)
    }
}
```

Wire-up just before `app.run()`:

```swift
// Focus-watcher: app-activation events + a light poll (catches window/tab switches
// WITHIN a terminal app, which fire no NSWorkspace notification).
NSWorkspace.shared.notificationCenter.addObserver(
    forName: NSWorkspace.didActivateApplicationNotification,
    object: nil, queue: .main) { _ in pollFocus() }
let focusTimer = Timer(timeInterval: 0.5, repeats: true) { _ in pollFocus() }
RunLoop.main.add(focusTimer, forMode: .common)
pollFocus()   // report initial focus
```

- [ ] **Step 2: Build & spike-verify** end-to-end without touching the live install. On the dev box:

```bash
swiftc hotkeyd/sonari-hotkeyd.swift -o /tmp/sonari-hotkeyd-spike
# Ensure the rebuilt sonari-raise (Task 4) is at ~/.sonari/sonari-raise and granted.
# Run the spike watcher; in another terminal, tail the daemon stderr (or temporarily add a
# debug print in on_os_focus). Switch between two Terminal windows/tabs and confirm an
# os_focus line with the correct tty is emitted on each switch, and {"focused":false}
# when a non-terminal app is frontmost. Kill the spike when done.
```

Expected: one `os_focus` per focus change with the correct front tty; `{"focused":false}` off-terminal; no traffic while idle in one tab. Verified by the implementer, not by the user.

- [ ] **Step 3: Commit**

```bash
git add hotkeyd/sonari-hotkeyd.swift
git commit -m "feat(hotkeyd): background focus-watcher emits os_focus

NSWorkspace frontmost-app gate + 0.5s poll (catches in-app window/tab
switches); delegates the front-tab read to sonari-raise off the run loop;
sends os_focus only on change. Spike-verified end-to-end on the dev box."
```

---

### Task 6: On-hardware acceptance (human gate)

**Files:** none (verification only).

This is the final gate — like the M2 human gate that caught defects synthetic tests missed. It needs two real Claude sessions and is the one step the user performs (eyes-free), so it is scheduled deliberately, never mid-build.

- [ ] **Step 1:** Install the rebuilt binaries into a **sacrificial** setup (or run the spike binaries pointed at a scratch daemon) — do **not** clobber the live `~/.sonari` install without the user's go-ahead. Re-grant Automation for `sonari-raise` when macOS prompts.
- [ ] **Step 2:** Open two Claude sessions in two Terminal windows (folders e.g. `frontend` / `api`). Prompt the `api` session so it becomes the voice.
- [ ] **Step 3:** Focus the `frontend` window (don't type) → confirm the voice stays on `api` (story 4).
- [ ] **Step 4:** Press Up in the focused `frontend` window → confirm you hear `"frontend."` then `frontend`'s transcript, and `api` stops (story 1).
- [ ] **Step 5:** Switch focus back to `api`, press Up → confirm `api` navigates from its own marker (story 3).
- [ ] **Step 6:** Focus a non-terminal app, press Up → confirm graceful fallback (no crash; story 5).
- [ ] **Step 7:** Record the outcome. Any defect → new systematic-debugging cycle. All green → ready to integrate (PR per the repo's branch+PR rule).

---

## Self-Review

**1. Spec coverage:**
- Watcher in hotkeyd (spec §4.1) → Task 5. ✓
- `sonari-raise` read subcommands (§4.2) → Task 4. ✓
- Protocol `os_focus` (§4.3) → Task 2. ✓
- `SessionManager` resolve/store (§4.4) → Task 1. ✓
- `on_nav` routing + voice move + cue (§4.5) → Task 3. ✓
- Fallback (§5) → Task 1 (`focused_session()` None) + Task 3 (`or foreground()`) + explicit `test_nav_falls_back_to_foreground_when_no_os_focus`. ✓
- Testing strategy (§6): headline test (Task 3), identity resolution (Task 1), voice-move/cue + pin (Task 3), handler (Task 2), build-time spike + on-hardware acceptance (Tasks 4–6). ✓
- Permission re-grant note → Task 4. ✓
- Daemon-restart fallback note (§4.1) → covered by `focused_session()` returning None until the next `os_focus`; no code needed (Task 1 default). ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every command has expected output. ✓

**3. Type consistency:** `set_os_focus(term_program, tty, iterm_session_id, focused)` and `focused_session()` are used identically in Tasks 1, 2, 3. `_enqueue(...)` call matches the verified signature (`mute_exempt`, `at_front`, `names_session`). `MsgType.OS_FOCUS == "os_focus"` used consistently. `ALL_27`→`ALL_28` rename applied to list, function, and both body references. ✓

**Known small risks to watch during execution:**
- The `"frontend."` cue text assertion in Task 3 assumes `_enqueue(... names_session=True, at_front=True)` lands the cue at the queue head ahead of the seeded transcript — if `_nav`'s `queue.clear()` ordering differs in practice, adjust the assertion to peek the head item rather than index `[0]` of a drained list.
- Timer-on-main vs `.accessory` run loop: if the 0.5s `Timer` doesn't fire under `app.run()`, fall back to a `DispatchSourceTimer` on a background queue (it does not depend on the main RunLoop mode).
