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

### Task 4: HostSummarizer protocol + ClaudeCliSummarizer adapter + config keys

### Task 5: Voice-per-utterance plumbing

### Task 6: Catch-up press handler + worker thread + mailbox transport

### Task 7: `catchup_result` render + landing

### Task 8: Burn-on-completion + cut semantics in `note_spoken`

### Task 9: ⌃⌘W count-semantics unification (the 14-vs-2 seam)

### Task 10: Spec-hygiene rewrite + changelog

### Task 11: Final verification + plan totals
