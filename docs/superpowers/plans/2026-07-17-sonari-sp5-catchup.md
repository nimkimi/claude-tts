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

**Files:**
- Modify: `src/sonari/queue.py:7-20` (SpeechItem: one new field)
- Modify: `src/sonari/speaker.py:52-97` (`speak` accepts a per-call voice override)
- Modify: `src/sonari/daemon/host.py` (`_enqueue` :231-260 threads `voice`; the FOUR `speak()` call sites at ~515-519 held branch and ~588-592 normal branch pass `voice=item.voice`)
- Modify: `tests/daemon_helpers.py:36-54` (FakeSpeaker records the per-call voice)
- Test: `tests/test_voice_per_utterance.py` (new)

**Interfaces:**
- Produces: `SpeechItem.voice: "str | None" = None`; `Speaker.speak(text=None, audio_path=None, voice=None, cancel_epoch=None)` — `voice` overrides `self._voice` for exactly that call, reverting after; `_enqueue(..., voice=None)` passes it onto the item. FakeSpeaker gains `self.spoken_voices: list` (one entry per `speak()`).
- Rationale (advisor): the catch-up render items are `pause_exempt`, so the body plays through the HELD branch when the target session is stopped — the voice MUST be threaded at the held-branch call sites too, or a stopped-session catch-up body silently reverts to the main voice.

- [ ] **Step 1: Write the failing test** — `tests/test_voice_per_utterance.py`:
```python
from sonari.speaker import Speaker
from tests.daemon_helpers import make_daemon


class _Proc:
    returncode = 0

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass


def test_speaker_speak_overrides_voice_then_reverts():
    seen = []

    def runner(text, voice, rate):
        seen.append(voice)
        return _Proc()

    sp = Speaker(voice="Main", rate=200, say_runner=runner)
    assert sp.speak("hi", voice="Alt") is True
    assert sp.speak("bye") is True
    assert seen == ["Alt", "Main"]


def test_item_voice_reaches_speaker_through_the_loop():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "Body sentence.", False, voice="Daniel")
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Body sentence."
    assert speaker.spoken_voices[-1] == "Daniel"


def test_default_item_voice_is_none():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._enqueue("fg", "prose", "Plain.", False)
    daemon._speak_loop_once()
    assert speaker.spoken_voices[-1] is None
```

