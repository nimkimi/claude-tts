import threading
import sonari.ttyutil as ttyutil
from sonari.protocol import MsgType
from sonari.queue import SpeechItem
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon

def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _liveness(monkeypatch, dead):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff not in `dead`
    (the tests/test_chooser.py / test_identity_eviction.py idiom)."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _ident(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


# --- Test C: keep-going advances the voice but NEVER moves the workspace (R12/D10) ---
def test_keep_going_does_not_move_foreground():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "from b", False)
    daemon._speak_loop_once()                      # A empty/idle -> keep-going adopts B
    assert sessions.speaker() == "B"               # voice advanced
    assert sessions.foreground() == "A"            # workspace stayed put
    assert any(s and "from b" in s for s in speaker.spoken)


# --- Test D: longest-waiting-first = minimum oldest SpeechItem.id (§14), NOT insertion order ---
def test_keep_going_longest_waiting_first():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("late", cwd="/x/late")       # registered FIRST (insertion order would pick it)
    sessions.register("early", cwd="/x/early")
    daemon._enqueue("early", "prose", "older", False)   # lower id (enqueued first)
    daemon._enqueue("late", "prose", "newer", False)    # higher id
    daemon._speak_loop_once()
    assert sessions.speaker() == "early"           # picked min oldest_id, not insertion order
    assert any(s and "older" in s for s in speaker.spoken)
    # (Few items, far below the 200 backlog_cap, so cap eviction never fires — §4. If a
    # variant preloads past the cap, build the streams with cap=None so "oldest" can't
    # silently become "oldest-surviving".)


# --- Test F: keep-going bootstraps from a None speaker (the post-session-end path) ---
def test_keep_going_bootstraps_from_none_speaker():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "hello b", False)
    sessions.unregister("A")                       # A ends -> _speaker becomes None (sessions.py:93-94)
    assert sessions.speaker() is None
    daemon._speak_loop_once()
    assert sessions.speaker() == "B"              # adopted the background session from None
    assert any(s and "hello b" in s for s in speaker.spoken)


# --- Test E (negative): keep-going SUPPRESSED when speaker queue empty but prose_buffer non-empty ---
def test_keep_going_suppressed_when_prose_buffered():
    """The speaker's queue is empty but it has buffered prose awaiting minqueue flush.
    Keep-going must NOT advance the voice to a background session — the speaker still
    has speech to deliver. Pins the `len(st.prose_buffer) == 0` clause of
    _stream_quiescent so dropping it would turn this test RED."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._enqueue("B", "prose", "from b", False)
    # Leave A's queue empty but give A a non-empty prose_buffer (direct injection —
    # prose_buffer is a plain list per SessionStream, and _buffer_prose appends tuples).
    st_a = daemon._stream("A")
    st_a.prose_buffer.append(("buffered prose not yet flushed", None))
    daemon._speak_loop_once()
    # Voice must NOT have advanced: speaker is still A, B's text must NOT be spoken.
    assert sessions.speaker() == "A", "keep-going advanced voice despite non-empty prose_buffer"
    assert not any(s and "from b" in s for s in speaker.spoken), \
        "B's text was spoken even though A still had buffered prose"


# --- Test G: a keep-going-voiced decision is unanswerable until you jump (R10, fail-closed) ---
def test_keep_going_voiced_decision_unanswerable_until_jump():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B")
    daemon._pending_decisions["B"] = {"event": threading.Event(), "behavior": None}
    daemon._enqueue("B", "permission", "Allow X?", True)
    daemon._speak_loop_once()                      # keep-going voices B's decision
    assert sessions.speaker() == "B"
    assert sessions.workspace() == "A"             # workspace still A (no deliberate move)
    daemon.handle_message(_msg(MsgType.ANSWER_PERMISSION, "", behavior="allow"))
    assert daemon._pending_decisions["B"]["behavior"] is None   # B NOT auto-answered
    assert speaker.earcons[-1] == "error_misdirected"   # W6 fail-closed tone (decisions.py:188)


# --- D3 §4d: keep-going skips a dead session's backlog (the T1 headline fix) ---
def test_keep_going_skips_dead_tty_backlog(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysD"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("D", cwd="/x/D"); _ident(sessions, "D", "/dev/ttysD")
    sessions.register("L", cwd="/x/L"); _ident(sessions, "L", "/dev/ttysL")
    daemon._enqueue("D", "prose", "dead backlog", False)   # oldest (lower id)
    daemon._enqueue("L", "prose", "live backlog", False)   # younger (higher id)
    daemon._speak_loop_once()
    assert sessions.speaker() == "L"               # live picked despite being younger
    assert any(s and "live backlog" in s for s in speaker.spoken)
    assert not any(s and "dead backlog" in s for s in speaker.spoken)


def test_keep_going_all_candidates_dead_returns_none(monkeypatch):
    _liveness(monkeypatch, dead={"/dev/ttysD"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.register("D", cwd="/x/D"); _ident(sessions, "D", "/dev/ttysD")
    daemon._enqueue("D", "prose", "dead backlog", False)
    daemon._speak_loop_once()
    assert sessions.speaker() == "fg"              # voice stayed idle; no dead adoption
    assert not any(s and "dead backlog" in s for s in speaker.spoken)


def test_keep_going_still_adopts_a_pending_stream():
    """A restored (provisional) session with a daemon-authored queued item — mirroring
    the restart line's own delivery — must still be adopted. Seeded via _enqueue under
    the daemon lock (never a session-authored message: R1 would clear the quarantine)."""
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.load_state({"s1": {"folder": "repo", "number": 1}})
    assert sessions.is_provisional("s1") is True
    with daemon._lock:
        daemon._enqueue("s1", "prose", "Resumed.", False,
                        mute_exempt=True, pause_exempt=True)
    daemon._speak_loop_once()
    assert sessions.speaker() == "s1"              # pending stays adoptable
    assert any(s and "Resumed." in s for s in speaker.spoken)


def test_keep_going_skips_evicted_backlog(monkeypatch):
    """Dead by steal evidence (tty recycled onto a fresh session) is skipped;
    complements tests/test_identity_eviction.py:162's jump-side pin."""
    _liveness(monkeypatch, dead=set())             # the node exists (recycled)
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    _ident(sessions, "A", "/dev/ttysA")
    sessions.register("stale", cwd="/x/stale"); _ident(sessions, "stale", "/dev/ttysT")
    daemon._enqueue("stale", "prose", "stale backlog", False)   # oldest, pre-eviction
    sessions.register("fresh", cwd="/x/fresh"); _ident(sessions, "fresh", "/dev/ttysT")  # steals the tty
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    daemon._enqueue("C", "prose", "c backlog", False)           # younger
    daemon._speak_loop_once()
    assert sessions.speaker() == "C"               # evicted stale skipped despite older backlog
    assert any(s and "c backlog" in s for s in speaker.spoken)
    assert not any(s and "stale backlog" in s for s in speaker.spoken)
