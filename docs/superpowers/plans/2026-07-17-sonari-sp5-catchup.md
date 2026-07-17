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

### Task 8: Burn-on-completion + cut semantics in `note_spoken`

### Task 9: ⌃⌘W count-semantics unification (the 14-vs-2 seam)

### Task 10: Spec-hygiene rewrite + changelog

### Task 11: Final verification + plan totals
