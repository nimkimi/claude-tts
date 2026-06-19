"""Per-session narration history + sentence-granular heard-marker.

PURE: no I/O. Records every narrated sentence per session, grouped by message
and by TURN (one turn per user prompt). `heard` flips True only when the speak
loop confirms the utterance COMPLETED, so an interrupted sentence stays unheard.
Powers within-turn nav (next/prev/first/last) and the persistent cross-turn
transcript (Stage 4); SESSION_END clears it, a new prompt only opens a new turn.
"""
from __future__ import annotations

from collections import deque


class HistoryEntry:
    __slots__ = ("text", "kind", "msg_id", "seq", "turn_id", "heard")

    def __init__(self, text: str, kind: str, msg_id: int, seq: int = 0,
                 turn_id: int = 0) -> None:
        self.text = text
        self.kind = kind          # prose|choice|plan|permission
        self.msg_id = msg_id      # message group; bumped by end_message()/start_turn()
        self.seq = seq            # 0-based index within the group; seq 0 == its head
        self.turn_id = turn_id    # turn group; bumped by start_turn() (a new prompt)
        self.heard = False


class SessionHistory:
    def __init__(self, cap: int = 200) -> None:
        self._cap = cap
        self._entries: "dict[str, deque]" = {}
        self._msg_id: "dict[str, int]" = {}
        self._group_seq: "dict[str, int]" = {}   # next entry index within the open group
        self._turn_id: "dict[str, int]" = {}     # current turn per session (a new prompt bumps it)
        self._touch: "dict[str, int]" = {}   # recency across sessions
        self._tick = 0

    def record(self, session: str, kind: str, text: str) -> HistoryEntry:
        d = self._entries.get(session)
        if d is None:
            d = deque(maxlen=self._cap)
            self._entries[session] = d
        seq = self._group_seq.get(session, 0)
        entry = HistoryEntry(text, kind, self._msg_id.get(session, 0), seq,
                             self._turn_id.get(session, 0))
        self._group_seq[session] = seq + 1
        d.append(entry)
        self._tick += 1
        self._touch[session] = self._tick
        return entry

    def end_message(self, session: str) -> None:
        """Close the current message group (the assembler's final boundary)."""
        self._msg_id[session] = self._msg_id.get(session, 0) + 1
        self._group_seq[session] = 0          # the next group starts at the head

    def start_turn(self, session: str) -> None:
        """Open a new turn (a new user prompt). Subsequent entries belong to the
        new turn, and a fresh message group is started so the new turn never
        continues the prior turn's still-open group. Unlike reset(), the prior
        turn's entries are KEPT — the transcript persists across turns (Stage 4);
        only SESSION_END drops it."""
        self._turn_id[session] = self._turn_id.get(session, 0) + 1
        self._msg_id[session] = self._msg_id.get(session, 0) + 1
        self._group_seq[session] = 0

    def last_message(self, session: str) -> list:
        """All entries of the most recent message group (the 'whole last
        message'), oldest first."""
        d = self._entries.get(session)
        if not d:
            return []
        last_id = d[-1].msg_id
        return [e for e in d if e.msg_id == last_id]

    def message_ids(self, session: str) -> list:
        """Distinct message ids for the session, oldest first. Each id is one
        'item' (one assistant message) within the current turn; the list is the
        current turn's messages (history resets on each new prompt). Powers the
        next/prev/first/last navigation cursor."""
        d = self._entries.get(session)
        if not d:
            return []
        ids = []
        seen = set()
        for e in d:
            if e.msg_id in seen:
                continue
            seen.add(e.msg_id)
            # The first PRESENT entry of a group. If its seq != 0 the group's head
            # was evicted by the rolling cap, so the group is truncated — exclude it
            # from navigation rather than letting nav replay a fragment (#8).
            if e.seq == 0:
                ids.append(e.msg_id)
        return ids

    def entries_for_message(self, session: str, msg_id: int) -> list:
        """All entries of a given message id, oldest first."""
        d = self._entries.get(session)
        if not d:
            return []
        return [e for e in d if e.msg_id == msg_id]

    def nth_last_message(self, session: str, n: int) -> list:
        """Entries of the n-th most recent message group, oldest first.
        n=0 is the current/most-recent message (== last_message); n=1 is the one
        before it, and so on. Returns [] if n is out of range. Powers skip-back
        ('previous item') navigation."""
        d = self._entries.get(session)
        if not d or n < 0:
            return []
        ordered_ids = []                       # distinct msg_ids, most-recent first
        for e in reversed(d):
            if e.msg_id not in ordered_ids:
                ordered_ids.append(e.msg_id)
        if n >= len(ordered_ids):
            return []
        target = ordered_ids[n]
        return [e for e in d if e.msg_id == target]

    def unheard(self, session: str) -> list:
        """All not-yet-completed entries for session, oldest first."""
        return [e for e in self._entries.get(session, ()) if not e.heard]

    def reset(self, session: str) -> None:
        """Forget a session entirely (SESSION_END)."""
        self._entries.pop(session, None)
        self._msg_id.pop(session, None)
        self._group_seq.pop(session, None)
        self._turn_id.pop(session, None)
        self._touch.pop(session, None)

