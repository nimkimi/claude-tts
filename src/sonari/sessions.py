from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Identity:
    """Terminal identity captured at SessionStart, used by focus-follow."""
    term_program: str = ""
    tty: str = ""
    iterm_session_id: str = ""


def _basename(cwd) -> "str | None":
    """Portable last path component of *cwd*, handling both / and \\ separators
    regardless of host OS (a Windows cwd is named correctly even on a macOS runner).
    Empty/None -> None."""
    if not cwd:
        return None
    s = str(cwd).replace("\\", "/").rstrip("/")
    base = s.rsplit("/", 1)[-1]
    return base or None


class SessionManager:
    def __init__(self, background_policy: str = "earcon_only") -> None:
        self.background_policy = background_policy
        # session id -> cwd basename (or None). Insertion-ordered (dict) so a future
        # list/cycle is stable; membership/`in`/len behave like the old set.
        self._sessions: "dict[str, str | None]" = {}
        self._foreground: "str | None" = None
        self._pinned: "str | None" = None      # None = auto (follow last prompt)
        self._identities: "dict[str, Identity]" = {}

    def _record(self, session: str, cwd) -> None:
        folder = _basename(cwd)
        if session not in self._sessions:
            self._sessions[session] = folder
        elif folder:                            # update only with a non-empty name
            self._sessions[session] = folder

    def set_foreground(self, session: str, cwd=None) -> None:
        self._record(session, cwd)
        self._foreground = session

    def foreground(self) -> "str | None":
        """The session that owns the voice: the pinned one if pinned, else the last
        session to submit a prompt / start."""
        return self._pinned if self._pinned is not None else self._foreground

    def is_foreground(self, session: str) -> bool:
        fg = self.foreground()
        return fg is not None and session == fg

    def register(self, session: str, cwd=None) -> None:
        self._record(session, cwd)

    def unregister(self, session: str) -> None:
        self._sessions.pop(session, None)
        self._identities.pop(session, None)
        if self._foreground == session:
            self._foreground = None
        if self._pinned == session:             # pinned session ended -> auto
            self._pinned = None

    def should_speak(self, session: str) -> bool:
        return self.is_foreground(session)

    def pinned(self) -> "str | None":
        return self._pinned

    def folder(self, session: str) -> "str | None":
        return self._sessions.get(session)

    def set_identity(self, session: str, identity: "Identity") -> None:
        """Store the terminal identity for *session*.

        Don't-clobber-with-empties rule (same as the folder map in `_record`):
        SESSION_START re-fires on resume/clear/compact for the same session_id, and
        tty derivation is best-effort and can intermittently return "". If an
        identity already exists, each incoming EMPTY field keeps the existing value
        and each non-empty field updates it. A real terminal switch (all fields
        non-empty) still fully updates; only empties are ignored. First set on an
        absent session stores it as-is."""
        existing = self._identities.get(session)
        if existing is None:
            self._identities[session] = identity
            return
        self._identities[session] = Identity(
            term_program=identity.term_program or existing.term_program,
            tty=identity.tty or existing.tty,
            iterm_session_id=identity.iterm_session_id or existing.iterm_session_id,
        )

    def identity(self, session: str) -> "Identity | None":
        return self._identities.get(session)

    def focus(self, session: str, cwd=None) -> None:
        """Explicitly move the voice to *session* (the jump-to-waiting hotkey):
        clear any pin — an explicit jump overrides a pin — and set it foreground.
        Does NOT re-pin."""
        self._record(session, cwd)
        self._pinned = None
        self._foreground = session

    def pin_toggle(self) -> "tuple[str, str | None]":
        """Toggle the pin against the RAW last-prompt foreground.

        - no foreground          -> ("none", None), no change
        - already pinned to it   -> unpin -> ("unpinned", folder)
        - otherwise              -> pin it -> ("pinned", folder)
        """
        cur = self._foreground
        if cur is None:
            return ("none", None)
        if self._pinned == cur:
            self._pinned = None
            return ("unpinned", self._sessions.get(cur))
        self._pinned = cur
        return ("pinned", self._sessions.get(cur))
