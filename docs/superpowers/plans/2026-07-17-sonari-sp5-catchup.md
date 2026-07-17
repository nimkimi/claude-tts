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
- **No Sonari-side retries** around `claude -p`. The CLI retries transient errors internally; any call Sonari sees fail (non-zero exit / `is_error` / timeout) falls STRAIGHT to the deterministic digest. This is deliberate (spec §4): a summary is best-effort, and a failed attempt already cost quota — retrying risks doubling the draw and compounding a false "usage limit" positive. One shot, then the floor.
- **All state changes on the daemon loop.** The worker thread never mutates daemon state (not history, streams, sessions, or `self._catchup` fields the loop reads for rendering). It only calls the summarizer and posts to the mailbox.
- **Guards green at every commit and never weakened:** the 6 concurrency/monotonicity guards must pass at every commit; existing tests are updated for new pile semantics, never weakened.
- **Suite green at every commit:** `.venv/bin/python -m pytest -q` passes after every task's final step.
- **Conventional commits, NO AI/tool/session mentions ever** in any commit message or code comment.
- **Every new `MsgType` is registered in all three completeness guards:** (1) `tests/test_protocol.py`'s `test_msgtype_defines_no_extra_string_constants` (line 95 — the strict string-constant set); (2) `tests/test_daemon_registry.py`'s `ALL_TYPES` list (~line 108, feeds `test_all_msgtypes_registered` — every listed type must have a handler); and (3) `assert_complete(...)` in `src/sonari/daemon/__init__.py` (the import-time guard). Guards (2) and (3) demand a handler, so their entries land ALONGSIDE the handler (Tasks 6/7), never in Task 1 (empirically required — SP4 T6 lesson).
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
  - `render_slice(entries, folder) -> str` — the narrator stdin body: header line `"Slice: {N} item(s) across {T} turn(s) in {folder}."` (singular "item"/"turn" when the count is 1; folder falls back to `"this session"`), then one kind-tagged line per entry oldest-first. Kind→tag map is `{"prose": "assistant", "tool": "tool", "choice": "question", "plan": "plan", "permission": "permission"}`, unknown kinds tag as themselves.
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
    assert lines[0] == "Slice: 1 item across 1 turn in this session."


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
    header = "Slice: {0} {1} across {2} {3} in {4}.".format(
        n, "item" if n == 1 else "items",
        turns, "turn" if turns == 1 else "turns",
        folder or "this session")
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
- Modify: `tests/test_config.py:5-17` (extend the exact-set completeness guard `test_defaults_has_documented_top_level_keys` — its literal must track DEFAULTS, so the three new keys are added; this EXTENDS a completeness guard, it does not weaken one)
- Test: `tests/test_summarizer.py` (new)

**Interfaces:**
- Produces:
  - `SummarizeResult` with `.is_ok: bool`, `.text: str`, `.reason: str`; classmethods `SummarizeResult.ok(text)` / `SummarizeResult.failed(reason)` (reason ∈ `unavailable|logged_out|timeout|error` — the spoken fallback is identical for all; an empty/whitespace summary is caught downstream by the sanitizer, §Task 2).
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
    def __init__(self, out, err="", returncode=0):
        self.returncode = returncode
        self.pid = 4242
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(out)
        self.stderr = io.StringIO(err)

    def poll(self):
        return self.returncode      # already complete


class _FakePopen:
    def __init__(self, out, err="", returncode=0):
        self._out, self._err, self._rc, self.calls = out, err, returncode, []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, **kwargs})
        return _FakeProc(self._out, self._err, self._rc)


def _stream(*events):
    """Build a stream-json stdout blob (one JSON object per line)."""
    return "\n".join(json.dumps(e) for e in events)


def _assistant(*blocks):
    return {"type": "assistant", "message": {"content": list(blocks)}}


def _text(t):
    return {"type": "text", "text": t}


def _thinking(t="mulling"):
    return {"type": "thinking", "thinking": t}


def _result(subtype="success", is_error=False):
    return {"type": "result", "subtype": subtype, "is_error": is_error, "num_turns": 1}


def _ok(text="All tests passed."):
    # Real shape: a thinking block, then the clean text block, then a result event.
    return _stream(_assistant(_thinking(), _text(text)), _result())


def test_child_env_scrubs_both_api_keys_and_inherits_the_rest():
    env = {"ANTHROPIC_API_KEY": "sk-secret", "ANTHROPIC_AUTH_TOKEN": "tok",
           "PATH": "/usr/bin", "HOME": "/home/nima"}
    fake = _FakePopen(_ok())
    s = ClaudeCliSummarizer(popen=fake, which=lambda n: "/usr/bin/claude", env=env)
    s.summarize("Slice: 1 item.\nassistant: hi.", timeout_s=5)
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
    assert argv[0] == "/c" and argv[1] == "-p"   # argv[0] = the which()-resolved path
    assert argv[argv.index("--model") + 1] == "haiku"
    # stream-json + --verbose: we read the FIRST assistant text block, not .result.
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert NARRATOR_PROMPT in argv
    # Spec §6 non-negotiable #3 pinned: never resume/continue the user's live session.
    assert "--continue" not in argv and "--resume" not in argv
    assert fake.calls[0]["cwd"]                  # neutral temp cwd, not the caller's


