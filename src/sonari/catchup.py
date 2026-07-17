"""Pure catch-up text helpers (SP5): sanitize LLM output, render the transcript
slice for the narrator, and build the deterministic digest floor. No daemon
imports — safe to unit-test in isolation and to call from the worker thread."""
from __future__ import annotations

import re

_LIST_MARKER = re.compile(r"(?m)^[ \t]*(?:[-+*]|\d+\.)[ \t]+")
_FENCE = re.compile(r"`{3,}")
_HEADING = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+")
# Strip markdown DECORATION only, never bare [*_#] wherever they appear: a
# blanket strip silently rewrites spoken facts (my_file_name.py -> myfilename.py,
# issue #123 -> issue 123). Free-standing runs need 3+ marks to count as a
# divider (***): a lone * or # between spaces is an operator/symbol in coding
# prose (2 * 3, O(n * m), SELECT * FROM, the # character) and must survive.
_BARE_MARK_RUN = re.compile(r"(?:(?<=\s)|^)[*_#]{3,}(?=\s|$)")
# Asterisk-only paired emphasis. Underscore is NEVER an emphasis delimiter here:
# a whitespace-delimited __dunder__ or _sunder_ identifier is syntactically
# identical to underscore-bold/italic, and stripping it rewrites a fact
# (__init__ -> init) while a stray _italic_ left intact costs only a silent
# character in speech. The * in both guards stops matches starting mid-run.
_PAIRED_EMPHASIS = re.compile(
    r"(?<![A-Za-z0-9*])(\*{1,3})(?=\S)(.+?)(?<=\S)\1(?![A-Za-z0-9*])")
_WHITESPACE = re.compile(r"\s+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def sanitize_summary(text: str, ceiling: int = 8) -> str:
    """Speech-safe body text from whatever the model returned. Strip markdown,
    collapse whitespace to single spaces, split into sentences, clamp to
    *ceiling*. Returns '' when nothing survives (caller falls to the digest)."""
    if not text:
        return ""
    s = _LIST_MARKER.sub("", text)          # leading bullets/numbers, per line
    s = _HEADING.sub("", s)                  # line-start # heading marks
    s = _FENCE.sub(" ", s)                   # ``` fences
    s = s.replace("`", "")                   # inline code ticks
    s = _BARE_MARK_RUN.sub("", s)            # free-standing *** ___ ### runs
    s = _PAIRED_EMPHASIS.sub(r"\2", s)       # *italic* / **bold** delimiters...
    s = _PAIRED_EMPHASIS.sub(r"\2", s)       # ...twice for nested **bold *it***
    s = _WHITESPACE.sub(" ", s).strip()      # newlines/runs -> single spaces
    if not s:
        return ""
    sentences = [p for p in _SENTENCE_SPLIT.split(s) if p.strip()]
    return " ".join(sentences[:ceiling])


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
