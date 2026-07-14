"""Exclusive-tty eviction (F-RETEST-2): a tty device node has exactly ONE live
claimant. When a session captures a non-empty tty, every OTHER session holding
that tty loses its claim — the node was recycled, so the old claimant's terminal
provably isn't on it anymore. The evicted session is fail-CLOSED (not live) until
it re-asserts a tty itself (W4 re-captures on every prompt, so an alive session
heals on its next submit). Without this, a phantom REVIVES in the cycle ring when
its tty name is recycled, and os_focus resolves to the stale first registrant."""
import sonari.ttyutil as ttyutil
from sonari.protocol import MsgType
from sonari.sessions import Identity, SessionManager
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _liveness(monkeypatch, dead):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff its tty not in `dead`."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _ident(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


# --- 1. capturing a tty evicts the stale claimant: tty cleared, no longer live ---
def test_capture_evicts_stale_claimant(monkeypatch):
    _liveness(monkeypatch, dead=set())            # the node EXISTS (it was recycled)
    m = SessionManager()
    m.register("stale"); _ident(m, "stale", "/dev/ttysT")
    m.register("fresh"); _ident(m, "fresh", "/dev/ttysT")   # new terminal reuses the node
    assert m.identity("stale").tty == ""          # claim gone (os_focus can't match it)
    assert m.identity("stale").term_program == "Apple_Terminal"   # other fields kept
    assert m.is_live("stale") is False            # fail-CLOSED on the steal signal
    assert m.is_live("fresh") is True
    assert m.identity("fresh").tty == "/dev/ttysT"


# --- 2. an evicted session heals by re-asserting a non-empty tty (W4 path) ---
def test_evicted_session_heals_on_reassert(monkeypatch):
    _liveness(monkeypatch, dead=set())
    m = SessionManager()
    m.register("stale"); _ident(m, "stale", "/dev/ttysT")
    m.register("fresh"); _ident(m, "fresh", "/dev/ttysT")
    assert m.is_live("stale") is False
    _ident(m, "stale", "/dev/ttysNEW")            # its next prompt re-captures
    assert m.is_live("stale") is True
    assert m.identity("stale").tty == "/dev/ttysNEW"


# --- 3. an EMPTY re-capture does not heal (best-effort tty probe can return "") ---
def test_empty_reassert_does_not_heal(monkeypatch):
    _liveness(monkeypatch, dead=set())
    m = SessionManager()
    m.register("stale"); _ident(m, "stale", "/dev/ttysT")
    m.register("fresh"); _ident(m, "fresh", "/dev/ttysT")
    _ident(m, "stale", "")                        # empty capture: no claim asserted
    assert m.is_live("stale") is False


# --- 4. boundary pins: no eviction without a claim; no self-eviction ---
def test_empty_capture_evicts_nobody(monkeypatch):
    _liveness(monkeypatch, dead=set())
    m = SessionManager()
    m.register("holder"); _ident(m, "holder", "/dev/ttysT")
    m.register("other"); _ident(m, "other", "")
    assert m.identity("holder").tty == "/dev/ttysT"
    assert m.is_live("holder") is True


def test_reasserting_own_tty_does_not_self_evict(monkeypatch):
    _liveness(monkeypatch, dead=set())
    m = SessionManager()
    m.register("holder"); _ident(m, "holder", "/dev/ttysT")
    _ident(m, "holder", "/dev/ttysT")             # same session, same tty (every prompt)
    assert m.identity("holder").tty == "/dev/ttysT"
    assert m.is_live("holder") is True


# --- 5. unregister clears the evicted mark (same session id re-registers fresh) ---
def test_unregister_clears_evicted_mark(monkeypatch):
    _liveness(monkeypatch, dead=set())
    m = SessionManager()
    m.register("stale"); _ident(m, "stale", "/dev/ttysT")
    m.register("fresh"); _ident(m, "fresh", "/dev/ttysT")
    assert m.is_live("stale") is False
    m.unregister("stale")
    m.register("stale")                           # re-registered, no identity yet
    assert m.is_live("stale") is True             # back to fail-open (no stale mark leaked)