def test_first_text_block_is_the_summary():
    out = _ok("The build is green.")
    r = ClaudeCliSummarizer(popen=_FakePopen(out),
                            which=lambda n: "/c", env={}).summarize("x", timeout_s=5)
    assert r.is_ok and r.text == "The build is green."


def test_error_max_turns_with_text_is_still_success():
    # The load-bearing case: the model produced a clean summary, then the harness
    # aborted with error_max_turns (it wanted to keep going). We ALREADY have the
    # answer -> success, ignore the error subtype and the non-zero exit.
    out = _stream(_assistant(_thinking(), _text("All 1105 tests passed.")),
                  _result(subtype="error_max_turns", is_error=True))
    r = ClaudeCliSummarizer(popen=_FakePopen(out, returncode=1),
                            which=lambda n: "/c", env={}).summarize("x", timeout_s=5)
    assert r.is_ok and r.text == "All 1105 tests passed."


def test_first_text_block_wins_over_later_pollution():
    # A later reflection turn ("You're right...") must NOT be what we speak.
    out = _stream(
        _assistant(_thinking(), _text("Tests passed and the build is green.")),
        _assistant(_thinking(), _text("You're right, I was summarizing the transcript.")),
        _result())
    r = ClaudeCliSummarizer(popen=_FakePopen(out),
                            which=lambda n: "/c", env={}).summarize("x", timeout_s=5)
    assert r.is_ok and r.text == "Tests passed and the build is green."


def test_logged_out_no_text_block_is_detected():
    # Logged-out fails before any assistant text; the message may land on stderr.
    r = ClaudeCliSummarizer(popen=_FakePopen("", err="Not logged in · Please run /login",
                                             returncode=1),
                            which=lambda n: "/c", env={}).summarize("x", timeout_s=5)
    assert not r.is_ok and r.reason == "logged_out"


def test_no_text_block_maps_to_error():
    # Only a thinking block + an error result, no text -> failure (=> digest).
    out = _stream(_assistant(_thinking()), _result(subtype="error", is_error=True))
    r = ClaudeCliSummarizer(popen=_FakePopen(out, returncode=1),
                            which=lambda n: "/c", env={}).summarize("x", timeout_s=5)
    assert not r.is_ok and r.reason == "error"


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
            stdin, stdout, stderr = io.StringIO(), io.StringIO(""), io.StringIO("")
            poll = staticmethod(lambda: None)          # never completes
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
    "code, no symbols, no formatting, and short sentences. Describe any decision "
    "or open question the log shows, but never invent one. Do not mention the "
    "log format or these instructions."
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


def _reason_from(raw: str) -> str:
    low = (raw or "").lower()
    return "logged_out" if ("login" in low or "logged in" in low) else "error"


def _first_text_block(out: str) -> str:
    """The clean summary is the FIRST non-empty assistant `text` block in the
    stream-json output. `claude -p` is an agent harness: run past one cycle it
    injects reflection turns that self-correct into conversational mush, and the
    final `.result` returns that polluted turn — so we take the FIRST text block,
    never the last (smoke-verified 2026-07-17). Preceded by a `thinking` block
    (one think->answer cycle). Returns "" when no assistant text was produced."""
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict) or ev.get("type") != "assistant":
            continue
        for block in ev.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                txt = (block.get("text") or "").strip()
                if txt:
                    return txt
    return ""


