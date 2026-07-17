# Sonari SP5 Catch-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `catch_up` verb — a spoken host-LLM summary of what happened in a piled session while the user was away. An instant deterministic ack carrying ground-truth facts, then (~5–10 s later) a summary voiced in a distinct voice at a sentence boundary; hearing it to completion burns the pile like an informed skip. Any failure falls to a deterministic extractive digest. Host-CLI auth only (no API key ever).

**Architecture:** A new daemon-side summarizer seam (`HostSummarizer` protocol + `ClaudeCliSummarizer` adapter) `Popen`s the user's own `claude -p` with the transcript slice on stdin and the API-key env scrubbed. Preparation runs on one fire-and-forget worker thread that touches NO daemon state — it calls only `summarizer.summarize(slice_text)` and posts an internal `catchup_result` protocol message to a `queue.Queue` mailbox drained at the top of the speak loop, so every state change happens on the daemon loop via the normal handler registry (tests drive it directly via `handle_message`). The press captures a full bundle (`self._catchup`) — target, folder, pinned `slice_end`, deterministic digest, slice text — that survives SESSION_END (daemon state, not sessions/history). The render is a three-item sequence (frame main-voice + body summary-voice + tail main-voice) tagged with a `render_id`; the frontier burns to the pinned `slice_end` only when the whole sequence completes, and any mid-render cut suppresses the burn.

**Tech Stack:** Python 3.9+ daemon, stdlib only (`subprocess`, `threading`, `queue`, `tempfile`), pytest. No new third-party dependency. DI via injected `popen` callable (the `kokoro_provision` pattern). Run the suite with `.venv/bin/python -m pytest -q`.

## Global Constraints

- **Agent-neutrality:** no host-specific shape crosses the adapter seam into protocol/history/core. Core asks one question — "summarize this slice, or fail detectably." `hooks_entry.py` (the inbound hook-process adapter) stays untouched; the summarizer is a NEW daemon-side seam.
- **Spoken-grammar principles** (`2026-07-16-sonari-whereami-grammar-v2.md`) bind every new spoken string and the narrator prompt: sentence boundaries are the only rate-proof prosody (each spoken unit ends in a period); a role word sits adjacent to every number ("{N} items"); never leave a high-stakes fact as a standalone clippable landmark (the decision fact rides an inline tail, never a bare "Decision:").
- **Env-scrub:** the `claude -p` child environment is a copy of the daemon env with `ANTHROPIC_API_KEY` AND `ANTHROPIC_AUTH_TOKEN` removed — the billing trap (a stray key silently flips subscription OAuth to metered API billing). This is the single most important line in the feature and has its own dedicated test.
- **Explicit press only, one in flight globally, 30 s hard timeout** with the child process group killed on expiry; stdin written then closed promptly. SP5 fires no automatic summaries. A press while one is in flight is a pure cancel (never starts a new one).
- **All state changes on the daemon loop.** The worker thread never mutates daemon state (not history, streams, sessions, or `self._catchup` fields the loop reads for rendering). It only calls the summarizer and posts to the mailbox.
- **Guards green at every commit and never weakened:** the 6 concurrency/monotonicity guards must pass at every commit; existing tests are updated for new pile semantics, never weakened.
- **Suite green at every commit:** `.venv/bin/python -m pytest -q` passes after every task's final step.
- **Conventional commits, NO AI/tool/session mentions ever** in any commit message or code comment.
- **Every new `MsgType` is added to `tests/test_protocol.py`'s completeness guard** (`test_msgtype_defines_no_extra_string_constants`, line 95 — the strict one) AND to `assert_complete(...)` in `src/sonari/daemon/__init__.py` (empirically required — SP4 T6 lesson).
- **Build-entry gate:** this build starts only after the OWNER runs the §4 smoke tests on his machine (his quota, his go). Plan tasks NEVER run a live `claude -p`; all tests use the injected fake summarizer.
- **No result caching, no mid-prep progress ticks, no auto-triggers** in v1 (out of scope, §14 of the spec).

### Task 1: Protocol + keymap wiring for `catch_up` and `catchup_result`

