from __future__ import annotations

from dataclasses import dataclass

from sonari import ttyutil


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
        # Sessions whose tty claim was taken over by another session (the node was
        # recycled by a NEW terminal — pty names reuse the lowest free number, so a
        # long-lived roster WILL see this). Fail-CLOSED in is_live until the session
        # re-asserts a tty itself (W4 re-captures on every prompt, so an alive session
        # heals on its next submit; a dead one stays out of the ring instead of
        # reviving as a phantom).
        self._tty_evicted: "set[str]" = set()

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

    def set_speaker(self, session: str) -> None:
        """Advance the VOICE owner WITHOUT moving the workspace. Keep-going calls this
        to read accumulated background output while _foreground (the last
        deliberately-acted session) stays put — the window never moves on its own
        (R12/D10). Unlike set_foreground()/focus() it writes ONLY _speaker: no folder
        _record, no registration, no _foreground write. Caller holds the daemon lock
        by convention (keep-going runs inside the speak-loop lock)."""
        self._speaker = session

    def foreground(self) -> "str | None":
        """The last deliberately-acted session (submit / jump / cycle). NOTE: the
        voice owner is speaker() — since SP1 split the two, this is no longer "who
        owns the voice"; in SP1 they happen to coincide, but SP2 diverges them."""
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
        self._tty_evicted.discard(session)
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
        if identity.tty:
            # Exclusive-tty invariant: a tty device node has exactly ONE live
            # claimant. A non-empty capture is positive evidence this session owns
            # the node NOW, so every other claimant's terminal provably is not on
            # it anymore (the OS never gives a node to two terminals): clear their
            # claim — a stale tty must never match os_focus or raise a foreign
            # window — and fail-close their liveness until they re-assert (W4).
            for other, ident in self._identities.items():
                if other != session and ident.tty == identity.tty:
                    self._identities[other] = Identity(
                        term_program=ident.term_program,
                        tty="",
                        iterm_session_id=ident.iterm_session_id,
                    )
                    self._tty_evicted.add(other)
            self._tty_evicted.discard(session)
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

    def is_live(self, session: str) -> bool:
        """True if *session*'s terminal is still open (its captured tty device node
        exists). Fail-open: an unknown identity or empty tty -> live (never hide a
        live session). Pure read over _identities; writes nothing."""
        if session in self._tty_evicted:
            # Positive steal evidence beats fail-open: its recorded terminal is
            # someone else's now, and it has not re-asserted one of its own.
            return False
        ident = self._identities.get(session)
        return ttyutil.tty_alive(ident.tty if ident is not None else "")

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

