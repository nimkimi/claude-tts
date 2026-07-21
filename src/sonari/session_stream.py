from __future__ import annotations

from sonari.assembler import ProseAssembler
from sonari.queue import SpeechQueue


class SessionStream:
    """All per-session speech state for one Claude Code session, in one place.

    Stage 2 of the per-session-streams redesign: each session owns its own speech
    queue, and the speak loop plays only the foreground session's stream.
    """

    def __init__(self, queue_cap: "int | None" = None) -> None:
        self.queue = SpeechQueue(cap=queue_cap)   # this session's own pending-speech queue
        self.assembler = ProseAssembler()
        self.prose_buffer: list = []        # [(text, HistoryEntry)] awaiting minqueue flush
        self.options: "str | None" = None   # last decision text, for reread
        self.nav_cursor = None              # anchored message id (None == latest)
        self.nav_turn = None                # two-level nav anchor: the turn being navigated
                                            # (None == the live turn); a new prompt snaps it back
        self.stopped = False                # per-session stop (⌃⌘S / ⌃⌘M); sticky across prompts
        self.announce_resume = False        # D2 §6.3: a Policy-A lift happened on the
                                            # SET_FOREGROUND leg; on_flush (or the
                                            # SESSION_START leg) delivers "Resumed."
                                            # AFTER the new-prompt clear. Transient —
                                            # never serialized.
        self.warned_immediate = False       # warned once about immediate selection
        self.guided = False                 # received the setup-guidance cue once
        # SP4 frontier: the monotonic "furthest I've dealt with" high-water mark,
        # (msg_id, seq) of a HistoryEntry, None == nothing dealt-with yet. DISTINCT
        # from nav_cursor (browse). Advanced ONLY by note_spoken (forward completion)
        # and the pile-skip gesture; never derived from heard (B1); never retreats;
        # NOT reset on a new prompt (cross-turn) — dropped only when SESSION_END pops
        # the stream. Plain JSON-shaped tuple so SP6 serializes it unchanged.
        self.frontier = None

    def reset_for_new_prompt(self) -> None:
        """A new user prompt (FLUSH): reset playback state with a fresh assembler,
        but KEEP the sticky flags (stopped / warned_immediate / guided). Does NOT
        clear self.queue — the FLUSH handler clears it so it can drop the dropped
        items' heard-markers."""
        self.assembler = ProseAssembler()
        self.prose_buffer = []
        self.options = None
        self.nav_cursor = None
        self.nav_turn = None

    def advance_frontier(self, key) -> None:
        """Monotonically advance the frontier to key=(msg_id, seq). No-op unless key
        is strictly ahead (None frontier == nothing dealt-with yet). The frontier
        NEVER retreats and is NOT derived from the heard flags."""
        if key is not None and (self.frontier is None or key > self.frontier):
            self.frontier = key

    def to_state(self) -> dict:
        """Serialize the durable frontier (list form — JSON has no tuple). Only
        the frontier persists; every other field is transient (§4.2). PURE."""
        return {"frontier": list(self.frontier) if self.frontier is not None else None}

    def load_state(self, data) -> None:
        """Rehydrate the frontier, converting JSON's list back to a TUPLE so
        `key > self.frontier` (tuple-vs-tuple) never raises TypeError (§6). PURE."""
        f = data.get("frontier")
        self.frontier = tuple(f) if f is not None else None