**Files:**
- Modify: `src/sonari/protocol.py:45` (add two `MsgType` constants after `SKIP_PILE`)
- Modify: `src/sonari/keymap.py:26-52` (add `catch_up` to `ACTION_MESSAGES`; ships UNBOUND — do NOT add to `_DEFAULT_KEYS`)
- Modify: `tests/test_protocol.py:95-139` (add both new keys to the strict completeness guard `expected` dict)
- Test: `tests/test_keymap.py` (new test asserting `catch_up` resolves and ships unbound)

**Interfaces:**
- Produces: `MsgType.CATCH_UP == "catch_up"` (keyboard press) and `MsgType.CATCHUP_RESULT == "catchup_result"` (internal worker→loop message). `ACTION_MESSAGES["catch_up"] == {"type": "catch_up"}`.
- Note: `assert_complete(...)` in `daemon/__init__.py` is NOT touched here — the entries are added alongside their handlers in Task 6 (`catch_up`) and Task 7 (`catchup_result`), so the suite stays green (a MsgType constant with no handler is legal; only a type LISTED in `assert_complete` demands a handler).

- [ ] **Step 1: Write the failing tests**

In `tests/test_protocol.py`, add both keys to the `expected` dict inside `test_msgtype_defines_no_extra_string_constants` (after the `"SKIP_PILE": "skip_pile",` line):
```python
        "SKIP_PILE": "skip_pile",
        "CATCH_UP": "catch_up",
        "CATCHUP_RESULT": "catchup_result",
```
In `tests/test_keymap.py`, add:
```python
def test_catch_up_is_a_valid_unbound_action():
    from sonari import keymap
    # The catch-up press is a resolvable action...
    assert keymap.ACTION_MESSAGES["catch_up"] == {"type": "catch_up"}
    # ...but ships UNBOUND (owner ear-gate, like skip_pile): not in the defaults.
    km = keymap.default_keymap()
    assert "catch_up" not in km
```

- [ ] **Step 2: Run the tests to verify they fail**
```
.venv/bin/python -m pytest -q tests/test_protocol.py::test_msgtype_defines_no_extra_string_constants tests/test_keymap.py::test_catch_up_is_a_valid_unbound_action
```
Expect: `test_catch_up_is_a_valid_unbound_action` fails with `KeyError: 'catch_up'`; the protocol test fails with an `actual == expected` mismatch (the two new keys missing from `actual`).

- [ ] **Step 3: Write the minimal implementation**

In `src/sonari/protocol.py`, after the `SKIP_PILE = "skip_pile"` line (protocol.py:45):
```python
    SKIP_PILE = "skip_pile"             # deliberate pile-skip: advance the frontier past the pile (SP4)
    CATCH_UP = "catch_up"               # SP5: spoken host-LLM summary of the pile (ships unbound; ⌃⌘L proposed)
    CATCHUP_RESULT = "catchup_result"   # SP5 internal: worker→daemon-loop delivery of a prepared summary
```
In `src/sonari/keymap.py`, add to `ACTION_MESSAGES` after the `"skip_pile"` entry (keymap.py:51):
```python
    "skip_pile": {"type": "skip_pile"},
    # SP5 catch-up: bindable + resolvable, ships UNBOUND (NOT in _DEFAULT_KEYS) —
    # Nima's ear-gate. Proposed: ⌃⌘L (keymap.json: key "l", mods ["ctrl","cmd"]).
    "catch_up": {"type": "catch_up"},
```

- [ ] **Step 4: Run the tests to verify they pass**
```
.venv/bin/python -m pytest -q tests/test_protocol.py tests/test_keymap.py
```
Expect: all pass (both the new tests and every existing protocol/keymap test).

- [ ] **Step 5: Commit**
```
git add src/sonari/protocol.py src/sonari/keymap.py tests/test_protocol.py tests/test_keymap.py
git commit -m "feat(sp5): add catch_up and catchup_result message types"
```

### Task 2: Summary sanitizer (pure)

**Files:**
- Create: `src/sonari/catchup.py` (new module — pure catch-up text helpers; the daemon feature and the adapter import from here)
- Test: `tests/test_catchup_sanitizer.py` (new)

**Interfaces:**
- Produces: `sanitize_summary(text: str, ceiling: int = 8) -> str` — deterministic; strips markdown, collapses whitespace, sentence-splits, clamps to `ceiling` sentences; returns `""` when nothing survives (the caller reads `""` as "fall to the digest"). No daemon imports; stdlib `re` only.

- [ ] **Step 1: Write the failing test**

