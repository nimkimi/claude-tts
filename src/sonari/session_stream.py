from __future__ import annotations

from sonari.assembler import ProseAssembler


class SessionStream:
    """All per-session speech state for one Claude Code session, in one place.

    Stage 1 of the per-session-streams redesign: a pure container that replaces
    the parallel per-session dicts/sets formerly held directly on SpeechDaemon.
    The speech queue stays shared in Stage 1; per-stream queues arrive in Stage 2.
    """

    def __init__(self) -> None:
        self.assembler = ProseAssembler()
        self.prose_buffer: list = []        # [(text, HistoryEntry)] awaiting minqueue flush
        self.options: "str | None" = None   # last decision text, for reread
        self.nav_cursor = None              # anchored message id (None == latest)
        self.captured = False               # message started while the voice was unavailable
        self.open_msg = False               # an assistant message is currently streaming
        self.muted = False                  # sticky per-session mute
        self.warned_immediate = False       # warned once about immediate selection
        self.guided = False                 # received the setup-guidance cue once

    def reset_for_new_prompt(self) -> None:
        """A new user prompt (FLUSH): reset playback state with a fresh assembler,
        but KEEP the sticky flags (muted / warned_immediate / guided), matching the
        current FLUSH handler exactly."""
        self.assembler = ProseAssembler()
        self.prose_buffer = []
        self.options = None
        self.nav_cursor = None
        self.captured = False
        self.open_msg = False