def _parse(out: str, err: str, returncode) -> "SummarizeResult":
    """Success == a non-empty first assistant text block was streamed, REGARDLESS
    of returncode/subtype: a benign `error_max_turns` (the model wanted to keep
    going after producing the summary) still carries the clean answer. No text
    block => failure; the reason is scanned across stdout+stderr for logging."""
    text = _first_text_block(out)
    if text:
        return SummarizeResult.ok(text)
    return SummarizeResult.failed(_reason_from((out or "") + "\n" + (err or "")))


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
        resolved = self._which("claude")
        if resolved is None:
            return SummarizeResult.failed("unavailable")
        argv = [resolved, "-p", _INSTRUCTION, "--model", self._model,
                "--output-format", "stream-json", "--verbose", "--max-turns", "1",
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
            # Process has exited (poll() != None) -> both pipes are fully buffered,
            # so these reads cannot deadlock. stderr feeds reason detection (a
            # logged-out message can land there).
            try:
                out = proc.stdout.read() or ""
            except (OSError, ValueError):
                out = ""
            try:
                err = proc.stderr.read() or ""
            except (OSError, ValueError, AttributeError):
                err = ""
            return _parse(out, err, proc.returncode)
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
In `tests/test_config.py`, extend the exact-set completeness guard `test_defaults_has_documented_top_level_keys` (lines 5-17) — its literal set asserts `set(DEFAULTS.keys()) ==` a fixed key list, so the three new keys MUST be added or it goes red. This EXTENDS the guard to the new keys (it stays an exact-set assertion), it does NOT weaken it:
```python
        "spearcon_voice",
        "spearcon_rate",
        "summarizer",
        "summary_voice",
        "summary_model",
    }
```

- [ ] **Step 4: Run the tests to verify they pass**
```
.venv/bin/python -m pytest -q tests/test_summarizer.py tests/test_config.py
```
Expect: all pass (11 in test_summarizer.py + every config test, including the now-extended exact-set guard `test_defaults_has_documented_top_level_keys`).

- [ ] **Step 5: Commit**
```
git add src/sonari/summarizer.py src/sonari/config.py tests/test_config.py tests/test_summarizer.py
git commit -m "feat(sp5): add host-LLM summarizer adapter with API-key env scrub"
```

### Task 5: Voice-per-utterance plumbing

**Files:**
- Modify: `src/sonari/queue.py:7-20` (SpeechItem: one new field)
- Modify: `src/sonari/speaker.py:52-97` (`speak` accepts a per-call voice override)
- Modify: `src/sonari/daemon/host.py` (`_enqueue` :231-260 threads `voice`; the FOUR `speak()` call sites at ~515-519 held branch and ~588-592 normal branch pass `voice` ONLY when the item sets one — the conditional-kwarg splat below)
- Modify: `tests/daemon_helpers.py:36-54` (FakeSpeaker records the per-call voice)
- Test: `tests/test_voice_per_utterance.py` (new)

**Interfaces:**
- Produces: `SpeechItem.voice: "str | None" = None`; `Speaker.speak(text=None, audio_path=None, cancel_epoch=None, voice=None)` — `voice` overrides `self._voice` for exactly that call, reverting after; `_enqueue(..., voice=None)` passes it onto the item. FakeSpeaker gains `self.spoken_voices: list` (one entry per `speak()`).
- **Blast-radius contract (load-bearing):** `voice` reaches the speaker ONLY for items that set it — the loop calls `speak(**{"voice": item.voice})` conditionally and calls `speak()` with NO `voice` kwarg for the None-voiced default. This is why legacy/guard test-double Speakers with voice-less signatures (`tests/test_concurrency_guards.py` ×3, `tests/test_blackbox_net.py`, `tests/test_keepgoing_preroll.py`, `tests/test_frontier.py`) are NEVER passed `voice` and stay untouched — do NOT edit those files. Only the real `Speaker` and `FakeSpeaker` gain the param.
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
In `src/sonari/speaker.py`, change the `speak` signature and the say-runner call. Append `voice` at the END (after `cancel_epoch`) so no existing positional caller shifts — every daemon call site passes `voice=`/`cancel_epoch=` by keyword anyway:
```python
    def speak(self, text=None, audio_path=None, cancel_epoch=None, voice=None) -> bool:
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
and in the `SpeechItem(...)` construction add `voice=voice,` alongside `forward=forward,`. Then at ALL FOUR `speak()` call sites in `_speak_loop_once` (held branch ~515-519 and normal branch ~588-592), pass `voice` CONDITIONALLY — build a kwarg dict that is empty unless the item carries a voice, so voice-less test-double Speakers are never handed a `voice=` they can't accept:
```python
            vkw = {"voice": item.voice} if item.voice is not None else {}
            if item.audio_path:
                completed = self.speaker.speak(
                    item.text, audio_path=item.audio_path,
                    cancel_epoch=cancel_epoch, **vkw)
            else:
                completed = self.speaker.speak(
                    item.text, cancel_epoch=cancel_epoch, **vkw)
```
(held branch uses `item.text`; the normal branch uses the attributed `text` — keep each branch's existing first argument, only add `cancel_epoch=cancel_epoch, **vkw`. Compute `vkw` once per pop, before the `if item.audio_path` split, and reuse it in both branches. The default None-voiced item calls `speak()` with NO `voice` kwarg — so guard/legacy doubles are untouched; only a catch-up body item ever passes it.)
In `tests/daemon_helpers.py`, add `self.spoken_voices: list = []` in `FakeSpeaker.__init__` and record it (mirror the appended-`voice` order):
```python
    def speak(self, text=None, audio_path=None, cancel_epoch=None, voice=None) -> bool:
        self.spoken.append(text)
        self.audio_paths.append(audio_path)
        self.spoken_voices.append(voice)
        return self.complete
```

- [ ] **Step 4: Run the focused tests, then the FULL suite**
```
.venv/bin/python -m pytest -q tests/test_voice_per_utterance.py tests/test_speaker.py
.venv/bin/python -m pytest -q
```
Expect: the 3 new tests pass; the full suite is green. The conditional-kwarg splat means voice-less Speaker doubles (`test_concurrency_guards.py`, `test_blackbox_net.py`, `test_keepgoing_preroll.py`, `test_frontier.py`) are never handed a `voice=` kwarg, so no guard/legacy test breaks — a global run confirms it (this task changed the `speak` signature; verify the whole suite, not just the two files). If ANY existing test errors with `unexpected keyword argument 'voice'`, the conditional splat was applied wrong (an item with `voice=None` must call `speak()` with no `voice` kwarg) — fix the call site, do NOT add `voice=` to the failing double.

- [ ] **Step 5: Commit**
```
git add src/sonari/queue.py src/sonari/speaker.py src/sonari/daemon/host.py tests/daemon_helpers.py tests/test_voice_per_utterance.py
git commit -m "feat(sp5): per-utterance voice override through the speak loop"
```

### Task 6: Catch-up press handler + worker thread + mailbox transport

**Files:**
- Create: `src/sonari/daemon/features/catchup.py` (new feature module — the `catch_up` handler + worker + cancel; the `catchup_result` handler is added in Task 7 in this same file)
- Modify: `src/sonari/daemon/host.py` (add `summarizer` ctor param + `_summarizer()`; the catch-up in-flight fields + `queue.Queue` inbox in `__init__`; `_drain_catchup_inbox()` called at the TOP of `_speak_loop_once`; import the new feature module)
- Modify: `src/sonari/daemon/__init__.py:11` (add `MsgType.CATCH_UP` — and close the pre-existing `MsgType.SKIP_PILE` omission — to `assert_complete`; reword the stale count comment)
- Modify: `tests/test_daemon_registry.py:108-124` (add `_MsgType.CATCH_UP` to `ALL_TYPES` — the second real completeness guard, feeding `test_all_msgtypes_registered`)
- Modify: `tests/daemon_helpers.py` (add `FakeSummarizer`; `make_daemon(..., summarizer=None)`)
- Test: `tests/test_catchup_press.py` (new)

**Interfaces:**
- Consumes: `select_summarizer`/`SummarizeResult` (Task 4), `render_slice`/`build_digest` (Task 3), `MsgType.CATCH_UP`/`CATCHUP_RESULT` (Task 1), `_enqueue(..., voice=)` (Task 5).
- Produces (daemon state, mutated ONLY on the loop): `host._catchup` — the in-flight bundle `{"id", "target", "folder", "slice_end", "digest", "cancel": threading.Event, "phase": "preparing"|"rendering", "render_id": int|None, "ended": bool, "ack_id": int|None}` or `None`; `host._catchup_seq: int`; `host._catchup_inbox: queue.Queue`; `host._summarizer() -> HostSummarizer | None`; `host._drain_catchup_inbox()`. `ack_id` is the id `_enqueue` returned for the ack item (the ordering anchor — Task 7's render inserts after it). The bundle survives SESSION_END (daemon state, not sessions/history) — so the Task 7 result handler can render `"{folder} ended."` from it.
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


def test_render_never_precedes_its_ack_default_digest():
    # No adapter (make_daemon's default summarizer=None): the failure result is
    # mailed SYNCHRONOUSLY at press, so the digest render is drainable on the very
    # next loop tick. It MUST still land AFTER the ack, never ahead of it — the
    # ground-truth magnitude always speaks first (the ack->summary contract).
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=None)
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "a.")
    daemon.handle_message(_catch_up())
    for _ in range(4):
        daemon._speak_loop_once()
    ack = "Catching up 1 item in r."
    digest = "Summary unavailable. Last: a."
    assert ack in speaker.spoken and digest in speaker.spoken
    assert speaker.spoken.index(ack) < speaker.spoken.index(digest)


def test_render_lands_after_ack_when_ack_queued_behind_a_busy_item():
    # A busy utterance is already queued; the ack is enqueued at_front (ahead of
    # it), then the digest result arrives while the ack is still queued ->
    # insert_after keeps the render immediately behind the ack, never ahead of it.
    daemon, queue, speaker, sessions, config = make_daemon(summarizer=None)
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "a.")
    daemon._enqueue("fg", "prose", "Busy line.", False)   # already queued ahead
    daemon.handle_message(_catch_up())
    for _ in range(5):
        daemon._speak_loop_once()
    ack = "Catching up 1 item in r."
    digest = "Summary unavailable. Last: a."
    assert speaker.spoken.index(ack) < speaker.spoken.index(digest)


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
        _speak_loop_once BEFORE the held-branch return so results land in all states.
        This position guards STATE-DELIVERY (results reach the loop while held), NOT
        ordering: ack-before-render is guaranteed by on_catchup_result inserting the
        render after the ack's queued id (Task 7), independent of when this drains."""
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
    # No-folder fallback = "this session" (the target IS the workspace the user sits
    # at — never "another session"; matches render_slice's fallback). Owner ear-pass
    # veto string, like every other spoken string here.
    where = "in {0}".format(folder) if folder else "in this session"
    ack = "Catching up {0} {1} {2}.".format(n, "item" if n == 1 else "items", where)
    if aged_out:
        ack = "Earlier output aged out. " + ack
    ack_id = host._enqueue(dest, "prose", ack, False,
                           mute_exempt=True, pause_exempt=True, at_front=True)
    last = entries[-1]
    slice_text = render_slice(entries, folder)      # pinned + rendered AT PRESS
    host._catchup_seq += 1
    request_id = host._catchup_seq
    cancel = threading.Event()
    # `ack_id` lets on_catchup_result land the render RIGHT AFTER the still-queued
    # ack (never ahead of it), so the ground-truth magnitude always speaks first.
    host._catchup = {"id": request_id, "target": target, "folder": folder,
                     "slice_end": (last.msg_id, last.seq),
                     "digest": build_digest(entries), "cancel": cancel,
                     "phase": "preparing", "render_id": None, "ended": False,
                     "ack_id": ack_id}
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
In `src/sonari/daemon/__init__.py`, add `MsgType.CATCH_UP,` to the `assert_complete([...])` list (its handler now exists; do NOT add `CATCHUP_RESULT` yet — Task 7). While here, also insert `MsgType.SKIP_PILE,` immediately after it — a pre-existing SP4 omission (its handler is live at `playback.py:31`, so this is safe to close now), and reword the stale count comment above the list (`# ... we enumerate all 35 known keys explicitly.`) to name no count — e.g. `# ... we enumerate every known key explicitly.` The tail of the list becomes:
```python
    MsgType.REPEAT_LAST,
    MsgType.SKIP_PILE,     # close the pre-existing SP4 omission (handler at playback.py:31)
    MsgType.CATCH_UP,
])
```
In `tests/test_daemon_registry.py`, add `_MsgType.CATCH_UP,` to the `ALL_TYPES` list right after `_MsgType.SKIP_PILE,` (the second real completeness guard — `test_all_msgtypes_registered` now demands CATCH_UP have a handler, which it does):
```python
    _MsgType.SKIP_PILE,
    _MsgType.CATCH_UP,
]
```
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
.venv/bin/python -m pytest -q tests/test_catchup_press.py tests/test_protocol.py tests/test_daemon_registry.py
```
Expect: the 8 press tests pass; the import-time `assert_complete` guard passes (CATCH_UP + the newly-closed SKIP_PILE both have handlers); `test_all_msgtypes_registered` passes with CATCH_UP now in `ALL_TYPES`.

- [ ] **Step 5: Commit**
```
git add src/sonari/daemon/features/catchup.py src/sonari/daemon/host.py src/sonari/daemon/__init__.py tests/test_daemon_registry.py tests/daemon_helpers.py tests/test_catchup_press.py
git commit -m "feat(sp5): catch-up press handler, worker thread, and result mailbox"
```

### Task 7: `catchup_result` render + landing

**Files:**
- Modify: `src/sonari/queue.py` (SpeechItem: `render_id` + `catchup_burn` fields; `SpeechQueue.insert_after(item_id, items)` — lands the render after the ack)
- Modify: `src/sonari/daemon/host.py` (`_enqueue` threads `render_id`/`catchup_burn` + gains an `after_id` param; `_voices_provider` attr + `_installed_voices()`)
- Modify: `src/sonari/catchup.py` (append `resolve_summary_voice`)
- Modify: `tests/test_queue.py` (unit test for `insert_after`)
- Modify: `src/sonari/daemon/features/catchup.py` (add the `on_catchup_result` handler)
- Modify: `src/sonari/daemon/__init__.py` (add `MsgType.CATCHUP_RESULT` to `assert_complete`)
- Modify: `tests/test_daemon_registry.py` (add `_MsgType.CATCHUP_RESULT` to `ALL_TYPES` — the second real completeness guard, alongside its handler landing)
- Modify: `tests/daemon_helpers.py` (`make_daemon` sets `daemon._voices_provider = lambda: []` for hermetic renders)
- Test: `tests/test_catchup_render.py` (new)

**Interfaces:**
- Consumes: `sanitize_summary` (Task 2), `build_digest` (Task 3), `_has_decision` from `features/control.py`, `resolve_summary_voice` (this task), `_enqueue(..., voice=, render_id=, catchup_burn=, after_id=)`, `cu["ack_id"]` (Task 6).
- Produces: `SpeechItem.render_id: int|None`, `SpeechItem.catchup_burn: bool=False`; `SpeechQueue.insert_after(item_id, items) -> bool` (inserts `items` right after the queued item with id `item_id`; returns False when that item is no longer queued — the caller falls back to `at_front`; caller holds the daemon lock); `_enqueue(..., after_id=None)` (when `after_id` is set and still queued, insert after it; otherwise honor `at_front`); `resolve_summary_voice(cfg_value, main_voice, voices) -> str|None` (concrete name wins; `auto` picks the first voice `!= main_voice`, else main voice; `off`/`None` → main voice); `host._installed_voices() -> list[str]`. The result handler id-matches `msg["request_id"]` against `host._catchup["id"]` (stale → drop), assembles `[ "{folder} ended." (if ended) ] + [ frame+body | digest ] + [ tail if decision & not ended ]`, routes to `_cue_dest`, and enqueues the segments REVERSED with `after_id=cu.get("ack_id")` so — when the ack is still queued — the whole render lands immediately AFTER the ack in play order (never ahead of it); when the ack has already been spoken, `insert_after` returns False and the reversed enqueue falls to `at_front` as before. It marks the LAST item `catchup_burn=True` ALWAYS (it is the render-done marker that clears `self._catchup` on completion; Task 8 gates the actual frontier burn on `not cu["ended"]`, so an ended render still clears the bundle). Task 8 consumes `render_id`/`catchup_burn` in `note_spoken`.

- [ ] **Step 1a: Write the failing `insert_after` unit test** — append to `tests/test_queue.py` (idiom: `_item(id, ...)` helper already defined at the top of that file):
```python
def test_insert_after_lands_items_right_behind_the_anchor():
    q = SpeechQueue()
    q.enqueue(_item(1, text="ack"))
    q.enqueue(_item(2, text="tail"))
    assert q.insert_after(1, [_item(3, text="render")]) is True
    assert [q.pop_next().text for _ in range(3)] == ["ack", "render", "tail"]


def test_insert_after_returns_false_when_anchor_absent():
    q = SpeechQueue()
    q.enqueue(_item(1, text="only"))
    assert q.insert_after(999, [_item(2, text="render")]) is False
    assert len(q) == 1 and q.pop_next().text == "only"   # nothing inserted
```

- [ ] **Step 1b: Write the failing render tests** — `tests/test_catchup_render.py`:
```python
import threading

from sonari.catchup import resolve_summary_voice
from tests.daemon_helpers import make_daemon, stream_queue


def _result(rid, ok, text="", reason=""):
    return {"v": 1, "type": "catchup_result", "request_id": rid,
            "ok": ok, "text": text, "reason": reason}


def _inflight(daemon, target="fg", folder="myrepo",
              digest="Summary unavailable. Last: x."):
    daemon._catchup = {"id": 1, "target": target, "folder": folder,
                       "slice_end": (0, 0), "digest": digest,
                       "cancel": threading.Event(), "phase": "preparing",
                       "render_id": None, "ended": False, "ack_id": None}
    return 1


def _catch_up(session="fg"):
    return {"v": 1, "type": "catch_up", "session": session}


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
.venv/bin/python -m pytest -q tests/test_queue.py tests/test_catchup_render.py
```
Expect: the two `insert_after` tests fail with `AttributeError: 'SpeechQueue' object has no attribute 'insert_after'`; `test_catchup_render.py` fails with `ImportError: cannot import name 'resolve_summary_voice'` (and, once that exists, the render assertions fail — no `catchup_result` handler yet).

- [ ] **Step 3: Write the minimal implementation**

In `src/sonari/queue.py`, add to `SpeechItem` after `voice`:
```python
    voice: "str | None" = None  # SP5: per-utterance say voice override (the summary body); None == main voice
    render_id: "int | None" = None  # SP5: groups a catch-up render's frame/body/tail items
    catchup_burn: bool = False  # SP5: True on the render's LAST item; note_spoken burns on its completion
```
Add to `SpeechQueue` (the render must land right after its ack, never ahead of it):
```python
    def insert_after(self, item_id, items) -> bool:
        """Insert *items* (in order) immediately after the queued item with id
        *item_id*. Returns False when that item is no longer queued (already
        spoken/dropped) so the caller can fall back to at_front. The catch-up
        render lands after its still-queued ack this way. Caller holds the lock."""
        for i, it in enumerate(self._items):
            if it.id == item_id:
                for offset, new in enumerate(items, start=1):
                    self._items.insert(i + offset, new)
                return True
        return False
```
In `src/sonari/daemon/host.py`, extend `_enqueue`'s signature with `render_id=None, catchup_burn=False` and pass both to `SpeechItem(...)`. Also add an `after_id=None` param: when it is set and the target item is still queued, insert the new item right after it; otherwise fall through to the existing `at_front`/append placement. Concretely, replace the placement block with:
```python
        if after_id is not None and st.queue.insert_after(after_id, [item]):
            pass                                     # landed right after the anchor (the ack)
        elif at_front:
            st.queue.enqueue_front(item)
        else:
            evicted = st.queue.enqueue(item)
            if evicted is not None:
                self._drop_pending([evicted])
```
(Within one handler dispatch the anchor's presence is invariant — the loop can't pop between the reversed inserts under the same lock — so a per-item `after_id` stays consistent across the whole render: all insert-after, or all fall to `at_front`.) Add `self._voices_provider = None` and `self._voices_cache = None` in `__init__` (beside the catch-up fields) and a method:
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
    ack_id = cu.get("ack_id")                        # land the render AFTER the still-queued ack
    for i in range(last, -1, -1):                    # reverse -> preserved play order (after the ack, else at_front)
        text, voice = segments[i]
        # The last item is the render-DONE marker (always) — it clears self._catchup
        # on completion; whether it also BURNS is gated on `not ended` in Task 8, so
        # an ended render still clears the bundle (no spurious "Cancelled." next press).
        host._enqueue(dest, "prose", text, False, mute_exempt=True, pause_exempt=True,
                      at_front=True, voice=voice, render_id=render_id,
                      catchup_burn=(i == last), after_id=ack_id)
    return None
```
In `src/sonari/daemon/__init__.py`, add `MsgType.CATCHUP_RESULT,` to `assert_complete([...])` (its handler now exists).
In `tests/test_daemon_registry.py`, add `_MsgType.CATCHUP_RESULT,` to `ALL_TYPES` right after `_MsgType.CATCH_UP,` (its handler now exists — `test_all_msgtypes_registered` stays green):
```python
    _MsgType.CATCH_UP,
    _MsgType.CATCHUP_RESULT,
]
```
In `tests/daemon_helpers.py`, in `make_daemon`, after constructing `daemon`, add `daemon._voices_provider = lambda: []` (hermetic renders: `auto` → main voice).

- [ ] **Step 4: Run the tests to verify they pass**
```
.venv/bin/python -m pytest -q tests/test_queue.py tests/test_catchup_render.py tests/test_catchup_press.py tests/test_daemon_registry.py
```
Expect: the two `insert_after` unit tests + all render + press tests pass; `assert_complete` still green (both types now have handlers); `test_all_msgtypes_registered` green with CATCHUP_RESULT now in `ALL_TYPES`.

- [ ] **Step 5: Commit**
```
git add src/sonari/queue.py src/sonari/daemon/host.py src/sonari/catchup.py src/sonari/daemon/features/catchup.py src/sonari/daemon/__init__.py tests/test_daemon_registry.py tests/test_queue.py tests/daemon_helpers.py tests/test_catchup_render.py
git commit -m "feat(sp5): render catch-up summary with frame, distinct-voice body, and tail"
```

### Task 8: Burn-on-completion + cut semantics in `note_spoken`

**Files:**
- Modify: `src/sonari/queue.py` (add `SpeechQueue.remove_where(pred)`)
- Modify: `src/sonari/daemon/host.py` (`note_spoken` render block; `_drop_render_items`/`_burn_catchup` helpers)
- Modify: `src/sonari/daemon/features/catchup.py` (rewrite `_cancel_catchup` to handle the rendering phase)
- Modify: `src/sonari/daemon/features/control.py:347-351` (`on_where_am_i` re-queue SKIPS a render item — a cut render must not leave an orphan fragment)
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
                       "render_id": None, "ended": False, "ack_id": None}


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