`tests/test_catchup_sanitizer.py`:
```python
from sonari.catchup import sanitize_summary


def test_clean_prose_passes_through():
    text = "Tests passed. The build is green. It asked to deploy."
    assert sanitize_summary(text) == text


def test_strips_markdown_fences_backticks_emphasis_headings():
    raw = "# Result\nRan `pytest`. **All** green.\n```\ncode\n```"
    out = sanitize_summary(raw)
    assert "`" not in out and "*" not in out and "#" not in out
    assert "```" not in out
    assert "Ran pytest." in out and "All green." in out


def test_strips_leading_list_markers_and_collapses_newlines():
    raw = "- first thing.\n- second thing.\n\n1. third thing."
    out = sanitize_summary(raw)
    assert out == "first thing. second thing. third thing."


def test_clamps_to_ceiling_sentences():
    raw = " ".join("Sentence {0}.".format(i) for i in range(1, 13))
    out = sanitize_summary(raw, ceiling=8)
    assert out == " ".join("Sentence {0}.".format(i) for i in range(1, 9))
    assert "Sentence 9." not in out


def test_empty_and_pure_markdown_return_empty():
    assert sanitize_summary("") == ""
    assert sanitize_summary("   \n\t  ") == ""
    assert sanitize_summary("```\n\n```") == ""
    assert sanitize_summary("*** ___ ###") == ""
```

- [ ] **Step 2: Run the test to verify it fails**
```
.venv/bin/python -m pytest -q tests/test_catchup_sanitizer.py
```
Expect: `ModuleNotFoundError: No module named 'sonari.catchup'`.

- [ ] **Step 3: Write the minimal implementation**

`src/sonari/catchup.py`:
```python
"""Pure catch-up text helpers (SP5): sanitize LLM output, render the transcript
slice for the narrator, and build the deterministic digest floor. No daemon
imports — safe to unit-test in isolation and to call from the worker thread."""
from __future__ import annotations

import re