- [ ] **Step 2: Run the test to verify it fails**
```
.venv/bin/python -m pytest -q tests/test_voice_per_utterance.py
```
Expect: `TypeError: __init__() got an unexpected keyword argument 'voice'` (SpeechItem via `_enqueue`) / `AttributeError: 'FakeSpeaker' object has no attribute 'spoken_voices'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/sonari/queue.py`, add to the `SpeechItem` dataclass after `forward`:
```python
    forward: bool = False  # SP4 provenance: True only at forward-readout enqueue sites (prose/decision/
                           # tool-announce readout). Browse-replay + control cues stay False so a review
                           # gesture never advances the frontier (B1). Read only by note_spoken's advance.
    voice: "str | None" = None  # SP5: per-utterance say voice override (the summary body); None == main voice
```
In `src/sonari/speaker.py`, change the `speak` signature and the say-runner call:
```python
    def speak(self, text=None, audio_path=None, voice=None, cancel_epoch=None) -> bool:
```
and inside, replace the runner call for the say path (the `else` of the `audio_path is not None` spawn):
```python
        say_voice = voice if voice is not None else self._voice
        proc = (runner(audio_path) if audio_path is not None
                else runner(text, say_voice, self._rate))
```
In `src/sonari/daemon/host.py`, add `voice=None` to `_enqueue`'s signature and pass it to `SpeechItem`:
```python
    def _enqueue(self, session: str, kind: str, text: str, is_decision: bool,
                 entry=None, mute_exempt: bool = False,
                 pause_exempt: bool = False, at_front: bool = False,
                 names_session: bool = False, audio_path=None,
                 forward: bool = False, voice=None) -> int:
```
and in the `SpeechItem(...)` construction add `voice=voice,` alongside `forward=forward,`. Then at ALL FOUR `speak()` call sites in `_speak_loop_once` (held branch ~515-519 and normal branch ~588-592), add `voice=item.voice`, e.g.:
```python
            if item.audio_path:
                completed = self.speaker.speak(
                    item.text, audio_path=item.audio_path,
                    voice=item.voice, cancel_epoch=cancel_epoch)
            else:
                completed = self.speaker.speak(
                    item.text, voice=item.voice, cancel_epoch=cancel_epoch)
```
(held branch uses `item.text`; the normal branch uses the attributed `text` — keep each branch's existing first argument, only add `voice=item.voice`).
In `tests/daemon_helpers.py`, add `self.spoken_voices: list = []` in `FakeSpeaker.__init__` and record it:
```python
    def speak(self, text=None, audio_path=None, voice=None, cancel_epoch=None) -> bool:
        self.spoken.append(text)
        self.audio_paths.append(audio_path)
        self.spoken_voices.append(voice)
        return self.complete
```

- [ ] **Step 4: Run the tests to verify they pass**
```
.venv/bin/python -m pytest -q tests/test_voice_per_utterance.py tests/test_speaker.py
```
Expect: the 3 new tests pass and every existing speaker test stays green.

- [ ] **Step 5: Commit**
```
git add src/sonari/queue.py src/sonari/speaker.py src/sonari/daemon/host.py tests/daemon_helpers.py tests/test_voice_per_utterance.py
git commit -m "feat(sp5): per-utterance voice override through the speak loop"
```

### Task 6: Catch-up press handler + worker thread + mailbox transport

**Files:**
- Create: `src/sonari/daemon/features/catchup.py` (new feature module — the `catch_up` handler + worker + cancel; the `catchup_result` handler is added in Task 7 in this same file)
- Modify: `src/sonari/daemon/host.py` (add `summarizer` ctor param + `_summarizer()`; the catch-up in-flight fields + `queue.Queue` inbox in `__init__`; `_drain_catchup_inbox()` called at the TOP of `_speak_loop_once`; import the new feature module)
- Modify: `src/sonari/daemon/__init__.py:11` (add `MsgType.CATCH_UP` to `assert_complete`)
- Modify: `tests/daemon_helpers.py` (add `FakeSummarizer`; `make_daemon(..., summarizer=None)`)
- Test: `tests/test_catchup_press.py` (new)

**Interfaces:**
- Consumes: `select_summarizer`/`SummarizeResult` (Task 4), `render_slice`/`build_digest` (Task 3), `MsgType.CATCH_UP`/`CATCHUP_RESULT` (Task 1), `_enqueue(..., voice=)` (Task 5).
- Produces (daemon state, mutated ONLY on the loop): `host._catchup` — the in-flight bundle `{"id", "target", "folder", "slice_end", "digest", "cancel": threading.Event, "phase": "preparing"|"rendering", "render_id": int|None, "ended": bool}` or `None`; `host._catchup_seq: int`; `host._catchup_inbox: queue.Queue`; `host._summarizer() -> HostSummarizer | None`; `host._drain_catchup_inbox()`. The bundle survives SESSION_END (daemon state, not sessions/history) — so the Task 7 result handler can render `"{folder} ended."` from it.
- The worker thread closure captures `summarizer`, `slice_text`, `cancel`, `request_id`, `host` as LOCALS and only `host._catchup_inbox.put(...)` + `host._wake.set()` — it reads/writes NO daemon state.

- [ ] **Step 1: Write the failing tests** — `tests/test_catchup_press.py`:
```python
from sonari.summarizer import SummarizeResult
from tests.daemon_helpers import make_daemon, FakeSummarizer


def _catch_up(session="fg"):
    return {"v": 1, "type": "catch_up", "session": session}


def test_empty_pile_says_nothing_to_catch_up_and_no_worker():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon.handle_message(_catch_up())
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Nothing to catch up."
    assert daemon._catchup is None


def test_ack_announces_pile_magnitude_and_folder():
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=FakeSummarizer())
    sessions.set_foreground("fg", cwd="/x/myrepo")
    for i in range(3):
        daemon.history.record("fg", "prose", "line {0}.".format(i))
    daemon.handle_message(_catch_up())
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Catching up 3 items in myrepo."
    assert daemon._catchup is not None and daemon._catchup["phase"] == "preparing"


def test_singular_item_ack():
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=FakeSummarizer())
    sessions.set_foreground("fg", cwd="/x/myrepo")
    daemon.history.record("fg", "prose", "only one.")
    daemon.handle_message(_catch_up())
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Catching up 1 item in myrepo."


def test_aged_out_rider_rides_the_ack():
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=FakeSummarizer())
    sessions.set_foreground("fg", cwd="/x/myrepo")
    daemon.history.record("fg", "prose", "a.")
    daemon._stream("fg").frontier = (-1, -1)   # behind the oldest entry -> aged_out
    daemon.handle_message(_catch_up())
    daemon._speak_loop_once()
    assert speaker.spoken[-1] == "Earlier output aged out. Catching up 1 item in myrepo."


def test_worker_posts_result_and_press_pins_slice_end():
    fake = FakeSummarizer(result=SummarizeResult.ok("Done."))
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=fake)
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "one.")
    e1 = daemon.history.record("fg", "prose", "two.")
    daemon.handle_message(_catch_up())
    posted = daemon._catchup_inbox.get(timeout=2)     # worker thread posted it
    assert posted["type"] == "catchup_result"
    assert posted["ok"] is True and posted["text"] == "Done."
    assert fake.calls and "assistant: two." in fake.calls[0]
    assert daemon._catchup["slice_end"] == (e1.msg_id, e1.seq)


def test_no_summarizer_posts_unavailable_without_a_worker():
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=None)
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "a.")
    daemon.handle_message(_catch_up())
    posted = daemon._catchup_inbox.get(timeout=2)
    assert posted["ok"] is False and posted["reason"] == "unavailable"


def test_press_while_in_flight_cancels_no_new_worker():
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=FakeSummarizer())
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "a.")
    daemon.handle_message(_catch_up())
    cancel_event = daemon._catchup["cancel"]
    daemon.handle_message(_catch_up())       # second press = pure cancel
    assert daemon._catchup is None and cancel_event.is_set()
    daemon._speak_loop_once()
    assert "Cancelled." in speaker.spoken


def test_mailbox_drains_on_speak_loop_tick():
    daemon, queue, speaker, sessions, config = make_daemon()
    daemon._catchup_inbox.put({"v": 1, "type": "catchup_result", "request_id": 999,
                               "ok": False, "text": "", "reason": "error"})
    daemon._speak_loop_once()
    assert daemon._catchup_inbox.empty()     # drained (dispatched; no handler yet = no-op)
```

- [ ] **Step 2: Run the tests to verify they fail**
```
.venv/bin/python -m pytest -q tests/test_catchup_press.py
```
Expect: `TypeError: make_daemon() got an unexpected keyword argument 'summarizer'` / `ImportError: cannot import name 'FakeSummarizer'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/sonari/daemon/host.py`: add `import queue` at the top; add `summarizer=None` to `SpeechDaemon.__init__`'s signature; inside `__init__` (after the `_last_drain` lines) add:
```python
        # SP5 catch-up: the in-flight bundle (mutated only on the daemon loop),
        # a monotonic request id, and the worker→loop mailbox.
        self._summarizer_override = summarizer
        self._catchup = None
        self._catchup_seq = 0
        self._catchup_inbox = queue.Queue()
```
Add the feature side-effect import beside the others (host.py:22-30): `from sonari.daemon.features import catchup  # noqa: F401`. Add two methods to the class:
```python
    def _summarizer(self):
        if self._summarizer_override is not None:
            return self._summarizer_override
        from sonari.summarizer import select_summarizer
        return select_summarizer(self.config)

    def _drain_catchup_inbox(self) -> None:
        """Deliver any worker-posted catchup_result on the daemon loop, under the
        transaction lock (mirrors the socket/hotkey dispatch). Called at the top of
        _speak_loop_once BEFORE the held-branch return so results land in all states."""
        while True:
            try:
                msg = self._catchup_inbox.get_nowait()
            except queue.Empty:
                break
            with self._state.transaction():
                self.handle_message(msg)
```
Make `self._drain_catchup_inbox()` the FIRST line of `_speak_loop_once` (before `fg0 = ...`).

Create `src/sonari/daemon/features/catchup.py`:
```python
from __future__ import annotations

import threading

from sonari.protocol import MsgType, PROTOCOL_VERSION
from sonari.daemon.registry import handler
from sonari.catchup import render_slice, build_digest


def _result_msg(request_id, result):
    return {"v": PROTOCOL_VERSION, "type": MsgType.CATCHUP_RESULT,
            "request_id": request_id, "ok": result.is_ok,
            "text": result.text, "reason": result.reason}


def _cue_dest(sessions, target):
    # Route audible cues to the SPEAKER when it diverges from the caught-up target
    # (the SP4 skip-cue lesson: a diverged target's stream isn't heard). Else target.
    spk = sessions.speaker()
    return spk if (spk is not None and spk != target) else target


@handler(MsgType.CATCH_UP)
def on_catch_up(ctx, msg):
    host = ctx.host
    sessions = host.sessions
    if host._catchup is not None:            # in flight -> pure cancel (§2.9)
        _cancel_catchup(host)
        return None
    target = sessions.workspace()
    if target is None:
        host.speaker.earcon("error")
        return None
    st = host._stream(target)
    entries, aged_out = host.history.unheard_from_frontier(target, st.frontier)
    folder = sessions.folder(target)
    dest = _cue_dest(sessions, target)
    if not entries:
        host._enqueue(dest, "prose", "Nothing to catch up.", False,
                      mute_exempt=True, pause_exempt=True, at_front=True)
        return None
    n = len(entries)
    where = "in {0}".format(folder) if folder else "in another session"
    ack = "Catching up {0} {1} {2}.".format(n, "item" if n == 1 else "items", where)
    if aged_out:
        ack = "Earlier output aged out. " + ack
    host._enqueue(dest, "prose", ack, False,
                  mute_exempt=True, pause_exempt=True, at_front=True)
    last = entries[-1]
    slice_text = render_slice(entries, folder)      # pinned + rendered AT PRESS
    host._catchup_seq += 1
    request_id = host._catchup_seq
    cancel = threading.Event()
    host._catchup = {"id": request_id, "target": target, "folder": folder,
                     "slice_end": (last.msg_id, last.seq),
                     "digest": build_digest(entries), "cancel": cancel,
                     "phase": "preparing", "render_id": None, "ended": False}
    summarizer = host._summarizer()
    if summarizer is None:                          # no adapter -> straight to the floor
        from sonari.summarizer import SummarizeResult
        host._catchup_inbox.put(_result_msg(request_id, SummarizeResult.failed("unavailable")))
        host._wake.set()
        return None

    def _run():                                     # worker: touches NO daemon state
        result = summarizer.summarize(slice_text, timeout_s=30.0, cancel=cancel)
        host._catchup_inbox.put(_result_msg(request_id, result))
        host._wake.set()
    threading.Thread(target=_run, daemon=True).start()
    return None


def _cancel_catchup(host):
    cu = host._catchup
    if cu is None:
        return
    cu["cancel"].set()                       # kill an in-flight child if still preparing
    host._catchup = None                     # Task 8 extends this to cut a SPEAKING render
    dest = _cue_dest(host.sessions, cu["target"])
    if dest is not None:
        host._enqueue(dest, "prose", "Cancelled.", False,
                      mute_exempt=True, pause_exempt=True, at_front=True)
```
In `src/sonari/daemon/__init__.py`, add `MsgType.CATCH_UP,` to the `assert_complete([...])` list (its handler now exists; do NOT add `CATCHUP_RESULT` yet — Task 7).
In `tests/daemon_helpers.py`, add the fake summarizer and the `make_daemon` seam:
```python
class FakeSummarizer:
    """Records the slice text; returns a scripted SummarizeResult (default: ok)."""
    def __init__(self, result=None):
        self.result = result
        self.calls: list = []

    def summarize(self, slice_text, timeout_s=30.0, cancel=None):
        self.calls.append(slice_text)
        if self.result is not None:
            return self.result
        from sonari.summarizer import SummarizeResult
        return SummarizeResult.ok("Fake summary.")
```
and change `make_daemon` to `def make_daemon(verbosity="everything", foreground="fg", summarizer=None):`, add `config["summarizer"] = "off"` after the verbosity line (so no test ever reaches a real `claude`), and pass `summarizer=summarizer` into `SpeechDaemon(...)`.

- [ ] **Step 4: Run the tests to verify they pass**
```
.venv/bin/python -m pytest -q tests/test_catchup_press.py tests/test_protocol.py
```
Expect: the 8 press tests pass; the import-time `assert_complete` guard passes (CATCH_UP has a handler).

- [ ] **Step 5: Commit**
```
git add src/sonari/daemon/features/catchup.py src/sonari/daemon/host.py src/sonari/daemon/__init__.py tests/daemon_helpers.py tests/test_catchup_press.py
git commit -m "feat(sp5): catch-up press handler, worker thread, and result mailbox"
```

### Task 7: `catchup_result` render + landing

**Files:**
- Modify: `src/sonari/queue.py` (SpeechItem: `render_id` + `catchup_burn` fields)
- Modify: `src/sonari/daemon/host.py` (`_enqueue` threads `render_id`/`catchup_burn`; `_voices_provider` attr + `_installed_voices()`)
- Modify: `src/sonari/catchup.py` (append `resolve_summary_voice`)
- Modify: `src/sonari/daemon/features/catchup.py` (add the `on_catchup_result` handler)
- Modify: `src/sonari/daemon/__init__.py` (add `MsgType.CATCHUP_RESULT` to `assert_complete`)
- Modify: `tests/daemon_helpers.py` (`make_daemon` sets `daemon._voices_provider = lambda: []` for hermetic renders)
- Test: `tests/test_catchup_render.py` (new)

**Interfaces:**
- Consumes: `sanitize_summary` (Task 2), `build_digest` (Task 3), `_has_decision` from `features/control.py`, `resolve_summary_voice` (this task), `_enqueue(..., voice=, render_id=, catchup_burn=)`.
- Produces: `SpeechItem.render_id: int|None`, `SpeechItem.catchup_burn: bool=False`; `resolve_summary_voice(cfg_value, main_voice, voices) -> str|None` (concrete name wins; `auto` picks the first voice `!= main_voice`, else main voice; `off`/`None` → main voice); `host._installed_voices() -> list[str]`. The result handler id-matches `msg["request_id"]` against `host._catchup["id"]` (stale → drop), assembles `[ "{folder} ended." (if ended) ] + [ frame+body | digest ] + [ tail if decision & not ended ]`, routes to `_cue_dest`, enqueues reversed-at_front (so play order is preserved), and marks the LAST item `catchup_burn=True` unless ended. Task 8 consumes `render_id`/`catchup_burn` in `note_spoken`.

- [ ] **Step 1: Write the failing tests** — `tests/test_catchup_render.py`:
```python
import threading

from sonari.catchup import resolve_summary_voice
from tests.daemon_helpers import make_daemon


def _result(rid, ok, text="", reason=""):
    return {"v": 1, "type": "catchup_result", "request_id": rid,
            "ok": ok, "text": text, "reason": reason}


def _inflight(daemon, target="fg", folder="myrepo",
              digest="Summary unavailable. Last: x."):
    daemon._catchup = {"id": 1, "target": target, "folder": folder,
                       "slice_end": (0, 0), "digest": digest,
                       "cancel": threading.Event(), "phase": "preparing",
                       "render_id": None, "ended": False}
    return 1


def _drain(daemon, n=4):
    for _ in range(n):
        daemon._speak_loop_once()


def test_resolve_summary_voice_rules():
    assert resolve_summary_voice("Daniel", "Alex", ["Alex", "Daniel"]) == "Daniel"
    assert resolve_summary_voice("auto", "Alex", ["Alex", "Samantha"]) == "Samantha"
    assert resolve_summary_voice("auto", "Alex", ["Alex"]) == "Alex"
    assert resolve_summary_voice("auto", None, []) is None
    assert resolve_summary_voice("off", "Alex", ["Bob"]) == "Alex"


def test_success_renders_frame_then_body_in_summary_voice():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/myrepo")
    config["summary_voice"] = "Daniel"
    rid = _inflight(daemon, target="fg", folder="myrepo")
    daemon.handle_message(_result(rid, ok=True, text="The build is green."))
    _drain(daemon)
    assert speaker.spoken[:2] == ["Summary:", "The build is green."]
    assert speaker.spoken_voices[0] is None          # frame -> main voice
    assert speaker.spoken_voices[1] == "Daniel"      # body -> summary voice


def test_pending_decision_appends_tail_last():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    daemon._pending_decisions["fg"] = {"event": None, "behavior": None,
                                       "text": "?", "item_id": None}
    rid = _inflight(daemon, target="fg", folder="r")
    daemon.handle_message(_result(rid, ok=True, text="Ran tests."))
    _drain(daemon)
    assert speaker.spoken[speaker.spoken.index("Ran tests.") + 1] == "Decision waiting."


def test_no_decision_no_tail():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    rid = _inflight(daemon, target="fg", folder="r")
    daemon.handle_message(_result(rid, ok=True, text="Ran tests."))
    _drain(daemon)
    assert "Decision waiting." not in speaker.spoken


def test_failure_renders_digest_main_voice_no_frame():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    config["summary_voice"] = "Daniel"
    rid = _inflight(daemon, folder="r", digest="Summary unavailable. Last: All done.")
    daemon.handle_message(_result(rid, ok=False, reason="timeout"))
    _drain(daemon)
    assert "Summary:" not in speaker.spoken
    assert speaker.spoken[0] == "Summary unavailable. Last: All done."
    assert speaker.spoken_voices[0] is None


def test_empty_summary_falls_to_digest():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    rid = _inflight(daemon, folder="r", digest="Summary unavailable. Last: x.")
    daemon.handle_message(_result(rid, ok=True, text="```\n\n```"))
    _drain(daemon)
    assert "Summary:" not in speaker.spoken
    assert "Summary unavailable. Last: x." in speaker.spoken


def test_session_ended_midprep_prepends_folder_ended_no_tail():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("live", cwd="/x/live")   # a live speaker to voice on
    daemon._pending_decisions["gone"] = {"event": None, "behavior": None,
                                         "text": "?", "item_id": None}
    rid = _inflight(daemon, target="gone", folder="oldrepo")   # 'gone' unregistered
    daemon.handle_message(_result(rid, ok=True, text="It finished the refactor."))
    _drain(daemon)
    assert speaker.spoken[0] == "oldrepo ended."
    assert "Summary:" in speaker.spoken
    assert "Decision waiting." not in speaker.spoken


def test_stale_result_after_cancel_is_dropped():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    rid = _inflight(daemon)
    daemon._catchup = None                           # a cancel landed first
    daemon.handle_message(_result(rid, ok=True, text="Late summary."))
    _drain(daemon)
    assert "Late summary." not in speaker.spoken and "Summary:" not in speaker.spoken


def test_wrong_request_id_is_dropped():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    _inflight(daemon)                                # id == 1
    daemon.handle_message(_result(999, ok=True, text="Wrong."))
    _drain(daemon)
    assert "Wrong." not in speaker.spoken
```

- [ ] **Step 2: Run the tests to verify they fail**
```
.venv/bin/python -m pytest -q tests/test_catchup_render.py
```
Expect: `ImportError: cannot import name 'resolve_summary_voice'` (and, once that exists, the render assertions fail — no `catchup_result` handler yet).

- [ ] **Step 3: Write the minimal implementation**

In `src/sonari/queue.py`, add to `SpeechItem` after `voice`:
```python
    voice: "str | None" = None  # SP5: per-utterance say voice override (the summary body); None == main voice
    render_id: "int | None" = None  # SP5: groups a catch-up render's frame/body/tail items
    catchup_burn: bool = False  # SP5: True on the render's LAST item; note_spoken burns on its completion
```
In `src/sonari/daemon/host.py`, extend `_enqueue`'s signature with `render_id=None, catchup_burn=False` and pass both to `SpeechItem(...)`. Add `self._voices_provider = None` and `self._voices_cache = None` in `__init__` (beside the catch-up fields) and a method:
```python
    def _installed_voices(self) -> list:
        """The say voices, best-effort. Overridable via _voices_provider so tests
        stay hermetic (no `say -v ?`), and MEMOIZED so the one `say -v ?` a catch-up
        render can trigger runs at most once per daemon (it can run under the loop
        lock, and voices don't change within a session)."""
        if self._voices_provider is not None:
            return self._voices_provider()
        if self._voices_cache is None:
            try:
                from sonari.platform import get_platform
                self._voices_cache = list(get_platform().tts.list_voices())
            except Exception:  # noqa: BLE001 - a voice-list failure falls to the main voice
                self._voices_cache = []
        return self._voices_cache
```
Append to `src/sonari/catchup.py`:
```python
def resolve_summary_voice(cfg_value, main_voice, voices):
    """The body voice. A concrete configured name wins. 'auto' picks the first
    installed voice distinct from the main voice, falling back to the main voice
    (the frame word still marks the synthetic channel). 'off'/None -> main voice."""
    if isinstance(cfg_value, str) and cfg_value not in ("auto", "off", ""):
        return cfg_value
    if cfg_value == "auto":
        for v in (voices or []):
            if v != main_voice:
                return v
    return main_voice
```
In `src/sonari/daemon/features/catchup.py`, extend the imports and add the handler:
```python
from sonari.catchup import render_slice, build_digest, sanitize_summary, resolve_summary_voice
from sonari.daemon.features.control import _has_decision
```
```python
@handler(MsgType.CATCHUP_RESULT)
def on_catchup_result(ctx, msg):
    host = ctx.host
    cu = host._catchup
    if cu is None or cu.get("id") != msg.get("request_id"):
        return None                                  # stale (cancelled/superseded) -> drop
    sessions = host.sessions
    target = cu["target"]
    ended = target not in sessions.session_ids()     # SESSION_END destroyed its live state
    cfg_voice = host.config.get("summary_voice")     # only 'auto' consults the voice list
    voices = host._installed_voices() if cfg_voice == "auto" else []
    body_voice = resolve_summary_voice(cfg_voice, host.config.get("voice"), voices)
    segments = []                                    # ordered (text, voice)
    if ended:
        folder = cu["folder"]
        segments.append(("{0} ended.".format(folder) if folder else "The session ended.", None))
    body = sanitize_summary(msg.get("text", "")) if msg.get("ok") else ""
    if body:
        segments.append(("Summary:", None))          # frame -> main voice
        segments.append((body, body_voice))          # body -> distinct voice
    else:
        segments.append((cu["digest"], None))        # digest replaces frame+body, main voice
    if not ended and _has_decision(host, target):
        segments.append(("Decision waiting.", None))
    render_id = cu["id"]
    cu["render_id"] = render_id
    cu["phase"] = "rendering"
    cu["ended"] = ended
    dest = _cue_dest(sessions, target)
    if dest is None:
        host._catchup = None                         # nowhere audible (last session gone)
        return None
    cu["dest"] = dest                                # the stream the render items live on (for cancel/cut)
    last = len(segments) - 1
    for i in range(last, -1, -1):                    # reverse enqueue -> preserved play order
        text, voice = segments[i]
        host._enqueue(dest, "prose", text, False, mute_exempt=True, pause_exempt=True,
                      at_front=True, voice=voice, render_id=render_id,
                      catchup_burn=(i == last and not ended))
    return None
```
In `src/sonari/daemon/__init__.py`, add `MsgType.CATCHUP_RESULT,` to `assert_complete([...])` (its handler now exists).
In `tests/daemon_helpers.py`, in `make_daemon`, after constructing `daemon`, add `daemon._voices_provider = lambda: []` (hermetic renders: `auto` → main voice).

- [ ] **Step 4: Run the tests to verify they pass**
```
.venv/bin/python -m pytest -q tests/test_catchup_render.py tests/test_catchup_press.py
```
Expect: all render + press tests pass; `assert_complete` still green (both types now have handlers).

- [ ] **Step 5: Commit**
```
git add src/sonari/queue.py src/sonari/daemon/host.py src/sonari/catchup.py src/sonari/daemon/features/catchup.py src/sonari/daemon/__init__.py tests/daemon_helpers.py tests/test_catchup_render.py
git commit -m "feat(sp5): render catch-up summary with frame, distinct-voice body, and tail"
```

### Task 8: Burn-on-completion + cut semantics in `note_spoken`

**Files:**
- Modify: `src/sonari/queue.py` (add `SpeechQueue.remove_where(pred)`)
- Modify: `src/sonari/daemon/host.py` (`note_spoken` render block; `_drop_render_items`/`_burn_catchup` helpers)
- Modify: `src/sonari/daemon/features/catchup.py` (rewrite `_cancel_catchup` to handle the rendering phase)
- Test: `tests/test_catchup_burn.py` (new)

**Interfaces:**
- Consumes: `SpeechItem.render_id`/`catchup_burn` (Task 7), `host._catchup` bundle with `slice_end`/`target`/`dest`/`render_id` (Tasks 6–7).
- Produces: `SpeechQueue.remove_where(pred) -> list` (lock-free; caller holds `self._lock`); `host._drop_render_items(session, render_id)` and `host._burn_catchup(cu)` (both lock-free); the `note_spoken` render lifecycle — on a render item's **completion of the LAST (`catchup_burn`) item** advance the target frontier to the pinned `slice_end` and drop the target's queued items whose history key ≤ `slice_end`; on **any** render item's non-completion suppress the burn AND drop the remaining siblings; both clear `host._catchup`. `_cancel_catchup` now also cuts + drops a speaking render (no burn).
- Invariant: `advance_frontier` is monotonic — a `slice_end` behind the live frontier is a no-op (never retreats).

- [ ] **Step 1: Write the failing tests** — `tests/test_catchup_burn.py`:
```python
import threading

from tests.daemon_helpers import make_daemon


def _result(rid, ok, text="", reason=""):
    return {"v": 1, "type": "catchup_result", "request_id": rid,
            "ok": ok, "text": text, "reason": reason}


def _catch_up(session="fg"):
    return {"v": 1, "type": "catch_up", "session": session}


def _inflight(daemon, target="fg", folder="r", slice_end=(0, 0)):
    daemon._catchup = {"id": 1, "target": target, "folder": folder,
                       "slice_end": slice_end, "digest": "Summary unavailable. Last: x.",
                       "cancel": threading.Event(), "phase": "preparing",
                       "render_id": None, "ended": False}


def _drain(daemon, n=4):
    for _ in range(n):
        daemon._speak_loop_once()


def test_full_completion_burns_to_pinned_slice_end():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "a.")
    e1 = daemon.history.record("fg", "prose", "b.")
    _inflight(daemon, slice_end=(e1.msg_id, e1.seq))
    daemon.handle_message(_result(1, ok=True, text="All done."))
    _drain(daemon)
    assert daemon._stream("fg").frontier == (e1.msg_id, e1.seq)
    assert daemon._catchup is None


def test_cut_render_suppresses_burn_and_keeps_pile():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    for i in range(3):
        daemon.history.record("fg", "prose", "line {0}.".format(i))
    pile, _ = daemon.history.unheard_from_frontier("fg", None)
    _inflight(daemon, slice_end=(pile[-1].msg_id, pile[-1].seq))
    daemon.handle_message(_result(1, ok=True, text="One. Two. Three."))
    daemon._speak_loop_once()          # frame completes
    speaker.complete = False
    daemon._speak_loop_once()          # body cut
    assert daemon._stream("fg").frontier is None        # NO burn
    assert daemon._catchup is None                      # render invalidated
    still, _ = daemon.history.unheard_from_frontier("fg", daemon._stream("fg").frontier)
    assert len(still) == 3                              # pile intact


def test_cut_middle_drops_the_tail_no_lone_continuation():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    daemon._pending_decisions["fg"] = {"event": None, "behavior": None,
                                       "text": "?", "item_id": None}
    e1 = daemon.history.record("fg", "prose", "b.")
    _inflight(daemon, slice_end=(e1.msg_id, e1.seq))
    daemon.handle_message(_result(1, ok=True, text="Body."))
    daemon._speak_loop_once()          # frame completes
    speaker.complete = False
    daemon._speak_loop_once()          # body cut
    speaker.complete = True
    daemon._speak_loop_once()          # the tail was dropped, nothing to speak
    assert "Decision waiting." not in speaker.spoken
    assert daemon._stream("fg").frontier is None


def test_burn_drops_queued_pile_items_at_or_below_slice_end():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    e0 = daemon.history.record("fg", "prose", "a.")
    e1 = daemon.history.record("fg", "prose", "b.")
    daemon._enqueue("fg", "prose", "a.", False, entry=e0, forward=True)
    daemon._enqueue("fg", "prose", "b.", False, entry=e1, forward=True)
    _inflight(daemon, slice_end=(e1.msg_id, e1.seq))
    daemon.handle_message(_result(1, ok=True, text="Summary body."))
    _drain(daemon, 3)
    st = daemon._stream("fg")
    assert st.frontier == (e1.msg_id, e1.seq)
    assert len(st.queue) == 0                          # a./b. dropped on burn
    assert daemon._catchup is None


def test_burn_never_retreats_frontier():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "x.")
    daemon._stream("fg").frontier = (5, 0)
    _inflight(daemon, slice_end=(2, 0))
    daemon.handle_message(_result(1, ok=True, text="x."))
    _drain(daemon)
    assert daemon._stream("fg").frontier == (5, 0)     # monotonic; behind key is a no-op


def test_cancel_during_render_drops_items_and_no_burn():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    e1 = daemon.history.record("fg", "prose", "b.")
    _inflight(daemon, slice_end=(e1.msg_id, e1.seq))
    daemon.handle_message(_result(1, ok=True, text="Body one. Body two."))
    daemon._speak_loop_once()          # frame plays; body queued
    daemon.handle_message(_catch_up())  # cancel while rendering
    assert daemon._catchup is None
    _drain(daemon, 3)
    assert "Body one. Body two." not in speaker.spoken # render items dropped
    assert "Cancelled." in speaker.spoken
    assert daemon._stream("fg").frontier is None       # no burn
```

- [ ] **Step 2: Run the tests to verify they fail**
```
.venv/bin/python -m pytest -q tests/test_catchup_burn.py
```
Expect: `test_full_completion_burns_to_pinned_slice_end` fails (frontier stays `None` — no burn wired yet); several others fail on the missing burn/cut behavior.

- [ ] **Step 3: Write the minimal implementation**

In `src/sonari/queue.py`, add to `SpeechQueue`:
```python
    def remove_where(self, pred) -> "list[SpeechItem]":
        """Remove and return every queued item matching *pred* (SpeechItem -> bool).
        The catch-up burn drops the pinned pile; a cut drops the render's siblings.
        Caller holds the daemon lock."""
        kept, removed = deque(), []
        for it in self._items:
            (removed if pred(it) else kept).append(it)
        self._items = kept
        return removed
```
In `src/sonari/daemon/host.py`, at the END of `note_spoken`'s `with self._lock:` block (after the `item.forward` advance), add:
```python
            # SP5 catch-up render lifecycle. Render items are forward=False, so the
            # block above never advances the frontier: the burn is deferred to the
            # WHOLE sequence completing (R-8, the render is one item). Any cut
            # suppresses the burn and drops the remaining siblings (no lone tail).
            rid = getattr(item, "render_id", None)
            cu = self._catchup
            if rid is not None and cu is not None and cu.get("render_id") == rid:
                if not completed:
                    self._drop_render_items(item.session, rid)
                    self._catchup = None
                elif getattr(item, "catchup_burn", False):
                    self._burn_catchup(cu)
                    self._catchup = None
```
and add two lock-free helper methods to the class (callers already hold `self._lock`):
```python
    def _drop_render_items(self, session, render_id) -> None:
        st = self._state._streams.get(session)
        if st is not None:
            self._drop_pending(
                st.queue.remove_where(lambda it: it.render_id == render_id))

    def _burn_catchup(self, cu) -> None:
        """Advance the caught-up target's frontier to the PINNED slice_end (never
        newest-at-completion) and drop its queued pile items at or below it."""
        st = self._state._streams.get(cu["target"])
        if st is None:
            return
        slice_end = cu["slice_end"]
        st.advance_frontier(slice_end)                # monotonic: a behind key is a no-op
        ph = self._state._pending_heard

        def below(it):
            e = ph.get(it.id)
            return e is not None and (e.msg_id, e.seq) <= slice_end
        self._drop_pending(st.queue.remove_where(below))
```
In `src/sonari/daemon/features/catchup.py`, replace `_cancel_catchup` with the full version:
```python
def _cancel_catchup(host):
    cu = host._catchup
    if cu is None:
        return
    cu["cancel"].set()                       # kill an in-flight child if still preparing
    rid = cu.get("render_id")
    if rid is not None:                      # already speaking: cut + drop the render
        dest = cu.get("dest")
        if dest is not None:
            host._drop_render_items(dest, rid)
        cur = host._current_item
        if cur is not None and getattr(cur, "render_id", None) == rid:
            host.speaker.cancel()
    host._catchup = None                     # no burn on cancel (§2.9)
    dest = _cue_dest(host.sessions, cu["target"])
    if dest is not None:
        host._enqueue(dest, "prose", "Cancelled.", False,
                      mute_exempt=True, pause_exempt=True, at_front=True)
```

- [ ] **Step 4: Run the tests to verify they pass**
```
.venv/bin/python -m pytest -q tests/test_catchup_burn.py tests/test_catchup_render.py tests/test_catchup_press.py
```
Expect: all catch-up runtime tests pass.

- [ ] **Step 5: Commit**
```
git add src/sonari/queue.py src/sonari/daemon/host.py src/sonari/daemon/features/catchup.py tests/test_catchup_burn.py
git commit -m "feat(sp5): burn the pile on full render completion, suppress on cut"
```

### Task 9: ⌃⌘W count-semantics unification (the 14-vs-2 seam)

**Files:**
- Modify: `src/sonari/daemon/features/control.py:41-71` (`_entry_clauses` — `u` switches from the current-turn `history.unheard()` floor to the transcript pile `unheard_from_frontier`)
- Test: `tests/test_catchup_counts.py` (new — the 14-vs-2 reproduction)
- Update (NOT weaken): any existing ⌃⌘W test whose spoken `u` count legitimately changes now that `u` counts the whole pile

**Interfaces:**
- Consumes: `history.unheard_from_frontier(session, frontier)` (existing) and `SessionStream.frontier`.
- Behavior change: `u = max(0, len(unheard_from_frontier(session, st.frontier)) − k)`, floored at 0. The grammar is unchanged (`{k} waiting` before `{u} unheard`, the `unheard` word, clause order); only the NUMBER's source changes — `u` now decomposes the same pile skip and catch-up announce whole. `history.unheard()` stays for machinery that wants current-turn heard-flags; ⌃⌘W stops using it.
- **Scope fence (flag, do NOT fix here):** the `stale` word still reads `history.unheard_age` (current-turn oldest). Spec §8 scopes only `u`; leaving `stale` current-turn is a documented minor inconsistency (u counts the whole pile; stale reflects current-turn age). Confirm existing `stale` tests stay green; do not touch `unheard_age`.

- [ ] **Step 1: Write the failing test** — `tests/test_catchup_counts.py`:
```python
from tests.daemon_helpers import make_daemon


def test_14_vs_2_same_pile_decomposed_by_w():
    # The owner's 14-vs-2: skip/catch-up announce the WHOLE pile; ⌃⌘W's u must
    # decompose that SAME pile, not a current-turn floor. A two-turn pile of 5 on
    # a background session must read "5 unheard", not the old current-turn "3".
    daemon, queue, speaker, sessions, config = make_daemon(verbosity="quiet")
    sessions.set_foreground("fg", cwd="/x/fg")       # the converged speaker
    sessions.register("bg", cwd="/x/bg")
    daemon.history.record("bg", "prose", "t0 a.")
    daemon.history.record("bg", "prose", "t0 b.")
    daemon.history.start_turn("bg")                  # new prompt -> turn 1
    daemon.history.record("bg", "prose", "t1 a.")
    daemon.history.record("bg", "prose", "t1 b.")
    daemon.history.record("bg", "prose", "t1 c.")
    pile, _ = daemon.history.unheard_from_frontier("bg", daemon._stream("bg").frontier)
    assert len(pile) == 5                            # the pile skip/catch-up would announce
    daemon.handle_message({"v": 1, "type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()
    assert "5 unheard" in speaker.spoken[-1]         # bg's Also-map entry, same pile


def test_u_floors_at_zero_when_queue_exceeds_pile():
    daemon, queue, speaker, sessions, config = make_daemon(verbosity="quiet")
    sessions.set_foreground("fg", cwd="/x/fg")
    sessions.register("bg", cwd="/x/bg")
    e = daemon.history.record("bg", "prose", "only one.")
    # frontier past the single entry -> pile empty; a queued item makes k=1 > pile
    daemon._stream("bg").advance_frontier((e.msg_id, e.seq))
    daemon._enqueue("bg", "prose", "queued.", False)
    daemon.handle_message({"v": 1, "type": "where_am_i", "session": "fg"})
    daemon._speak_loop_once()
    assert "unheard" not in speaker.spoken[-1] or "0 unheard" not in speaker.spoken[-1]
```

- [ ] **Step 2: Run the test to verify it fails**
```
.venv/bin/python -m pytest -q tests/test_catchup_counts.py
```
Expect: `test_14_vs_2_same_pile_decomposed_by_w` fails — the readout says `"3 unheard"` (the current-turn floor), not `"5 unheard"`.

- [ ] **Step 3: Write the minimal implementation**

In `src/sonari/daemon/features/control.py`, inside `_entry_clauses`, replace the `u = max(0, len(host.history.unheard(session)) - k)` line (and update the comment) with the pile-based source:
```python
    # W10 → SP5: the unheard count now decomposes the SAME transcript pile that
    # skip and catch-up announce whole (spec §8), not the current-turn floor.
    # unheard_from_frontier is frontier-keyed + heard-flag-independent; subtract
    # k (the queued items, whose history entries are also still unheard) so the
    # split stays imminent-vs-backlog and never double-counts. Floored at 0.
    # (`stale` below still reads the current-turn unheard_age — spec §8 scopes
    # only u; the age word is a separate approximation, unchanged this wave.)
    frontier = st.frontier if st is not None else None
    pile, _ = host.history.unheard_from_frontier(session, frontier)
    u = max(0, len(pile) - k)
```

- [ ] **Step 4: Run the tests + reconcile existing ⌃⌘W tests**
```
.venv/bin/python -m pytest -q tests/test_catchup_counts.py
.venv/bin/python -m pytest -q -k "whereami or where_am_i or also_map or unheard or entry_clauses or control"
```
The new tests pass. For every existing ⌃⌘W test that now fails: the failure must be a pure NUMBER change (the `u` count rising to the true pile magnitude for a browsed/multi-turn/queued pile). Compute the true pile: `len(unheard_from_frontier(session, frontier)) − len(queue)`, floored at 0, and correct the expected count in the assertion. **Do NOT weaken any assertion** (no `==`→`in`, no dropping the negative-substring checks); `{k} waiting`, the `stale` word, and clause order are unchanged. If a failure is anything OTHER than a corrected `u` number (e.g. a clause reordered, `stale` flipped, `waiting` changed), STOP and flag it — that is out of scope for this task.

- [ ] **Step 5: Commit**
```
git add src/sonari/daemon/features/control.py tests/test_catchup_counts.py <any-updated-w-test-files>
git commit -m "feat(sp5): unify unheard count to the transcript pile across surfaces"
```

### Task 10: Spec-hygiene rewrite + changelog

**Files (docs only — no code, no new tests; the grep is the gate):**
- Modify: `docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md` (rewrite the stale verbatim-catch-up model per the §10 table + a top-of-file revision banner)
- Modify: `docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-reconciliation.md` (superseded banner)
- Modify: `docs/superpowers/specs/2026-07-16-sonari-whereami-grammar-v2.md` (reconcile the `u` source)
- Modify: `.superpowers/sdd/progress.md` (dated ledger line — this repo has no CHANGELOG.md; see the flag)

**Spec ambiguity to flag (do not resolve silently):** the SP5 spec §10 table lists a "Changelog | New entry pointing here" row, but this repo has NO `CHANGELOG.md` and the 2026-06-29 design spec has no changelog section. This task routes that requirement to (a) a top-of-file revision banner on the 2026-06-29 spec pointing to `2026-07-17-sonari-sp5-catchup-design.md`, and (b) a dated line in `.superpowers/sdd/progress.md`. Surface this substitution in the task's completion note.

- [ ] **Step 1: Baseline the completeness grep (never section-walk)**
```
grep -n "Reads forward from your frontier through the pile to live" docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md
grep -in "catch-up\|catch_up" docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md
```
Record every hit. The literal "Reads forward…" phrase (currently line 427) is the verbatim-model tell that MUST be rewritten. Every `catch-up`/`catch_up` hit is either rewritten to the summary model or is a historical/neutral mention the §10 table allows to stand.

- [ ] **Step 2: Rewrite each location per the SP5 spec §10 table**

In `docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md`:
- **§8 table row (line ~427) + the ADD paragraph (~440-443):** replace "Reads forward from your frontier through the pile to live" with: catch-up is an **async host-LLM summary** of the pile (never a verbatim forward-read) that **burns the pile on hearing the summary to completion**; SP5 builds it net-new (MsgType + handler + keymap action); chord proposed **⌃⌘L, ships unbound**. Point to `2026-07-17-sonari-sp5-catchup-design.md`.
- **§10.1 (lines ~533-576, incl. the ~562-568 catch-up paragraph + the ~574-576 Observable):** replace the forward-read description with the summary model (SP5 spec §1-§2). **Also apply the C1→C1' correction:** §10.1's ~542-546 still describes C1 (the flood-only skip); update it to C1' — pile-seeking, **workspace-first** (the cue names the target), per the owner ruling 2026-07-17 already live in `playback.py`.
- **§8 preemption line (~389):** change "SP5's catch-up readout" → "SP5's catch-up **landing**" (the redirect class transfers to the sentence-boundary landing, SP5 spec §2).
- **§9 (aged-out ~474-478 + scope ~487-489):** note the aged-out cue now **rides the catch-up ack** (SP5 §2.3); "voluntary and in-place" stands.
- **D17 row (~647; cross-refs D7 ~637, D16 ~646):** "catch-up key" = the summary verb; semantics otherwise unchanged ("left" = stopped/quiet still stands).
- **Top-of-file revision banner** (the "changelog" substitute): add near the header a dated note — `> **2026-07-17 revision:** the catch-up model in §8/§9/§10.1/D17 is superseded by the async host-LLM summary in docs/superpowers/specs/2026-07-17-sonari-sp5-catchup-design.md (built in SP5). The verbatim forward-read described in the original text no longer reflects the shipped behavior.`

- [ ] **Step 3: Superseded banner + whereami-v2 reconcile + ledger line**
- In `docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-reconciliation.md`, add under the header: `> **Superseded (2026-07-17):** a point-in-time audit record. The catch-up verb it maps is now the async summary of docs/superpowers/specs/2026-07-17-sonari-sp5-catchup-design.md. Banner only — the audit body is left as-is.`
- In `docs/superpowers/specs/2026-07-16-sonari-whereami-grammar-v2.md`, reconcile the `u` source (§8 of the SP5 spec): find the line stating `u` remains the current-turn floor (`max(0, unheard−k)`, ~line 51) and amend it to note that **SP5 changed `u`'s SOURCE to the transcript pile** (`unheard_from_frontier`); the grammar (the waiting/unheard split, the `unheard` word) is unchanged. Note the `stale` word still reads the current-turn `unheard_age` (the documented minor inconsistency from Task 9).
- In `.superpowers/sdd/progress.md`, append a dated line: `2026-07-17 — SP5 catch-up (async host-LLM summary + burn + count unification) built; spec docs/superpowers/specs/2026-07-17-sonari-sp5-catchup-design.md; supersedes the verbatim catch-up model in 2026-06-29-sonari-voice-arbitration-design.md §8/§9/§10.1/D17.`

- [ ] **Step 4: Re-run the completeness grep (the gate)**
```
grep -n "Reads forward from your frontier through the pile to live" docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md
grep -in "catch-up\|catch_up" docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md
```
Expect: the literal "Reads forward…" phrase is GONE (rewritten). Every remaining `catch-up`/`catch_up` hit is a historical/neutral mention the §10 table permits (the verb name in state-machine rows, the "catch-up key" gesture label, cross-refs) — NONE still describes a verbatim forward-read. If any surviving hit still asserts the old model, rewrite it before committing.

- [ ] **Step 5: Commit**
```
git add docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-reconciliation.md docs/superpowers/specs/2026-07-16-sonari-whereami-grammar-v2.md .superpowers/sdd/progress.md
git commit -m "docs(sp5): rewrite the stale verbatim catch-up model to the summary verb"
```

### Task 11: Final verification + plan totals

**Files:** none (verification only; if a totals note is recorded, append it to `.superpowers/sdd/progress.md`).

**Interfaces:** none.

- [ ] **Step 1: Run the full suite**
```
.venv/bin/python -m pytest -q
```
Expect: all pass, 1 skipped. Baseline before SP5 was **1105 passed / 1 skipped**; this plan adds ~50 new tests (T1 keymap +1, T2 sanitizer +5, T3 slice/digest +5, T4 summarizer +11, T5 voice +3, T6 press +8, T7 render +9, T8 burn +6, T9 counts +2) plus in-place updates to existing ⌃⌘W tests (count corrections, not new tests), so the target is roughly **~1155 passed / 1 skipped**. Record the EXACT final number here.

- [ ] **Step 2: Confirm the import-time + protocol guards**
```
.venv/bin/python -c "import sonari.daemon"    # assert_complete runs at import: both catch_up + catchup_result must have handlers
.venv/bin/python -m pytest -q tests/test_protocol.py tests/test_concurrency_guards.py
```
Expect: the import is clean (no `AssertionError: MsgType(s) without a handler`), the protocol completeness guard passes with both new types, and every concurrency/monotonicity guard is green.

- [ ] **Step 3: Confirm no live `claude` was invoked by the suite**
```
.venv/bin/python -m pytest -q -k catchup -s 2>&1 | grep -i "not logged in\|claude -p\|Please run /login" && echo "LIVE CALL LEAKED" || echo "no live claude call"
```
Expect: `no live claude call` — every catch-up test drives the injected fake / `handle_message`, never a real subprocess (build-entry gate: the OWNER runs the live smoke tests separately, §4).

- [ ] **Step 4: Record the plan totals note**

Append one dated line to `.superpowers/sdd/progress.md` with: the final suite count (from Step 1), the new-machinery inventory shipped (MsgType `catch_up`+`catchup_result`; `catch_up` keymap action unbound; `HostSummarizer`/`ClaudeCliSummarizer`; `sonari.catchup` pure helpers; config `summarizer`/`summary_voice`/`summary_model`; per-utterance voice; the mailbox transport; the burn/cut lifecycle; the ⌃⌘W count unification), and the **owner-held items still open** (the §4 smoke tests, the ⌃⌘L chord binding, and the ear-pass of every new string + the summary voice + the length ceiling — none of which this build decides).

- [ ] **Step 5: Commit**
```
git add .superpowers/sdd/progress.md
git commit -m "chore(sp5): record catch-up build totals and owner-held items"
```