def test_failure_digest_render_burns_to_slice_end():
    # On an adapter-less/failed host the digest IS the only render, so it MUST
    # burn or catch-up would never clear the pile there (owner-flagged, kept).
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    e1 = daemon.history.record("fg", "prose", "b.")
    _inflight(daemon, slice_end=(e1.msg_id, e1.seq))
    daemon.handle_message(_result(1, ok=False, reason="timeout"))
    _drain(daemon)
    assert daemon._stream("fg").frontier == (e1.msg_id, e1.seq)   # digest burns too
    assert daemon._catchup is None


def test_ended_render_completion_clears_bundle_so_next_press_starts_fresh():
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("live", cwd="/x/live")   # a live session to voice on
    daemon._catchup = {"id": 1, "target": "gone", "folder": "oldrepo",
                       "slice_end": (0, 0), "digest": "Summary unavailable. Last: x.",
                       "cancel": threading.Event(), "phase": "preparing",
                       "render_id": None, "ended": False, "ack_id": None}
    daemon.handle_message(_result(1, ok=True, text="It finished."))   # 'gone' unregistered -> ended
    _drain(daemon)
    assert daemon._catchup is None                    # ended render CLEARED the bundle
    daemon.history.record("live", "prose", "new output.")
    daemon.handle_message(_catch_up("live"))          # a fresh press must START, not cancel
    _drain(daemon)
    assert "Cancelled." not in speaker.spoken
    assert any("Catching up" in s for s in speaker.spoken)