_LIST_MARKER = re.compile(r"(?m)^[ \t]*(?:[-+*]|\d+\.)[ \t]+")
_FENCE = re.compile(r"`{3,}")
_EMPHASIS = re.compile(r"[*_#]+")
_WHITESPACE = re.compile(r"\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def sanitize_summary(text: str, ceiling: int = 8) -> str:
    """Speech-safe body text from whatever the model returned. Strip markdown,
    collapse whitespace to single spaces, split into sentences, clamp to
    *ceiling*. Returns '' when nothing survives (caller falls to the digest)."""
    if not text:
        return ""
    s = _LIST_MARKER.sub("", text)          # leading bullets/numbers, per line
    s = _FENCE.sub(" ", s)                   # ``` fences
    s = s.replace("`", "")                   # inline code ticks
    s = _EMPHASIS.sub("", s)                 # * _ # emphasis/heading marks
    s = _WHITESPACE.sub(" ", s).strip()      # newlines/runs -> single spaces
    if not s:
        return ""
    sentences = [p for p in _SENTENCE_SPLIT.split(s) if p.strip()]
    return " ".join(sentences[:ceiling])
```

- [ ] **Step 4: Run the test to verify it passes**
```
.venv/bin/python -m pytest -q tests/test_catchup_sanitizer.py
```
Expect: 5 passed.

- [ ] **Step 5: Commit**
```
git add src/sonari/catchup.py tests/test_catchup_sanitizer.py
git commit -m "feat(sp5): add speech-safe summary sanitizer"
```

### Task 3: Slice-text renderer + deterministic digest builder (pure)

**Files:**
- Modify: `src/sonari/catchup.py` (append two functions + the tag map; reuse the `_WHITESPACE`/`_SENTENCE_SPLIT` regexes from Task 2)
- Test: `tests/test_catchup_slice.py` (new)

**Interfaces:**
- Consumes: `HistoryEntry`-shaped objects (attributes `.kind`, `.text`, `.turn_id`) — the entries returned by `history.unheard_from_frontier`.
- Produces:
  - `render_slice(entries, folder) -> str` — the narrator stdin body: header line `"Slice: {N} items across {T} turns in {folder}."` (folder falls back to `"this session"`), then one kind-tagged line per entry oldest-first. Kind→tag map is `{"prose": "assistant", "tool": "tool", "choice": "question", "plan": "plan", "permission": "permission"}`, unknown kinds tag as themselves.
  - `build_digest(entries) -> str` — the deterministic floor `"Summary unavailable. Last: {verbatim final sentence}."`; extracts the last `prose` entry's final sentence (any-kind last entry if no prose), guarantees exactly one terminal period. Built at PRESS time from the pinned slice (never touches the LLM).

- [ ] **Step 1: Write the failing test**

`tests/test_catchup_slice.py`:
```python
from sonari.history import HistoryEntry
from sonari.catchup import render_slice, build_digest


def _e(kind, text, turn=0):
    return HistoryEntry(text, kind, msg_id=0, seq=0, turn_id=turn)


def test_render_slice_header_and_tags_oldest_first():
    entries = [_e("prose", "Working on it.", 0),
               _e("tool", "Bash: pytest", 0),
               _e("permission", "Allow deploy?", 1)]
    lines = render_slice(entries, "myrepo").split("\n")
    assert lines[0] == "Slice: 3 items across 2 turns in myrepo."
    assert lines[1] == "assistant: Working on it."
    assert lines[2] == "tool: Bash: pytest"
    assert lines[3] == "permission: Allow deploy?"


def test_render_slice_no_folder_fallback():
    lines = render_slice([_e("prose", "Hi.")], None).split("\n")
    assert lines[0] == "Slice: 1 items across 1 turns in this session."


def test_digest_extracts_last_assistant_sentence():
    entries = [_e("prose", "Started."), _e("tool", "ran"),
               _e("prose", "All tests passed.")]
    assert build_digest(entries) == "Summary unavailable. Last: All tests passed."


def test_digest_appends_period_when_missing():
    out = build_digest([_e("prose", "no terminal punctuation")])
    assert out == "Summary unavailable. Last: no terminal punctuation."


def test_digest_falls_back_to_last_entry_when_no_prose():
    entries = [_e("tool", "ran a thing"), _e("permission", "Allow deploy?")]
    assert build_digest(entries) == "Summary unavailable. Last: Allow deploy?"
```

- [ ] **Step 2: Run the test to verify it fails**
```
.venv/bin/python -m pytest -q tests/test_catchup_slice.py
```
Expect: `ImportError: cannot import name 'render_slice' from 'sonari.catchup'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `src/sonari/catchup.py`:
```python
_KIND_TAGS = {
    "prose": "assistant",
    "tool": "tool",
    "choice": "question",
    "plan": "plan",
    "permission": "permission",
}


def render_slice(entries, folder) -> str:
    """The narrator's stdin: a header line then kind-tagged transcript lines,
    oldest first. Sonari's own transcript is the source of truth, never the
    host's session files."""
    n = len(entries)
    turns = len({e.turn_id for e in entries})
    header = "Slice: {0} items across {1} turns in {2}.".format(
        n, turns, folder or "this session")
    lines = ["{0}: {1}".format(_KIND_TAGS.get(e.kind, e.kind), e.text)
             for e in entries]
    return "\n".join([header] + lines)


def _last_sentence(text: str) -> str:
    s = _WHITESPACE.sub(" ", text or "").strip()
    if not s:
        return ""
    parts = [p for p in _SENTENCE_SPLIT.split(s) if p.strip()]
    return parts[-1] if parts else s


def build_digest(entries) -> str:
    """The deterministic floor: the verbatim final recorded sentence, framed as
    a degradation. Prefers the last assistant-prose entry; any-kind last entry
    otherwise. Built at press from the pinned slice — no model, no network."""
    prose = [e for e in entries if e.kind == "prose"]
    source = prose[-1] if prose else (entries[-1] if entries else None)
    last = _last_sentence(source.text) if source is not None else ""
    if not last:
        return "Summary unavailable."
    if not last.endswith((".", "!", "?")):
        last += "."
    return "Summary unavailable. Last: {0}".format(last)
```

- [ ] **Step 4: Run the test to verify it passes**
```
.venv/bin/python -m pytest -q tests/test_catchup_slice.py
```
Expect: 5 passed.

- [ ] **Step 5: Commit**
```
git add src/sonari/catchup.py tests/test_catchup_slice.py
git commit -m "feat(sp5): add slice renderer and deterministic digest builder"
```

### Task 4: HostSummarizer protocol + ClaudeCliSummarizer adapter + config keys

**Files:**
- Create: `src/sonari/summarizer.py` (new module — the adapter seam)
- Modify: `src/sonari/config.py:9-20` (three new DEFAULTS keys)
- Test: `tests/test_summarizer.py` (new)

**Interfaces:**
- Produces:
  - `SummarizeResult` with `.is_ok: bool`, `.text: str`, `.reason: str`; classmethods `SummarizeResult.ok(text)` / `SummarizeResult.failed(reason)` (reason ∈ `unavailable|logged_out|timeout|error|empty`).
  - `HostSummarizer` protocol: `summarize(slice_text: str, timeout_s: float, cancel=None) -> SummarizeResult` (`cancel` is a `threading.Event` set by the daemon to kill an in-flight child).
  - `ClaudeCliSummarizer(popen=subprocess.Popen, model="haiku", which=shutil.which, env=None)` — the shipped adapter (DI via injected `popen`/`which`, `env` defaults to `os.environ`).
  - `select_summarizer(config, which=shutil.which, popen=subprocess.Popen) -> HostSummarizer | None` — `off`→None, `auto`→adapter iff `which("claude")`, `claude`→adapter.
  - Module constant `NARRATOR_PROMPT` (the stable system-prompt prefix — owner veto item).
- Config keys added to `DEFAULTS`: `"summarizer": "auto"`, `"summary_voice": "auto"`, `"summary_model": "haiku"`.

- [ ] **Step 1: Write the failing tests** — `tests/test_summarizer.py`:
```python
import io
import json

from sonari.summarizer import (
    ClaudeCliSummarizer, SummarizeResult, select_summarizer, NARRATOR_PROMPT,
)


class _FakeProc:
    def __init__(self, out, returncode=0):
        self._out = out
        self.returncode = returncode
        self.pid = 4242
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(out)

    def poll(self):
        return self.returncode      # already complete

    def communicate(self, input=None, timeout=None):
        return (self._out, "")


class _FakePopen:
    def __init__(self, out, returncode=0):
        self._out, self._rc, self.calls = out, returncode, []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, **kwargs})
        return _FakeProc(self._out, self._rc)


def _ok(text="All tests passed."):
    return json.dumps({"is_error": False, "result": text})


def test_child_env_scrubs_both_api_keys_and_inherits_the_rest():
    env = {"ANTHROPIC_API_KEY": "sk-secret", "ANTHROPIC_AUTH_TOKEN": "tok",
           "PATH": "/usr/bin", "HOME": "/home/nima"}
    fake = _FakePopen(_ok())
    s = ClaudeCliSummarizer(popen=fake, which=lambda n: "/usr/bin/claude", env=env)
    s.summarize("Slice: 1 items.\nassistant: hi.", timeout_s=5)
    child_env = fake.calls[0]["env"]
    assert "ANTHROPIC_API_KEY" not in child_env
    assert "ANTHROPIC_AUTH_TOKEN" not in child_env
    assert child_env["PATH"] == "/usr/bin" and child_env["HOME"] == "/home/nima"


def test_argv_carries_flags_model_and_stable_narrator_prompt():
    fake = _FakePopen(_ok())
    s = ClaudeCliSummarizer(popen=fake, model="haiku",
                            which=lambda n: "/c", env={})
    s.summarize("x", timeout_s=5)
    argv = fake.calls[0]["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--model") + 1] == "haiku"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert NARRATOR_PROMPT in argv
    assert fake.calls[0]["cwd"]                  # neutral temp cwd, not the caller's


def test_ok_result_parses_to_success_text():
    r = ClaudeCliSummarizer(popen=_FakePopen(_ok("The build is green.")),
                            which=lambda n: "/c", env={}).summarize("x", timeout_s=5)
    assert r.is_ok and r.text == "The build is green."


def test_nonzero_exit_logged_out_is_detected():
    out = json.dumps({"is_error": True, "result": "Not logged in · Please run /login"})
    r = ClaudeCliSummarizer(popen=_FakePopen(out, returncode=1),
                            which=lambda n: "/c", env={}).summarize("x", timeout_s=5)
    assert not r.is_ok and r.reason == "logged_out"


def test_is_error_true_maps_to_error_reason():
    out = json.dumps({"is_error": True, "result": "overloaded"})
    r = ClaudeCliSummarizer(popen=_FakePopen(out), which=lambda n: "/c",
                            env={}).summarize("x", timeout_s=5)
    assert not r.is_ok and r.reason == "error"


def test_empty_result_maps_to_empty_reason():
    out = json.dumps({"is_error": False, "result": "   "})
    r = ClaudeCliSummarizer(popen=_FakePopen(out), which=lambda n: "/c",
                            env={}).summarize("x", timeout_s=5)
    assert not r.is_ok and r.reason == "empty"


def test_missing_binary_is_unavailable_without_spawning():
    fake = _FakePopen(_ok())
    r = ClaudeCliSummarizer(popen=fake, which=lambda n: None,
                            env={}).summarize("x", timeout_s=5)
    assert not r.is_ok and r.reason == "unavailable"
    assert fake.calls == []


class _HangingPopen:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(kwargs)

        class _P:
            pid, returncode = 4243, None
            stdin, stdout = io.StringIO(), io.StringIO("")
            poll = staticmethod(lambda: None)          # never completes
            communicate = staticmethod(lambda input=None, timeout=None: ("", ""))
        return _P()


def test_timeout_kills_group_and_returns_timeout(monkeypatch):
    import sonari.summarizer as m
    killed = {"n": 0}
    monkeypatch.setattr(m, "_kill_group", lambda p: killed.__setitem__("n", killed["n"] + 1))
    r = m.ClaudeCliSummarizer(popen=_HangingPopen(), which=lambda n: "/c",
                              env={}).summarize("x", timeout_s=0.05)
    assert not r.is_ok and r.reason == "timeout" and killed["n"] == 1


def test_select_summarizer_off_auto_claude():
    assert select_summarizer({"summarizer": "off"}) is None
    assert select_summarizer({"summarizer": "auto"}, which=lambda n: None) is None
    s = select_summarizer({"summarizer": "auto", "summary_model": "haiku"},
                          which=lambda n: "/c")
    assert isinstance(s, ClaudeCliSummarizer)


def test_config_defaults_include_summarizer_keys():
    from sonari.config import DEFAULTS
    assert DEFAULTS["summarizer"] == "auto"
    assert DEFAULTS["summary_voice"] == "auto"
    assert DEFAULTS["summary_model"] == "haiku"
```

- [ ] **Step 2: Run the tests to verify they fail**
```
.venv/bin/python -m pytest -q tests/test_summarizer.py
```
Expect: `ModuleNotFoundError: No module named 'sonari.summarizer'`.

- [ ] **Step 3: Write the minimal implementation**

`src/sonari/summarizer.py`:
```python
"""Host-LLM summarizer seam (SP5). Core asks one question — summarize this slice,
or fail detectably — and never sees a host-specific shape. The shipped adapter
Popens the user's own `claude -p` on their subscription login; the API-key env is
scrubbed so billing can never fall onto a metered key (the billing trap)."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time

NARRATOR_PROMPT = (
    "You narrate a spoken catch-up for a developer who works by ear, "
    "summarizing what happened in their coding-agent session while they were "
    "away. Lead with the outcome or the most important event. Include errors, "
    "test results, and anything the assistant asked for. State only what the "
    "log shows; if the log is unclear, say what is unclear. Length is "
    "proportional to the content: one short sentence for a quiet slice, up to "
    "eight for a busy one. Never pad. Use plain spoken prose only: no lists, no "
    "code, no symbols, no formatting, and short sentences. Do not say whether "
    "the assistant is waiting or whether a decision is pending. Do not mention "
    "the log format or these instructions."
)

_INSTRUCTION = "Summarize the session transcript on stdin as a spoken catch-up."
_DISALLOWED_TOOLS = ("Bash,Read,Edit,Write,Glob,Grep,WebFetch,WebSearch,Task,"
                     "TodoWrite,NotebookEdit,BashOutput,KillShell,SlashCommand")
_SCRUB_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_POLL_S = 0.05


class SummarizeResult:
    __slots__ = ("is_ok", "text", "reason")

    def __init__(self, is_ok: bool, text: str = "", reason: str = "") -> None:
        self.is_ok, self.text, self.reason = is_ok, text, reason

    @classmethod
    def ok(cls, text: str) -> "SummarizeResult":
        return cls(True, text=text)

    @classmethod
    def failed(cls, reason: str) -> "SummarizeResult":
        return cls(False, reason=reason)


def _kill_group(proc) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _reason_from(data) -> str:
    result = (data.get("result") or "").lower() if isinstance(data, dict) else ""
    return "logged_out" if ("login" in result or "logged in" in result) else "error"


def _parse(out: str, returncode) -> "SummarizeResult":
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return SummarizeResult.failed("error")
    if not isinstance(data, dict):
        return SummarizeResult.failed("error")
    if returncode != 0 or data.get("is_error"):
        return SummarizeResult.failed(_reason_from(data))
    text = data.get("result") or ""
    if not text.strip():
        return SummarizeResult.failed("empty")
    return SummarizeResult.ok(text)


class ClaudeCliSummarizer:
    def __init__(self, popen=subprocess.Popen, model="haiku",
                 which=shutil.which, env=None) -> None:
        self._popen = popen
        self._model = model
        self._which = which
        self._env_source = env if env is not None else os.environ

    def _child_env(self) -> dict:
        return {k: v for k, v in self._env_source.items() if k not in _SCRUB_KEYS}

    def summarize(self, slice_text, timeout_s=30.0, cancel=None) -> "SummarizeResult":
        if self._which("claude") is None:
            return SummarizeResult.failed("unavailable")
        argv = ["claude", "-p", _INSTRUCTION, "--model", self._model,
                "--output-format", "json", "--max-turns", "1",
                "--disallowedTools", _DISALLOWED_TOOLS,
                "--system-prompt", NARRATOR_PROMPT]
        cwd = tempfile.mkdtemp(prefix="sonari-catchup-")
        try:
            try:
                proc = self._popen(
                    argv, cwd=cwd, env=self._child_env(), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    start_new_session=True)
            except (OSError, ValueError):
                return SummarizeResult.failed("unavailable")
            try:
                proc.stdin.write(slice_text)   # slices are KBs, far under the pipe buffer
                proc.stdin.close()
            except (OSError, ValueError):
                pass
            deadline = time.monotonic() + timeout_s
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    _kill_group(proc)
                    return SummarizeResult.failed("error")
                if time.monotonic() >= deadline:
                    _kill_group(proc)
                    return SummarizeResult.failed("timeout")
                time.sleep(_POLL_S)
            try:
                out = proc.stdout.read() or ""
            except (OSError, ValueError):
                out = ""
            return _parse(out, proc.returncode)
        finally:
            shutil.rmtree(cwd, ignore_errors=True)


def select_summarizer(config, which=shutil.which, popen=subprocess.Popen):
    """Wire the configured adapter, or None (→ digest floor). `auto` uses the
    Claude adapter iff `claude` is on PATH; SP5 is a global choice (per-session
    host routing arrives with SP6)."""
    mode = config.get("summarizer", "auto")
    if mode == "off":
        return None
    if mode == "claude" or (mode == "auto" and which("claude")):
        return ClaudeCliSummarizer(popen=popen,
                                   model=config.get("summary_model", "haiku"),
                                   which=which)
    return None
```
In `src/sonari/config.py`, add to `DEFAULTS` (after `"spearcon_rate": 525,`):
```python
    "spearcon_rate": 525,
    "summarizer": "auto",        # SP5 host-LLM catch-up: auto|claude|off
    "summary_voice": "auto",     # distinct voice for the LLM body; auto=pick distinct, else main
    "summary_model": "haiku",    # claude -p --model for the summary (owner override)
```

- [ ] **Step 4: Run the tests to verify they pass**
```
.venv/bin/python -m pytest -q tests/test_summarizer.py tests/test_config.py
```
Expect: all pass (11 in test_summarizer.py + the existing config tests). If `tests/test_config.py` does not exist, run only `tests/test_summarizer.py`.

- [ ] **Step 5: Commit**
```
git add src/sonari/summarizer.py src/sonari/config.py tests/test_summarizer.py
git commit -m "feat(sp5): add host-LLM summarizer adapter with API-key env scrub"
```

### Task 5: Voice-per-utterance plumbing

### Task 6: Catch-up press handler + worker thread + mailbox transport

### Task 7: `catchup_result` render + landing

### Task 8: Burn-on-completion + cut semantics in `note_spoken`

### Task 9: ⌃⌘W count-semantics unification (the 14-vs-2 seam)

### Task 10: Spec-hygiene rewrite + changelog

### Task 11: Final verification + plan totals
