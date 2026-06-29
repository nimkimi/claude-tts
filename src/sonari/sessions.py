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


def _bare_iterm_guid(s: str) -> str:
    """iTerm2's ITERM_SESSION_ID is 'wNtNpN:GUID'; the scriptable `id of session`
    is the bare GUID after the last ':'. Return the part after the last ':', else s."""
    if not s:
        return ""
    tail = s.rpartition(":")[2]
    return tail or s


class SessionManager:
    def __init__(self, background_policy: str = "earcon_only") -> None:
        self.background_policy = background_policy
        # session id -> cwd basename (or None). Insertion-ordered (dict) so a future
        # list/cycle is stable; membership/`in`/len behave like the old set.
        self._sessions: "dict[str, str | None]" = {}
        self._foreground: "str | None" = None
        self._speaker: "str | None" = None    # the VOICE owner (speak loop reads this).
        # SP1: kept == _foreground (deliberate setters move both). SP2's keep-going
        # will advance _speaker on its own, diverging from _foreground (= last-acted).
        self._os_focused_session: "str | None" = None    # session in the OS-focused terminal
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
        self._speaker = session

    def foreground(self) -> "str | None":
        """The session that owns the voice: the last session to submit a prompt / start."""
        return self._foreground

    def speaker(self) -> "str | None":
        """The session the voice is reading (the speak loop plays this stream).
        SP1: == foreground(); SP2 keep-going advances it independently."""
        return self._speaker

    def workspace(self) -> "str | None":
        """The front terminal + keyboard: the OS-focused session if known, else the
        last deliberately-acted session (foreground). The spec's 'workspace' — where
        you answer and what raises. Independent of the speaker once keep-going lands."""
        return self.focused_session() or self._foreground

    def session_ids(self) -> "list[str]":
        """All registered session ids in insertion order — the cycle roster (⌃⌘Tab).
        Encapsulates the private _sessions dict so handlers don't poke it directly."""
        return list(self._sessions.keys())

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
        if self._speaker == session:
            self._speaker = None
        if self._os_focused_session == session:
            self._os_focused_session = None

    def should_speak(self, session: str) -> bool:
        return self.is_foreground(session)

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
        set it foreground."""
        self._record(session, cwd)
        self._foreground = session
        self._speaker = session

    def set_os_focus(self, term_program: str = "", tty: str = "",
                     iterm_session_id: str = "", focused: bool = True) -> None:
        """Record which terminal currently has OS keyboard focus, resolved to a live
        session. `focused=False` (or an unresolvable identity) clears it. Match is by
        NON-EMPTY identity only: tty for Apple_Terminal, bare GUID for iTerm.app. This
        is the INBOUND focus signal — distinct from focus()/foreground() (the voice)."""
        if not focused:
            self._os_focused_session = None
            return
        match = None
        if term_program == "Apple_Terminal" and tty:
            for sess, ident in self._identities.items():
                if ident.tty and ident.tty == tty:
                    match = sess
                    break
        elif term_program == "iTerm.app" and iterm_session_id:
            want = _bare_iterm_guid(iterm_session_id)
            for sess, ident in self._identities.items():
                if ident.iterm_session_id and _bare_iterm_guid(ident.iterm_session_id) == want:
                    match = sess
                    break
        self._os_focused_session = match

    def focused_session(self) -> "str | None":
        """The session whose terminal has OS keyboard focus, iff still registered.
        Returns None when focus is unknown/unmapped — callers fall back to foreground()."""
        s = self._os_focused_session
        return s if (s is not None and s in self._sessions) else None