def test_where_am_i_barge_in_mid_render_leaves_no_orphan_fragment():
    # A ⌃⌘W landing mid-body cuts the render (note_spoken's non-completion branch:
    # no burn, siblings dropped) AND on_where_am_i must NOT re-queue the cut render
    # item — a re-queued body would replay frame-less after the readout with no burn.
    daemon, queue, speaker, sessions, config = make_daemon()
    sessions.set_foreground("fg", cwd="/x/r")
    daemon.history.record("fg", "prose", "a.")
    _inflight(daemon, slice_end=(0, 0))
    daemon.handle_message(_result(1, ok=True, text="Body one. Body two."))
    daemon._speak_loop_once()          # frame plays
    speaker.complete = False
    daemon.handle_message({"v": 1, "type": "where_am_i", "session": "fg"})  # barge-in mid-body
    speaker.complete = True
    _drain(daemon, 4)
    # The readout spoke; the body was cut and NOT replayed as an orphan; no burn.
    assert not any(s in ("Body one. Body two.", "Body one.", "Body two.")
                   for s in speaker.spoken)
    assert daemon._catchup is None
    assert daemon._stream("fg").frontier is None
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
                    # The render finished. Burn UNLESS the session ended mid-prep
                    # (nothing left to burn) — but ALWAYS clear the bundle, so an
                    # ended render never strands _catchup (a spurious next-press cancel).
                    if not cu.get("ended"):
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
In `src/sonari/daemon/features/control.py`, the `on_where_am_i` barge-in re-queues the interrupted item at_front (lines ~347-351). A cut catch-up render must NOT be re-queued — its `note_spoken` non-completion branch already dropped the siblings and cleared `_catchup`; re-queuing the lone body would replay it frame-less with no burn. Guard the re-queue on `render_id`:
```python
    # Resume-after-interjection: re-queue the interrupted item FIRST so it ends up
    # DEEPEST (the status cue is appendleft'd in front of it below). A catch-up
    # render item (render_id set) is NEVER re-queued — a cut render is gone by
    # design (note_spoken dropped its siblings + cleared _catchup); the pile stays
    # unburned and the next press re-summarizes (§2.8).
    if cur is not None and getattr(cur, "render_id", None) is None:
        host._enqueue(cur.session, cur.kind, cur.text, cur.is_decision,
                      entry=entry, mute_exempt=cur.mute_exempt,
                      pause_exempt=cur.pause_exempt, names_session=cur.names_session,
                      audio_path=cur.audio_path, forward=cur.forward, at_front=True)
```
(This replaces the existing `if cur is not None:` re-queue block — only the guard condition changes from `cur is not None` to `cur is not None and getattr(cur, "render_id", None) is None`; the enqueue body is unchanged.)

