# Sonari Cockpit — Answer-via-Hook (sub-project C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer Claude Code permission prompts eyes-free by hotkey — ⌃⌘⏎ approve / ⌃⌘⎋ deny — via a blocking `PermissionRequest` hook ↔ daemon round-trip, with a structural "only the focused session can be answered" safety guarantee.

**Architecture:** Claude Code fires a `PermissionRequest` hook only when a permission dialog would appear. `bin/sonari-hook PermissionRequest` sends a `PERMISSION_REQUEST` message to the daemon and **blocks** waiting for a reply. The daemon speaks the prompt on the asking session, registers a pending decision, and (in `_handle_message_guarded`, **after** the transaction lock is released) blocks on a per-decision `threading.Event` until the user presses ⌃⌘⏎/⌃⌘⎋ (an `ANSWER_PERMISSION` hotkey message). The daemon replies `{"decision": "allow"|"deny"|None}`; the hook prints `hookSpecificOutput.decision.behavior` (or nothing → fall through to the terminal prompt). Verified end-to-end against a live Claude Code session (see spec §16).

**Tech Stack:** Python 3.9+ (`from __future__ import annotations`), pytest, threading. macOS Carbon hotkeys (keyCode-based). No new dependencies.

## Global Constraints

- **Fail-closed always:** timeout, IPC error, daemon down, or any exception → the hook prints **no decision** (fall through to the terminal prompt) or denies — **NEVER auto-allow**.
- **Lock discipline:** message handlers run UNDER the daemon lock (`self._state.transaction()`). The decision **wait must be OUTSIDE the lock** — it lives in `_handle_message_guarded` *after* the `with` block exits. NEVER call `event.wait()` while holding the lock; NEVER re-acquire `self._lock` inside a handler.
- **Safety keying:** a keypress resolves ONLY `focused_session() or foreground()`'s pending decision. Focused session with no pending decision → `error` earcon, never an answer routed elsewhere.
- **Timeout = fall through** (owner decision): daemon waits `PERMISSION_WAIT_TIMEOUT = 120.0`s; the client send timeout is `130.0`s (strictly longer, so the daemon returns a fall-through reply before the socket closes).
- **Bindings:** ⌃⌘⏎ approve (keyCode 36), ⌃⌘⎋ deny (keyCode 53). Standard ⌃⌘ chord. Collision-vet CLEAR (spec §16.5).
- **MsgType inventory 29 → 31** (`PERMISSION_REQUEST`, `ANSWER_PERMISSION`). Keep in sync, in the SAME commit: `daemon/__init__.py` `assert_complete([...])` list + its count comment; `tests/test_daemon_registry.py` (ALL_29 → ALL_31 + handler-fn-name assertions); `tests/test_protocol.py` (BOTH value dicts).
- **No new MsgType without its `@handler`** in the same task (else `assert_complete` fails at import).
- TDD every task; full suite green before each task is marked done (baseline **779 passed, 1 skipped**). Daemon behavior is unit-tested behind `tests/daemon_helpers.py` fakes (`make_daemon`).
- Branch + PR off merged main; **NO direct main push; NO `claude.ai/code/session` footer**. NEVER `sonari install` against live `~/.sonari`.
- All A + B behavior stays green (#65 voice-follows-speaker, per-session stop, nav grammar, ⌃⌘D).

## File Structure

- `src/sonari/protocol.py` — +2 MsgTypes.
- `src/sonari/daemon/__init__.py` — assert_complete list + count comment.
- `src/sonari/daemon/host.py` — `_pending_decisions` store, `PERMISSION_WAIT_TIMEOUT`, `_await_permission_decision`, deferred-wait wiring in `_handle_message_guarded`.
- `src/sonari/daemon/features/decisions.py` — `on_permission_request`, `on_answer_permission`, `_permission_request_text`.
- `src/sonari/hooks_entry.py` — `PermissionRequest` event branch; `permission_decision_stdout` pure helper.
- `bin/sonari-hook` — blocking send + stdout decision for `PermissionRequest`.
- `src/sonari/platform/macos/keytables.py` — `return`/`escape` keyCodes.
- `src/sonari/platform/macos/hotkeys.py` — display names for `return`/`escape`.
- `src/sonari/keymap.py` — `approve`/`deny` actions + default bindings.
- `hooks/hooks.json` — `PermissionRequest` entry.
- `docs/upstream/claude-code-feature-request-answer-hook.md` — the §9 upstream feature request draft.
- Tests: `tests/test_decisions_answer.py` (new), plus edits to `tests/test_daemon_registry.py`, `tests/test_protocol.py`, `tests/test_hooks_entry.py` (or equivalent), `tests/test_keymap*.py`.

---

### Task 1: Daemon blocking-IPC core (request + answer + wait, outside the lock)

**Files:**
- Modify: `src/sonari/protocol.py` (after line 38, the `WHERE_AM_I` constant)
- Modify: `src/sonari/daemon/__init__.py` (assert_complete list + comment)
- Modify: `src/sonari/daemon/host.py` (`__init__`, new methods, `_handle_message_guarded`)
- Modify: `src/sonari/daemon/features/decisions.py` (two handlers + text helper)
- Modify: `tests/test_daemon_registry.py`, `tests/test_protocol.py` (inventory sync)
- Create: `tests/test_decisions_answer.py`

**Interfaces:**
- Produces: `MsgType.PERMISSION_REQUEST = "permission_request"`, `MsgType.ANSWER_PERMISSION = "answer_permission"`; `on_permission_request` returns the AWAIT sentinel `{"__await_decision__": True, "session": <sid>}`; `on_answer_permission` reads `msg["behavior"]` ∈ {"allow","deny"}; `SpeechDaemon._pending_decisions: dict[str, dict]` where each value is `{"event": threading.Event, "behavior": str|None}`; `SpeechDaemon._await_permission_decision(session, timeout) -> {"decision": "allow"|"deny"|None}`.
- Consumes: `host.sessions.focused_session()/foreground()/folder()`, `host._enqueue(...)`, `host.speaker.cancel()/earcon(name)`, `host.history.record/end_message`, `host._flush_prose_buffer`.

- [ ] **Step 1: Write the failing tests** in `tests/test_decisions_answer.py`

```python
from __future__ import annotations

import threading

from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon   # adjust import to the repo's helper path


def _dispatch(daemon, msg):
    return daemon._handle_message_guarded(msg)


def test_permission_request_enqueues_prompt_and_registers_pending():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("S1", cwd="/x/alpha")
    result = daemon._handle_message_guarded.__wrapped__ if False else None  # placeholder
    # call the handler directly under a transaction to inspect the sentinel
    with daemon._state.transaction():
        ret = daemon.handle_message(
            {"type": MsgType.PERMISSION_REQUEST, "session": "S1",
             "tool": "Bash", "summary": "rm -rf build"})
    assert ret == {"__await_decision__": True, "session": "S1"}
    assert "S1" in daemon._pending_decisions
    # the prompt was enqueued as a decision item on S1
    st = daemon._stream("S1")
    assert any(it.is_decision and "rm -rf build" in it.text for it in list(st.queue))


def test_await_returns_behavior_when_signalled():
    daemon, *_ = make_daemon()
    daemon._pending_decisions["S1"] = {"event": threading.Event(), "behavior": None}
    daemon._pending_decisions["S1"]["behavior"] = "allow"
    daemon._pending_decisions["S1"]["event"].set()
    assert daemon._await_permission_decision("S1", 1.0) == {"decision": "allow"}
    assert "S1" not in daemon._pending_decisions   # popped after resolution


def test_await_times_out_to_none():
    daemon, *_ = make_daemon()
    daemon._pending_decisions["S1"] = {"event": threading.Event(), "behavior": None}
    assert daemon._await_permission_decision("S1", 0.05) == {"decision": None}


def test_answer_sets_behavior_and_confirms_for_focused_session():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("S1", cwd="/x/alpha")
    ev = threading.Event()
    daemon._pending_decisions["S1"] = {"event": ev, "behavior": None}
    _dispatch(daemon, {"type": MsgType.ANSWER_PERMISSION, "behavior": "allow"})
    assert daemon._pending_decisions["S1"]["behavior"] == "allow"
    assert ev.is_set()
    assert speaker.cancelled          # barge-in happened (adapt to FakeSpeaker's flag)
    st = daemon._stream("S1")
    assert any("Approved." in it.text for it in list(st.queue))


def test_answer_on_session_without_pending_is_error_no_route():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("A", cwd="/x/a")     # focused/foreground = A (no pending)
    other = threading.Event()
    daemon._pending_decisions["B"] = {"event": other, "behavior": None}  # B has the prompt
    _dispatch(daemon, {"type": MsgType.ANSWER_PERMISSION, "behavior": "allow"})
    assert daemon._pending_decisions["B"]["behavior"] is None   # B was NOT answered
    assert not other.is_set()
    assert "error" in speaker.earcons   # adapt to FakeSpeaker's earcon record


def test_blocking_round_trip_request_then_answer():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("S1", cwd="/x/alpha")
    out = {}

    def asker():
        out["reply"] = daemon._handle_message_guarded(
            {"type": MsgType.PERMISSION_REQUEST, "session": "S1",
             "tool": "Bash", "summary": "deploy"})

    t = threading.Thread(target=asker)
    t.start()
    # wait until the request has registered its pending decision, then answer
    deadline = threading.Event()
    for _ in range(200):
        if "S1" in daemon._pending_decisions:
            break
        deadline.wait(0.01)
    daemon._handle_message_guarded({"type": MsgType.ANSWER_PERMISSION, "behavior": "deny"})
    t.join(timeout=5.0)
    assert out["reply"] == {"decision": "deny"}
```

(Adapt `make_daemon`'s return tuple and `FakeSpeaker`'s introspection attributes — `cancelled`, `earcons` — to the actual `tests/daemon_helpers.py`. If the fake lacks them, prefer asserting via enqueued items / `speaker` call records that already exist; do NOT add test-only methods to production classes.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_decisions_answer.py -q`
Expected: FAIL — `AttributeError: ... PERMISSION_REQUEST` / `ANSWER_PERMISSION`, and `assert_complete` may raise at import once the MsgTypes exist without handlers (that is why Step 3 adds types + handlers together).

- [ ] **Step 3: Add the two MsgTypes** in `src/sonari/protocol.py` (after `WHERE_AM_I`)

```python
    WHERE_AM_I = "where_am_i"   # ⌃⌘W: terse SPOKEN status (barge-in + interjection-resume)
    PERMISSION_REQUEST = "permission_request"   # PermissionRequest hook: BLOCKING ask; daemon replies {"decision": ...}
    ANSWER_PERMISSION = "answer_permission"     # ⌃⌘⏎/⌃⌘⎋: answer the focused session's pending decision (msg["behavior"])
```

- [ ] **Step 4: Add the pending store, timeout, await, and deferred-wait wiring** in `src/sonari/daemon/host.py`

In `__init__` (after `self._reload_lock = ...`):

```python
        # Pending permission decisions: session_id -> {"event": Event, "behavior": str|None}.
        # Mutated ONLY under self._lock (handlers); the Event is waited on ONLY outside
        # the lock (in _handle_message_guarded, after the transaction exits).
        self._pending_decisions: dict = {}
```

Add a module-level constant near the top of `host.py` (after the imports):

```python
PERMISSION_WAIT_TIMEOUT = 120.0   # daemon's own wait; MUST be < the hook's client send timeout (130s)
```

Add the await method (after `note_spoken`, say):

```python
    def _await_permission_decision(self, session: str, timeout: float) -> dict:
        """Block (OUTSIDE the daemon lock) until the focused-session answer arrives
        or the wait expires. Returns {"decision": "allow"|"deny"|None}; None means the
        hook falls through to Claude Code's normal terminal prompt (fail-closed)."""
        with self._lock:
            pd = self._pending_decisions.get(session)
        if pd is None:
            return {"decision": None}
        got = pd["event"].wait(timeout)
        with self._lock:
            behavior = pd["behavior"] if got else None
            # Pop only if still ours (a newer request for the same session may have replaced it).
            if self._pending_decisions.get(session) is pd:
                self._pending_decisions.pop(session, None)
        return {"decision": behavior}
```

Rewrite `_handle_message_guarded` so the wait runs after the lock is released and is itself fail-closed:

```python
    def _handle_message_guarded(self, msg):
        """Dispatch one socket message under the lock; if the handler asks to block
        for a permission decision, do that AFTER the lock is released. Contained so a
        malformed/buggy message logs a traceback instead of killing the connection
        thread. Returns the reply or None."""
        try:
            with self._state.transaction():
                result = self.handle_message(msg)
        except Exception:  # noqa: BLE001 - one bad message must not drop the connection
            import sys
            import traceback
            traceback.print_exc(file=sys.stderr)
            return None
        if isinstance(result, dict) and result.get("__await_decision__"):
            try:
                return self._await_permission_decision(
                    result["session"], PERMISSION_WAIT_TIMEOUT)
            except Exception:  # noqa: BLE001 - the wait must never auto-allow on error
                import sys
                import traceback
                traceback.print_exc(file=sys.stderr)
                return {"decision": None}   # fail-closed: fall through to terminal
        return result
```

- [ ] **Step 5: Add the two handlers + text helper** in `src/sonari/daemon/features/decisions.py`

At the top, extend the import and add `threading`:

```python
import threading

from sonari.protocol import MsgType
```

Add the text helper (near `_permission_text`):

```python
def _permission_request_text(msg) -> str:
    # Render the spoken prompt for a blocking PermissionRequest. The payload carries the
    # tool name + a short summary (Bash command / file). Prefer an explicit action/message
    # if present (forward-compatible), else "{tool}: {summary}".
    action = (msg.get("action") or "").strip()
    if action:
        return action
    tool = (msg.get("tool") or "").strip()
    summary = (msg.get("summary") or "").strip()
    if tool and summary and summary != tool:
        return "{0}: {1}".format(tool, summary)
    return summary or tool or "Permission needed."
```

Add the handlers (after `on_permission`):

```python
@handler(MsgType.PERMISSION_REQUEST)
def on_permission_request(ctx, msg):
    # BLOCKING permission ask from the PermissionRequest hook. Speak the prompt on the
    # ASKING session as a decision item (so ⌃⌘D lands on it), register a pending decision,
    # and return the AWAIT sentinel — _handle_message_guarded then blocks OUTSIDE the lock.
    host = ctx.host
    session = ctx.session
    text = _permission_request_text(msg)
    host.speaker.earcon("permission")
    entry = host.history.record(session, "permission", text)
    host.history.end_message(session)
    host._flush_prose_buffer(session)        # prose before the permission ask
    host._enqueue(session, "permission", text, True, entry=entry)
    # We are under the daemon lock here, so mutate the store directly.
    prev = host._pending_decisions.get(session)
    if prev is not None:
        prev["event"].set()                  # release any stale waiter for this session
    host._pending_decisions[session] = {"event": threading.Event(), "behavior": None}
    return {"__await_decision__": True, "session": session}


@handler(MsgType.ANSWER_PERMISSION)
def on_answer_permission(ctx, msg):
    # ⌃⌘⏎ approve / ⌃⌘⎋ deny. Answer ONLY the focused session's own pending decision.
    host = ctx.host
    behavior = msg.get("behavior")
    if behavior not in ("allow", "deny"):
        host.speaker.earcon("error")
        return None
    target = host.sessions.focused_session() or host.sessions.foreground()
    pd = host._pending_decisions.get(target) if target is not None else None
    if pd is None:
        host.speaker.earcon("error")         # nothing to answer on the focused session
        return None
    pd["behavior"] = behavior
    pd["event"].set()
    host.speaker.cancel()                     # barge-in: confirm immediately
    host._enqueue(target, "prose",
                  "Approved." if behavior == "allow" else "Denied.",
                  False, mute_exempt=True, at_front=True)
    return None
```

(Verify `host.speaker.earcon("permission")` is a valid earcon name — it is the same kind the Notification `permission_prompt` path emits via `MsgType.EARCON(kind="permission")`. If the speaker's earcon set rejects unknown names, reuse exactly that name.)

- [ ] **Step 6: Sync the inventory** — `src/sonari/daemon/__init__.py`: add `MsgType.PERMISSION_REQUEST` and `MsgType.ANSWER_PERMISSION` to the `assert_complete([...])` list and change the comment `29 known keys` → `31 known keys`. Then update `tests/test_daemon_registry.py` (rename/extend `ALL_29` → `ALL_31`, add the two type strings, and add handler-function-name assertions for `on_permission_request` / `on_answer_permission` matching the file's existing pattern) and `tests/test_protocol.py` (add `permission_request` / `answer_permission` to BOTH value dicts — `test_msgtype_has_every_constant_with_exact_values` and `test_msgtype_defines_no_extra_string_constants`; while there, confirm both dicts already agree on the existing constants).

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_decisions_answer.py tests/test_daemon_registry.py tests/test_protocol.py -q`
Expected: PASS (all). Then full suite: `.venv/bin/python -m pytest -q` → 779 + new tests pass, 1 skipped.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(sonari): blocking permission-decision IPC (request/await/answer, outside the lock)"
```

---

### Task 2: Hook layer — PermissionRequest mapping + blocking send + stdout decision

**Files:**
- Modify: `src/sonari/hooks_entry.py` (event branch + pure stdout helper)
- Modify: `bin/sonari-hook` (blocking send for PermissionRequest, print decision)
- Modify/Create: `tests/test_hooks_entry.py` (the repo's hooks-entry test module)

**Interfaces:**
- Consumes: `MsgType.PERMISSION_REQUEST`, `client.send(msg, expect_reply=True, timeout=...)`.
- Produces: `handle_event("PermissionRequest", payload) -> [PERMISSION_REQUEST msg]`; `permission_decision_stdout(reply) -> str|None` (the exact stdout JSON, or None to fall through).

- [ ] **Step 1: Write the failing tests** (in the hooks-entry test module)

```python
import json
from sonari.hooks_entry import handle_event, permission_decision_stdout
from sonari.protocol import MsgType


def test_permission_request_maps_to_blocking_message():
    msgs = handle_event("PermissionRequest", {
        "session_id": "S1", "tool_name": "Bash",
        "tool_input": {"command": "rm -rf build", "description": "clean"}})
    assert len(msgs) == 1
    m = msgs[0]
    assert m["type"] == MsgType.PERMISSION_REQUEST
    assert m["session"] == "S1"
    assert m["tool"] == "Bash"
    assert "rm -rf build" in m["summary"]


def test_permission_decision_stdout_allow_and_deny():
    out = json.loads(permission_decision_stdout({"decision": "allow"}))
    assert out == {"hookSpecificOutput": {
        "hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}
    out = json.loads(permission_decision_stdout({"decision": "deny"}))
    assert out["hookSpecificOutput"]["decision"]["behavior"] == "deny"


def test_permission_decision_stdout_fallthrough_cases():
    assert permission_decision_stdout({"decision": None}) is None
    assert permission_decision_stdout({}) is None
    assert permission_decision_stdout(None) is None
    assert permission_decision_stdout({"decision": "ask"}) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_hooks_entry.py -q`
Expected: FAIL — `permission_decision_stdout` undefined; no PermissionRequest branch.

- [ ] **Step 3: Add the event branch + pure helper** in `src/sonari/hooks_entry.py`

Add `import json` at the top. Add the branch (alongside the other events in `handle_event`):

```python
    if event == "PermissionRequest":
        tool = payload.get("tool_name")
        ti = payload.get("tool_input", {})
        return [
            _msg(type=MsgType.PERMISSION_REQUEST, session=session,
                 tool=tool, summary=_tool_summary(tool, ti)),
        ]
```

Add the pure helper (module level):

```python
def permission_decision_stdout(reply) -> "str | None":
    """Render the PermissionRequest hook's stdout JSON from the daemon reply, or None
    to fall through to Claude Code's normal terminal prompt. Fail-closed: anything that
    is not an explicit allow/deny -> None (never auto-allow)."""
    behavior = reply.get("decision") if isinstance(reply, dict) else None
    if behavior in ("allow", "deny"):
        return json.dumps({"hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": behavior}}})
    return None
```

- [ ] **Step 4: Add the blocking path** in `bin/sonari-hook`

Replace the tail of `main()` (the `msgs = handle_event(...)` block through the send loop) so PermissionRequest blocks and prints, while every other event keeps the fire-and-forget loop:

```python
    msgs = handle_event(event, payload)
    if not msgs:
        return

    try:
        client.ensure_daemon()
    except Exception:
        pass

    if event == "PermissionRequest":
        # Blocking ask: send ONE message, wait for the daemon's decision (its own
        # ~120s wait < this 130s socket timeout), print the decision to stdout. Any
        # failure -> print nothing -> Claude Code shows its normal terminal prompt.
        from sonari.hooks_entry import permission_decision_stdout
        reply = None
        try:
            reply = client.send(msgs[0], expect_reply=True, timeout=130.0)
        except Exception:
            reply = None
        out = permission_decision_stdout(reply)
        if out:
            try:
                sys.stdout.write(out)
            except Exception:
                pass
        return

    for m in msgs:
        try:
            client.send(m)
        except Exception:
            pass
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_hooks_entry.py -q` → PASS. Then `.venv/bin/python -m pytest -q` → green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(sonari): PermissionRequest hook mapping + blocking send + stdout decision"
```

---

### Task 3: Keymap + keytables — ⌃⌘⏎ approve / ⌃⌘⎋ deny

**Files:**
- Modify: `src/sonari/platform/macos/keytables.py` (KEY_CODES)
- Modify: `src/sonari/platform/macos/hotkeys.py` (`_KEY_DISPLAY_BY_NAME`)
- Modify: `src/sonari/keymap.py` (`ACTION_MESSAGES`, `_DEFAULT_KEYS`)
- Modify: the keymap test module(s) (`tests/test_keymap*.py`)

**Interfaces:**
- Consumes: `MsgType.ANSWER_PERMISSION` payload shape `{type, behavior}`.
- Produces: default bindings `approve`→⌃⌘+keyCode 36, `deny`→⌃⌘+keyCode 53.

- [ ] **Step 1: Write the failing tests** (in the keymap test module)

```python
from sonari.keymap import resolve_keymap   # adapt to the actual resolver name/signature


def test_approve_deny_default_bindings():
    km = resolve_keymap({})   # defaults only; adapt to the real signature
    # Each entry maps a (keyCode, modifiers) chord to the action's protocol message.
    approve = _find_action(km, {"type": "answer_permission", "behavior": "allow"})
    deny = _find_action(km, {"type": "answer_permission", "behavior": "deny"})
    assert approve["keyCode"] == 36 and set(approve["mods"]) == {"ctrl", "cmd"}
    assert deny["keyCode"] == 53 and set(deny["mods"]) == {"ctrl", "cmd"}
```

(Write `_find_action` to match the resolver's output structure; if a `test_no_two_default_actions_share_a_key` test exists, no new test is needed for uniqueness — it must keep passing.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_keymap.py -q`
Expected: FAIL — `return`/`escape` not in KEY_CODES; `approve`/`deny` not actions.

- [ ] **Step 3: Add keyCodes** in `src/sonari/platform/macos/keytables.py` (KEY_CODES dict)

```python
    "tab": 48,
    "return": 36, "enter": 36,
    "escape": 53, "esc": 53,
```

- [ ] **Step 4: Add display names** in `src/sonari/platform/macos/hotkeys.py` (`_KEY_DISPLAY_BY_NAME`)

```python
    "equal": "=", "minus": "-", "tab": "Tab",
    "return": "Return", "escape": "Esc",
```

- [ ] **Step 5: Add actions + default bindings** in `src/sonari/keymap.py`

In `ACTION_MESSAGES`:

```python
    "approve": {"type": "answer_permission", "behavior": "allow"},
    "deny": {"type": "answer_permission", "behavior": "deny"},
```

In `_DEFAULT_KEYS`:

```python
    "approve": "return", "deny": "escape",
```

- [ ] **Step 6: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_keymap.py -q` → PASS (incl. the no-duplicate-chord test). Then full suite green.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(sonari): bind ⌃⌘Return approve / ⌃⌘Escape deny (keytables + keymap)"
```

---

### Task 4: Wire the hook + the upstream feature-request draft

**Files:**
- Modify: `hooks/hooks.json` (add `PermissionRequest`)
- Create: `tests/test_hooks_json.py` (or extend the existing hooks-config test) — assert the entry exists
- Create: `docs/upstream/claude-code-feature-request-answer-hook.md`

- [ ] **Step 1: Write the failing test** asserting the hooks.json wiring

```python
import json, os


def test_hooks_json_registers_permission_request():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = json.load(open(os.path.join(root, "hooks", "hooks.json")))
    entries = cfg["hooks"]["PermissionRequest"]
    cmds = [h["command"] for e in entries for h in e["hooks"]]
    assert any("sonari-hook PermissionRequest" in c for c in cmds)
```

(Adapt the key path to hooks.json's actual shape — match the existing `PreToolUse`/`Notification` entry structure exactly.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_hooks_json.py -q`
Expected: FAIL — `KeyError: 'PermissionRequest'`.

- [ ] **Step 3: Add the hooks.json entry** (matching the existing entry format; empty matcher = all permission-eligible tools)

```json
    "PermissionRequest": [
      {
        "matcher": "",
        "hooks": [
          { "type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/bin/sonari-hook PermissionRequest" }
        ]
      }
    ]
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_hooks_json.py -q` → PASS. Full suite green.

- [ ] **Step 5: Draft the upstream feature request** in `docs/upstream/claude-code-feature-request-answer-hook.md`

Content (no code; a filing-ready issue draft):

```markdown
# Feature request: a hook/IPC to answer AskUserQuestion & ExitPlanMode from an external tool

## Problem
Claude Code's `PermissionRequest` hook lets an external tool allow/deny a *tool* call
(verified working). But there is no equivalent channel to (a) select an **AskUserQuestion**
option or (b) approve/reject an **ExitPlanMode** plan from outside the interactive session.
Hooks can gate a tool but cannot supply a tool's *result*, and there is no IPC/response API
for an interactive turn.

## Why it matters
Eyes-free / accessibility tools (e.g. the Sonari TTS cockpit) can speak these prompts but
cannot let the user answer them by hotkey — the final selection must happen in the terminal,
which defeats hands/eyes-free operation.

## Proposed
A hook event (or IPC) that fires on AskUserQuestion / ExitPlanMode and accepts a structured
response from an external tool: the chosen option index/label (single or multi-select) for
AskUserQuestion, and approve/reject for ExitPlanMode — mirroring how `PermissionRequest`
returns `hookSpecificOutput.decision.behavior`.

## Notes
Keystroke injection is not viable (Secure Event Input swallows synthetic keys; wrong-target
risk). A blocking hook with a timeout (as `PermissionRequest` already supports) is the proven
pattern.
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(sonari): register PermissionRequest hook; draft upstream answer-hook feature request"
```

> Filing the upstream request on GitHub needs Nima's account — flag it for him; do not file automatically.

---

## Self-Review (controller, after writing — run before dispatch)

1. **Spec coverage (§16):** §16.1 pivot → Task 1+2; §16.2 blocking IPC → Task 1; §16.3 safety → Task 1 (`on_answer_permission` focused-only + the no-pending test); §16.4 timeout fall-through → Task 1 (`_await` → None) + Task 2 (`permission_decision_stdout` None) + bin/sonari-hook fail-closed; §16.5 bindings/collision/protocol → Task 1 (inventory) + Task 3 (keys); §16.6 testing → all tasks; §16.7 upstream FR → Task 4. ✓
2. **Placeholders:** the `make_daemon`/`FakeSpeaker` introspection and `resolve_keymap`/`hooks.json`-shape specifics are explicitly flagged "adapt to the repo" — the implementer reads those files; not a silent TODO.
3. **Type consistency:** `answer_permission` payload `{type, behavior}` used identically in keymap (Task 3), handler (Task 1), tests. `PERMISSION_REQUEST` message carries `session/tool/summary` in hooks_entry (Task 2) and is read by `_permission_request_text` (Task 1). AWAIT sentinel `{"__await_decision__", "session"}` produced by `on_permission_request` and consumed by `_handle_message_guarded` (both Task 1). Reply `{"decision": ...}` produced by `_await_permission_decision` (Task 1) and consumed by `permission_decision_stdout` (Task 2). ✓
