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
        # Last raw focus signal (term_program, tty, iterm_session_id), retained
        # while a terminal stays focused. The watcher dedupes unchanged focus, so
        # a signal that lands before its session's first prompt (no identity yet)
        # resolves to None and never re-fires — set_identity repairs the pin from
        # this instead (late-identity repair, ear-pass find 2026-07-16).
        self._os_focus_raw: "tuple[str, str, str] | None" = None
        self._identities: "dict[str, Identity]" = {}
        # Sessions whose tty claim was taken over by another session (the node was
        # recycled by a NEW terminal — pty names reuse the lowest free number, so a
        # long-lived roster WILL see this). Fail-CLOSED in is_live until the session
        # re-asserts a tty itself (W4 re-captures on every prompt, so an alive session
        # heals on its next submit; a dead one stays out of the ring instead of
        # reviving as a phantom).
        self._tty_evicted: "set[str]" = set()
        # Stable spoken session numbers (chooser spec §6): lowest free >= 1 at
        # registration, stable for the session's lifetime, freed on unregister.
        # Spoken in chooser previews, the W Also-map, the ⌃⌘W clauses, and the
        # registration announce. NEVER injected into content attribution
        # prefixes or jump cues (noise).
        self._numbers: "dict[str, int]" = {}
        # Recency (spec §8), most-recent first. Updated by DELIBERATE acts only:
        # set_foreground()/focus() (submit, jump, chooser commit) and a MATCHED
        # set_os_focus (a click counts as "you were there"). set_speaker() NEVER
        # touches it — keep-going voice drift is not presence (R12 discipline).
        # In-memory, like the roster.
        self._mru: "list[str]" = []
        # SP6 (§4.4): sessions restored from disk are QUARANTINED until they
        # re-capture a real identity this lifetime. Seeded by load_state, cleared
        # by set_identity. Empty for every normally-registered session, so the
        # is_live fail-close and the ⌃⌘W/chooser exclusions are no-ops off-restore.
        self._provisional: "set[str]" = set()
        self._announced = set()

    def _record(self, session: str, cwd) -> None:
        folder = _basename(cwd)
        if session not in self._sessions:
            self._sessions[session] = folder
        elif folder:                            # update only with a non-empty name
            self._sessions[session] = folder
        self._assign_number(session)

    def _assign_number(self, session: str) -> None:
        """Assign the lowest free number >= 1 once; stable until unregister."""
        if session in self._numbers:
            return
        used = set(self._numbers.values())
        n = 1
        while n in used:
            n += 1
        self._numbers[session] = n

    def _touch_mru(self, session: str) -> None:
        if session in self._mru:
            self._mru.remove(session)
        self._mru.insert(0, session)

    def number(self, session: str) -> "int | None":
        """The stable spoken number for *session*, or None if unregistered."""
        return self._numbers.get(session)

    def session_for_number(self, n) -> "str | None":
        """The registered session holding number *n*, or None (digit teleport)."""
        for s, num in self._numbers.items():
            if num == n:
                return s
        return None

    def mru(self) -> "list[str]":
        """Deliberately-visited sessions, most-recent first (a copy)."""
        return list(self._mru)

    def set_foreground(self, session: str, cwd=None) -> None:
        self._record(session, cwd)
        self._foreground = session
        self._speaker = session
        self._touch_mru(session)

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
        """All registered session ids in insertion order — the roster (chooser snapshot /
        W Also-map). Encapsulates the private _sessions dict so handlers don't poke it directly."""
        return list(self._sessions.keys())

    def is_foreground(self, session: str) -> bool:
        fg = self.foreground()
        return fg is not None and session == fg

    def register(self, session: str, cwd=None) -> None:
        self._record(session, cwd)

    def claim_announce(self, session: str) -> bool:
        """Claim the one-shot registration announce for *session*.

        TEST-AND-SET, not a query — call it exactly once, at the announce site.
        The announce used to be gated on `is_new` inferred from whether the id
        was already registered, but a single SessionStart hook emits TWO
        messages and the first one registers the session, so `is_new` was
        always False by the time SESSION_START arrived and the announce never
        fired. Claiming explicitly is robust to how many messages a hook emits.
        """
        if session in self._announced:
            return False
        self._announced.add(session)
        return True

    def unregister(self, session: str) -> None:
        self._sessions.pop(session, None)
        self._identities.pop(session, None)
        self._tty_evicted.discard(session)
        self._provisional.discard(session)
        self._numbers.pop(session, None)
        self._announced.discard(session)
        if session in self._mru:
            self._mru.remove(session)
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
        # SP6 (§4.4): re-capturing a real identity this lifetime lifts the
        # provisional quarantine — UNCONDITIONAL (even an all-empty best-effort
        # identity counts as "this session is back this lifetime"), so quarantine
        # ends exactly when the next prompt makes the pile reachable (§2).
        self._provisional.discard(session)
        if identity.tty:
            # Exclusive-tty invariant: a tty device node has exactly ONE live
            # claimant. A non-empty capture is positive evidence this session owns
            # the node NOW, so every other claimant's terminal provably is not on
            # it anymore (the OS never gives a node to two terminals): clear their
            # claim — a stale tty must never match os_focus or raise a foreign
            # window — and fail-close their liveness until they re-assert (W4).
            # Accepted edge: two ALIVE sessions can briefly share one tty (a
            # suspended `claude` plus a fresh one in the same window) — latest
            # asserter wins and the loser self-heals on its own next prompt.
            for other, ident in self._identities.items():
                if other != session and ident.tty == identity.tty:
                    self._identities[other] = Identity(
                        term_program=ident.term_program,
                        tty="",
                        iterm_session_id=ident.iterm_session_id,
                    )
                    self._tty_evicted.add(other)
                    if self._os_focused_session == other:
                        # The pin recorded "this tty's window is frontmost"; the
                        # tty just proved to be *session*'s terminal, so the pin
                        # belongs to the new claimant (hotkeyd dedupes unchanged
                        # focus signals, so nothing else would ever repair it).
                        self._os_focused_session = session
            self._tty_evicted.discard(session)
        existing = self._identities.get(session)
        if existing is None:
            self._identities[session] = identity
        else:
            self._identities[session] = Identity(
                term_program=identity.term_program or existing.term_program,
                tty=identity.tty or existing.tty,
                iterm_session_id=identity.iterm_session_id or existing.iterm_session_id,
            )
        # Late-identity repair (ear-pass find 2026-07-16): a focus signal that
        # arrived before this identity existed resolved to None, and — the watcher
        # dedupes unchanged focus — will never re-fire while that terminal stays
        # front. This identity may be the missing claimant, so re-resolve the
        # retained raw signal. Repair-only (None -> match): a live pin is never
        # re-adjudicated here — pin transfer on a tty steal stays the eviction
        # loop's job above.
        if self._os_focused_session is None and self._os_focus_raw is not None:
            self._resolve_os_focus()

    def identity(self, session: str) -> "Identity | None":
        return self._identities.get(session)

    def liveness(self, session: str) -> str:
        """The ONE composition of the three liveness signals (D3 spec §2):
        'live' | 'pending' | 'dead'. Precedence: pending (SP6-restored, not yet
        reconfirmed this lifetime) -> evicted (positive tty-steal evidence) ->
        dead-tty (captured node no longer exists) -> live. Fail-open on an
        unknown identity / empty tty is UNCHANGED throughout — never hide a
        live session. Pure read over _identities; writes nothing."""
        if session in self._provisional:
            return "pending"
        if session in self._tty_evicted:
            # Positive steal evidence beats fail-open: its recorded terminal is
            # someone else's now, and it has not re-asserted one of its own.
            return "dead"
        ident = self._identities.get(session)
        tty = ident.tty if ident is not None else ""
        if tty and not ttyutil.tty_alive(tty):
            return "dead"
        return "live"

    def is_live(self, session: str) -> bool:
        """True iff *session*'s liveness() is 'live'. Delegates to liveness() —
        the one composition (D3 spec §2) — so pending (SP6-restored,
        not-yet-reconfirmed) fails CLOSED and a tty-evicted or dead-tty session
        fails CLOSED, while an unknown identity / empty tty stays fail-OPEN
        (never hide a live session)."""
        return self.liveness(session) == "live"

    def focus(self, session: str, cwd=None) -> None:
        """Explicitly move the voice to *session* (the jump-to-waiting hotkey):
        set it foreground."""
        self._record(session, cwd)
        self._foreground = session
        self._speaker = session
        self._touch_mru(session)

    def set_os_focus(self, term_program: str = "", tty: str = "",
                     iterm_session_id: str = "", focused: bool = True) -> bool:
        """Record which terminal currently has OS keyboard focus, resolved to a live
        session. `focused=False` (or an unresolvable identity) clears it — including
        the retained raw signal, so a later identity never resurrects a pre-departure
        pin. Match is by NON-EMPTY identity only: tty for Apple_Terminal, bare GUID
        for iTerm.app. This is the INBOUND focus signal — distinct from
        focus()/foreground() (the voice). Returns True iff the resolved WORKSPACE
        session changed across this call (D2 §6.2: the feature layer cues on the
        change; this module stays audio-free). The late-identity repair path
        (set_identity -> _resolve_os_focus) is NOT a click and reports nothing."""
        before = self.workspace()
        if not focused:
            self._os_focus_raw = None
            self._os_focused_session = None
            return self.workspace() != before
        self._os_focus_raw = (term_program, tty, iterm_session_id)
        self._resolve_os_focus()
        return self.workspace() != before

    def _resolve_os_focus(self) -> None:
        """Resolve the retained raw focus signal against the current identities.
        Called on every inbound signal AND from set_identity's late-identity repair.
        An evicted session must not win the pin on ANY axis: eviction clears the
        tty (so the tty branch can't match it) but PRESERVES iterm_session_id, and
        same-pane succession leaves the ghost sharing the survivor's GUID with no
        self-heal (its empty tty means later captures skip the eviction loop)."""
        term_program, tty, iterm_session_id = self._os_focus_raw
        match = None
        if term_program == "Apple_Terminal" and tty:
            for sess, ident in self._identities.items():
                if sess in self._tty_evicted:
                    continue
                if ident.tty and ident.tty == tty:
                    match = sess
                    break
        elif term_program == "iTerm.app" and iterm_session_id:
            want = _bare_iterm_guid(iterm_session_id)
            for sess, ident in self._identities.items():
                if sess in self._tty_evicted:
                    continue
                if ident.iterm_session_id and _bare_iterm_guid(ident.iterm_session_id) == want:
                    match = sess
                    break
        self._os_focused_session = match
        if match is not None:
            # A resolved click IS presence (spec §8): the user was demonstrably there.
            self._touch_mru(match)

    def focused_session(self) -> "str | None":
        """The session whose terminal has OS keyboard focus, iff still registered.
        Returns None when focus is unknown/unmapped — callers fall back to foreground()."""
        s = self._os_focused_session
        return s if (s is not None and s in self._sessions) else None

    def to_state(self) -> dict:
        """Serialize the durable roster: folder label + stable number per
        session. Live pointers, identities, MRU, eviction are all transient
        (§4.2). PURE."""
        return {s: {"folder": self._sessions.get(s), "number": self._numbers.get(s)}
                for s in self._sessions}

    def load_state(self, data) -> None:
        """Rehydrate the roster and seed _provisional with every restored id
        (§4.4). Numbers are restored so digit teleports stay stable across the
        restart; a missing number is left unassigned (re-minted on demand). PURE."""
        for s, sd in data.items():
            self._sessions[s] = sd.get("folder")
            num = sd.get("number")
            if num is not None:
                self._numbers[s] = num
            self._provisional.add(s)

    def is_provisional(self, session: str) -> bool:
        """True for a restored-but-not-yet-reconfirmed session (§4.4). False for
        every session that has re-captured an identity, and for every session that
        was never restored."""
        return session in self._provisional

    def clear_quarantine(self, session: str) -> None:
        """Lift *session*'s provisional quarantine (D3 spec R1): any inbound
        message whose session field names a quarantined session is proof its
        hook is talking to the daemon, so the daemon's dispatch chokepoint
        calls this on EVERY session-authored message, not just an
        identity-carrying one. A no-op for a session that was never
        provisional. Public so a drift guard can keep the raw set private."""
        self._provisional.discard(session)