- [ ] **Step 4: Run the catch-up tests, then the FULL suite**
```
.venv/bin/python -m pytest -q tests/test_catchup_burn.py tests/test_catchup_render.py tests/test_catchup_press.py
.venv/bin/python -m pytest -q
```
Expect: all catch-up runtime tests pass, AND the full suite is green (this task edits `note_spoken` on the hot path — verify globally).

- [ ] **Step 5: Commit**
```
git add src/sonari/queue.py src/sonari/daemon/host.py src/sonari/daemon/features/catchup.py src/sonari/daemon/features/control.py tests/test_catchup_burn.py
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
    # Floored to 0 -> the unheard clause is suppressed entirely (never "0 unheard",
    # never "-1 unheard"). Strict: the word must not appear at all in this fixture.
    assert "unheard" not in speaker.spoken[-1]
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
The new tests pass. For every existing ⌃⌘W test that now fails: the failure must be a pure NUMBER change (the `u` count rising to the true pile magnitude for a browsed/multi-turn/queued pile). Compute the true pile: `len(unheard_from_frontier(session, frontier)) − len(queue)`, floored at 0, and correct the expected count in the assertion. **Do NOT weaken any assertion** (no `==`→`in`, no dropping the negative-substring checks); `{k} waiting`, the `stale` word, and clause order are unchanged. If a failure is anything OTHER than a corrected `u` number (e.g. a clause reordered, `stale` flipped, `waiting` changed), STOP and flag it — that is out of scope for this task. Finish with a full-suite run:
```
.venv/bin/python -m pytest -q
```
Expect: green (all ⌃⌘W tests reconciled to the pile-based count).

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
- Modify: `README.md:14` (one quota-honesty sentence where catch-up is described — spec §6)
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
- In `README.md`, the catch-up hotkey is listed at line 14 (`- **Global hotkeys** — stop, repeat, skip, jump-to-decision, catch-up, rate, verbosity, re-read …`). Add ONE quota-honesty sentence to the feature area so the subscription draw is disclosed (spec §6). Append after that bullet: `  - **Catch-up** summarizes a session via your own logged-in coding-agent CLI (no separate API key). It draws from that subscription's usage — roughly 16–32k tokens a press, far cheaper on repeats within the hour — and falls back to a plain last-line digest when the summary is unavailable.`
- In `.superpowers/sdd/progress.md`, append a dated line: `2026-07-17 — SP5 catch-up (async host-LLM summary + burn + count unification) built; spec docs/superpowers/specs/2026-07-17-sonari-sp5-catchup-design.md; supersedes the verbatim catch-up model in 2026-06-29-sonari-voice-arbitration-design.md §8/§9/§10.1/D17.`

