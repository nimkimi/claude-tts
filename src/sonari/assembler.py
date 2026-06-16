"""Assemble streamed text deltas into complete, speakable chunks.

PURE: no I/O. Splits prose into sentences and replaces triple-backtick
fenced code blocks with a spoken one-line summary.
"""
from __future__ import annotations

import re

from sonari.cleaner import clean_markdown

_FENCE = "```"
# a complete sentence ends at . ! or ? followed by whitespace or end-of-string
_SENTENCE = re.compile(r"(.+?[.!?])(?:\s+|$)", flags=re.DOTALL)

# A paragraph boundary = a blank line. We mark it with a private-use sentinel
# BEFORE clean_markdown collapses whitespace (which would otherwise erase it),
# then split on the sentinel after cleaning. feed() yields PARAGRAPH_BREAK between
# paragraphs so the daemon can group history by paragraph (the nav 'item' unit).
_PARA = re.compile(r"\n[ \t]*\n")
_SENTINEL = ""

# Sentinel object emitted in the feed() output stream between paragraphs.
PARAGRAPH_BREAK = object()


class ProseAssembler:
    def __init__(self) -> None:
        self._seen: set[int] = set()
        self._buf = ""                 # pending prose text (outside fences)
        self._pending = ""             # raw tail not yet split into a line/fence token
        self._in_fence = False
        self._fence_lang = ""
        self._fence_lines: list[str] = []
        self._fence_opened_line = False  # have we consumed the opening info-string line?

    def feed(self, delta: str, index: int, final: bool) -> list[str]:
        out: list[str] = []
        if index in self._seen:
            # still honor a final flush even on a duplicate index
            if final:
                out.extend(self._flush_prose())
                self._reset()
            return out
        self._seen.add(index)

        self._pending += delta
        out.extend(self._consume())

        if final:
            out.extend(self._consume(force=True))
            out.extend(self._flush_prose())
            self._reset()
        return out

    def _consume(self, force: bool = False) -> list[str]:
        """Scan _pending for fence boundaries, routing text to prose or fence.

        Only acts on text we can resolve: a fence marker, or (inside a fence)
        a complete line. Leftover ambiguous tail stays in _pending unless force.
        """
        out: list[str] = []
        while True:
            if self._in_fence:
                nl = self._pending.find("\n")
                close = self._pending.find(_FENCE)
                # closing fence comes before the next newline (or no newline)
                if close != -1 and (nl == -1 or close < nl):
                    # everything before the closing fence on this line is content
                    # (already-collected lines handle full lines; trailing inline
                    # content before ``` is rare, treat remainder as a line if any)
                    pre = self._pending[:close]
                    if pre.strip():
                        self._fence_lines.append(pre)
                    self._pending = self._pending[close + len(_FENCE):]
                    out.append(self._close_fence())
                    continue
                if nl != -1:
                    line = self._pending[:nl]
                    self._pending = self._pending[nl + 1:]
                    if not self._fence_opened_line:
                        # first line after opening ``` is the info string
                        self._fence_lang = line.strip()
                        self._fence_opened_line = True
                    else:
                        self._fence_lines.append(line)
                    continue
                # no newline and no closing fence yet
                if force:
                    # unterminated fence at EOF: flush what we have
                    out.append(self._close_fence())
                break
            else:
                open_at = self._pending.find(_FENCE)
                if open_at != -1:
                    prose = self._pending[:open_at]
                    self._buf += prose
                    self._pending = self._pending[open_at + len(_FENCE):]
                    out.extend(self._split_sentences())
                    self._in_fence = True
                    self._fence_opened_line = False
                    self._fence_lang = ""
                    self._fence_lines = []
                    continue
                # no fence opening visible
                if force:
                    self._buf += self._pending
                    self._pending = ""
                    out.extend(self._split_sentences())
                else:
                    # hold back only enough tail to detect a future "```";
                    # commit everything except a possible partial fence marker
                    keep = self._partial_fence_tail_len()
                    if keep:
                        commit = self._pending[:-keep]
                        self._pending = self._pending[-keep:]
                    else:
                        commit = self._pending
                        self._pending = ""
                    if commit:
                        self._buf += commit
                        out.extend(self._split_sentences())
                break
        return out

    def _partial_fence_tail_len(self) -> int:
        """How many trailing chars of _pending could be the start of a fence."""
        for n in (2, 1):
            if self._pending.endswith("`" * n):
                return n
        return 0

    def _close_fence(self) -> str:
        n = len(self._fence_lines)
        lang = self._fence_lang
        self._in_fence = False
        self._fence_opened_line = False
        self._fence_lang = ""
        self._fence_lines = []
        if lang:
            return f"{n}-line {lang} code block"
        return f"{n}-line code block"

    def _sentences_of(self, text: str, keep_remainder: bool):
        """Split *text* into sentences. Return (sentences, remainder). When
        keep_remainder is False the trailing fragment is emitted too (a complete
        paragraph) and remainder is ''."""
        out: list = []
        last_end = 0
        for m in _SENTENCE.finditer(text):
            sentence = m.group(1).strip()
            if len(sentence) > 1:
                out.append(sentence)
            last_end = m.end()
        remainder = text[last_end:]
        if not keep_remainder:
            tail = remainder.strip()
            if len(tail) > 1:
                out.append(tail)
            remainder = ""
        return out, remainder

    def _split_sentences(self) -> list:
        """Emit complete sentences from _buf, with PARAGRAPH_BREAK markers between
        paragraphs (blank-line boundaries). Keeps the trailing partial sentence."""
        out: list = []
        # Mark paragraph breaks BEFORE cleaning (the whitespace collapse would
        # otherwise erase them), then split on the surviving sentinel.
        cleaned = clean_markdown(_PARA.sub(_SENTINEL, self._buf))
        if not cleaned:
            self._buf = ""
            return out
        segments = cleaned.split(_SENTINEL)
        for seg in segments[:-1]:                       # complete paragraphs
            sents, _ = self._sentences_of(seg, keep_remainder=False)
            out.extend(sents)
            out.append(PARAGRAPH_BREAK)
        # the last segment is the current, possibly-incomplete paragraph
        sents, self._buf = self._sentences_of(segments[-1], keep_remainder=True)
        out.extend(sents)
        return out

    def _flush_prose(self) -> list[str]:
        if not self._buf:
            return []
        cleaned = clean_markdown(self._buf)
        self._buf = ""
        if len(cleaned) > 1:
            return [cleaned]
        return []

    def _reset(self) -> None:
        self._seen = set()
        self._buf = ""
        self._pending = ""
        self._in_fence = False
        self._fence_lang = ""
        self._fence_lines = []
        self._fence_opened_line = False
