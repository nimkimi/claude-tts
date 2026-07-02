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
        self.warned_immediate = False       # warned once about immediate selection
        self.guided = False                 # received the setup-guidance cue once

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