# --- 6. os_focus on a recycled tty resolves to the NEW claimant, not the stale one ---
def test_os_focus_resolves_to_new_claimant(monkeypatch):
    _liveness(monkeypatch, dead=set())
    m = SessionManager()
    m.register("stale"); _ident(m, "stale", "/dev/ttysT")   # registered FIRST
    m.register("fresh"); _ident(m, "fresh", "/dev/ttysT")
    m.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysT")
    assert m.focused_session() == "fresh"         # pre-fix: first-match returned "stale"


# --- 6b. an os-focus pin made BEFORE the recycler registers transfers to it ---
#         (hotkeyd's focus signal beats the SessionStart hook round-trip, and its
#         dedupe won't re-send an unchanged tty — the pin must not stay on the ghost)
def test_os_focus_pin_transfers_to_new_claimant_on_eviction(monkeypatch):
    _liveness(monkeypatch, dead=set())
    m = SessionManager()
    m.register("stale"); _ident(m, "stale", "/dev/ttysT")
    m.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysT")
    assert m.focused_session() == "stale"         # pinned before the new claimant exists
    m.register("fresh"); _ident(m, "fresh", "/dev/ttysT")
    # The pin meant "ttysT's window is frontmost"; ttysT just proved to be fresh's.
    assert m.focused_session() == "fresh"
    assert m.workspace() == "fresh"


# --- 6d. a post-eviction focus signal cannot re-pin the ghost (its claim is gone) ---
def test_os_focus_cannot_match_evicted_session(monkeypatch):
    _liveness(monkeypatch, dead=set())
    m = SessionManager()
    m.set_foreground("home")
    m.register("stale"); _ident(m, "stale", "/dev/ttysT")
    m.register("fresh"); _ident(m, "fresh", "/dev/ttysT")
    m.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttysT")
    assert m.focused_session() == "fresh"         # only the new claimant can match


# --- 6e. the iTerm GUID axis: an evicted session must not win the pin either ---
#         (same-pane succession: a killed claude + a fresh one in one iTerm tab share
#         GUID and tty; eviction clears the tty but PRESERVES the GUID, and the loser's
#         empty tty means later re-captures skip the eviction loop — no self-heal)
def test_os_focus_iterm_guid_cannot_repin_evicted_session(monkeypatch):
    _liveness(monkeypatch, dead=set())
    m = SessionManager()
    m.register("stale")
    m.set_identity("stale", Identity(term_program="iTerm.app", tty="/dev/ttysT",
                                     iterm_session_id="w0t0p0:GUID-G"))
    m.register("fresh")
    m.set_identity("fresh", Identity(term_program="iTerm.app", tty="/dev/ttysT",
                                     iterm_session_id="w0t0p0:GUID-G"))
    m.set_os_focus(term_program="iTerm.app", iterm_session_id="w0t0p0:GUID-G")
    assert m.focused_session() == "fresh"         # first-match must skip the evictee


# --- 7. the ring: a recycled node no longer REVIVES the phantom in ⌃⌘Tab ---
def test_cycle_skips_evicted_phantom_after_recycle(monkeypatch):
    _liveness(monkeypatch, dead=set())            # node exists again (recycled)
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("stale", cwd="/x/stale"); _ident(sessions, "stale", "/dev/ttysT")
    sessions.register("fresh", cwd="/x/fresh"); _ident(sessions, "fresh", "/dev/ttysT")
    daemon.handle_message(_msg(MsgType.CYCLE_SESSION, "", direction="next"))
    # roster [A, fresh] (stale evicted); from A -> fresh. Pre-fix it landed on stale.
    assert sessions.speaker() == "fresh"


# --- 8. ⌃⌘J: an evicted phantom's backlog is not a jump target ---
def test_jump_waiting_skips_evicted_phantom_backlog(monkeypatch):
    _liveness(monkeypatch, dead=set())
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("stale", cwd="/x/stale"); _ident(sessions, "stale", "/dev/ttysT")
    sessions.register("fresh", cwd="/x/fresh"); _ident(sessions, "fresh", "/dev/ttysT")
    daemon._enqueue("stale", "prose", "stale backlog", False)
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    daemon._enqueue("C", "prose", "c backlog", False)
    daemon.handle_message(_msg(MsgType.JUMP_WAITING, ""))
    assert sessions.speaker() == "C"              # evicted stale skipped despite backlog