- [ ] **Step 4: Re-run the completeness grep (the gate)**
```
grep -n "Reads forward from your frontier through the pile to live" docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md
grep -in "catch-up\|catch_up" docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md
```
Expect: the literal "Reads forward…" phrase is GONE (rewritten). Every remaining `catch-up`/`catch_up` hit is a historical/neutral mention the §10 table permits (the verb name in state-machine rows, the "catch-up key" gesture label, cross-refs) — NONE still describes a verbatim forward-read. If any surviving hit still asserts the old model, rewrite it before committing.

- [ ] **Step 5: Commit**
```
git add docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-design.md docs/superpowers/specs/2026-06-29-sonari-voice-arbitration-reconciliation.md docs/superpowers/specs/2026-07-16-sonari-whereami-grammar-v2.md README.md .superpowers/sdd/progress.md
git commit -m "docs(sp5): rewrite the stale verbatim catch-up model to the summary verb"
```

### Task 11: Final verification + plan totals

**Files:** none (verification only; if a totals note is recorded, append it to `.superpowers/sdd/progress.md`).

**Interfaces:** none.

- [ ] **Step 1: Run the full suite**
```
.venv/bin/python -m pytest -q
```
Expect: all pass, 1 skipped. Baseline before SP5 was **1105 passed / 1 skipped**; this plan adds ~58 new tests (T1 keymap +1, T2 sanitizer +5, T3 slice/digest +5, T4 summarizer +11 [stream-json first-text-block extraction, incl. the error_max_turns-is-success + first-block-wins-over-pollution cases from the 2026-07-17 smoke fix], T5 voice +3, T6 press +10 [+2 ack-before-render ordering, fix wave F3], T7 render +9 incl. +2 `insert_after` unit tests in test_queue.py, T8 burn +9 [+1 W-barge-in orphan guard, F6.9], T9 counts +2) plus in-place updates to existing ⌃⌘W tests (count corrections, not new tests), so the target is roughly **~1163 passed / 1 skipped**. These per-task counts are estimates — record the EXACT final number here after the run.

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
