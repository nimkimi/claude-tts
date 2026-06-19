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

    def message_ids_in_turn(self, session: str, turn_id: int) -> list:
        """Distinct message ids of the given turn, oldest first. Same truncated-head
        exclusion as `message_ids` (#8): a group whose head was evicted by the rolling
        cap is excluded so nav never replays a fragment. Powers within-response nav
        over any turn — current or past (Stage 5 two-level navigation)."""
        d = self._entries.get(session)
        if not d:
            return []
        ids = []
        seen = set()
        for e in d:
            if e.turn_id != turn_id:
                continue
            if e.msg_id in seen:
                continue
            seen.add(e.msg_id)
            if e.seq == 0:
                ids.append(e.msg_id)
        return ids

    def message_ids(self, session: str) -> list:
        """Distinct message ids of the CURRENT turn, oldest first (the live response).
        Bounded to the current turn so the single-level within-response nav never walks
        into prior turns (Stage 4). Delegates to `message_ids_in_turn` for the live turn;
        `message_ids_in_turn` serves any past turn for Stage 5's two-level nav."""
        return self.message_ids_in_turn(session, self._turn_id.get(session, 0))

    def turn_ids(self, session: str) -> list:
        """Navigable turn ids for the session, oldest first. A turn is navigable iff it
        still has at least one present message-group head (`message_ids_in_turn` non-empty)
        — a turn whose entries were entirely evicted, or whose only survivors are mid-group
        fragments, is excluded. Powers response-to-response navigation (Stage 5)."""
        d = self._entries.get(session)
        if not d:
            return []
        ordered = []
        seen = set()
        for e in d:
            if e.turn_id not in seen:
                seen.add(e.turn_id)
                ordered.append(e.turn_id)
        return [t for t in ordered if self.message_ids_in_turn(session, t)]

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
        """Not-yet-heard entries of the CURRENT turn only, oldest first.

        §7 (Stage 4): the transcript persists across turns, but `unheard` stays
        bounded to the live turn. With catch_up/REPEAT retired it has no replay
        consumer; spanning the whole transcript would be unbounded and meaningless.
        Heard-marking still flips entries from the speak loop regardless of turn."""
        cur_turn = self._turn_id.get(session, 0)
        return [e for e in self._entries.get(session, ())
                if e.turn_id == cur_turn and not e.heard]

    def reset(self, session: str) -> None:
        """Forget a session entirely (SESSION_END)."""
        self._entries.pop(session, None)
        self._msg_id.pop(session, None)
        self._group_seq.pop(session, None)
        self._turn_id.pop(session, None)
        self._touch.pop(session, None)

