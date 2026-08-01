"""D3 fix-wave A: who may voice a DEAD session's stream.

§4d keeps the voice off a dead session's backlog AUTOMATICALLY — keep-going
never adopts one. Three sites compose that rule, and the whole-branch review
found the seam between them broken in both directions:

1. Automatic flow never adopts dead (T9, `_select_keep_going` — unchanged).
2. A DELIBERATE press may adopt a dead stream and sanctions reading it. Idle
   ⌃⌘W and catch-up delivery (§4f) compose their answer INTO the dead
   destination's own stream; with keep-going refusing it and speaker() None,
   the correct answer was composed and never voiced (WB-C1 CRITICAL, WB-C2).
   Such a press now takes the voice itself and marks the stream consciously
   re-opened — including whatever backlog already sat there (the wrinkle,
   pinned below).
3. A dead speaker WITHOUT that sanction RELEASES the voice. §4d was
   selection-time only, so a session that died MID-DRAIN was auto-voiced to
   the end of its pile (R-1). Skipping the pop wedges the voice forever
   (probe G); releasing it — `set_speaker(None)`, queue intact — lets the
   bootstrap-from-None path reach a live session, the idiom chooser.py:214
   already uses.
"""
from sonari import ttyutil
from sonari.protocol import PROTOCOL_VERSION
from sonari.sessions import Identity
from tests.daemon_helpers import make_daemon


def _liveness(monkeypatch, dead):
    """Fake tty_alive: empty tty -> live (fail-open); else live iff not in `dead`
    (the tests/test_chooser.py idiom). `dead` is mutated in place by the
    mid-drain tests, so a terminal can close between two speak-loop turns."""
    monkeypatch.setattr(ttyutil, "tty_alive",
                        lambda tty: True if not tty else tty not in dead)


def _ident(sessions, sid, tty):
    sessions.set_identity(sid, Identity(term_program="Apple_Terminal", tty=tty))


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _drain(daemon, n):
    for _ in range(n):
        daemon._speak_loop_once()


def _dead_workspace(monkeypatch, folder="web", tty="/dev/ttysW"):
    """A one-session fleet whose workspace terminal is gone and whose voice is
    idle — the exact WB-C1/WB-C2 conjunction (a crashed terminal plus the other
    session ending normally, the morning-after fleet state D3 targets)."""
    dead = {tty}
    _liveness(monkeypatch, dead)
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("ws", cwd="/x/" + folder)
    sessions.set_foreground("ws")
    _ident(sessions, "ws", tty)                  # its captured node no longer exists
    sessions.set_speaker(None)                   # the voice is idle (legitimate post-SP3)
    assert sessions.liveness("ws") == "dead"
    return daemon, speaker, sessions


# --- WB-C1 (CRITICAL): the idle where-am-I readout on a dead workspace ---
def test_idle_whereami_on_a_dead_workspace_is_voiced(monkeypatch):
    """The P2 recipe. The readout composes correctly (Task 5 marks it ", closed")
    and lands in the workspace stream because speaker() is None — but T9 taught
    keep-going to skip dead streams, so it sat there forever: no speech, no
    tone, nothing, from the product's primary status verb."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon.handle_message(_msg("where_am_i", "ws"))
    _drain(daemon, 5)
    assert "Nothing playing. Keyboard: web 1, closed." in speaker.spoken


# --- the sanctioned-drain wrinkle: the deliberate read is the WHOLE stream ---
def test_sanctioned_dead_read_also_drains_that_stream_s_backlog(monkeypatch):
    """Consciously pinned (WB-C1's "wrinkle"): the press re-opened THIS stream on
    purpose, so the backlog already sitting in it is read too — deliberate
    reading of a closed session's stored pile, exactly what §4f sanctions. It
    re-opens nothing for any OTHER dead stream, and nothing automatic."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon._enqueue("ws", "prose", "backlog one", False)
    daemon._enqueue("ws", "prose", "backlog two", False)
    daemon.handle_message(_msg("where_am_i", "ws"))
    _drain(daemon, 6)
    assert any(s and "backlog one" in s for s in speaker.spoken)
    assert any(s and "backlog two" in s for s in speaker.spoken)
    # Byte-exact (the grammar-v2 reason): the mark precedes the Keyboard clause's
    # own content clause, and the backlog it counts is the backlog just drained.
    assert "Nothing playing. Keyboard: web 1, closed, 2 waiting." in speaker.spoken


# --- WB-C2 (MAJOR): catch-up's sanctioned recovery read, same conjunction ---
def test_catchup_on_a_dead_workspace_with_idle_voice_is_voiced(monkeypatch):
    """§4f: "reading a closed session's pile is a legitimate recovery act...
    catch-up still PROCEEDS." It proceeded silently — `_cue_dest` routes both
    the ack and the render into the dead target's own stream when speaker() is
    None. The frontier work happened and the user heard nothing."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch, folder="myrepo",
                                                tty="/dev/ttysM")
    for i in range(2):
        daemon.history.record("ws", "prose", "line {0}.".format(i))
    daemon.handle_message(_msg("catch_up", "ws"))
    _drain(daemon, 8)
    assert "Catching up 2 items in myrepo. That session closed." in speaker.spoken
    # summarizer=None -> straight to the digest floor; the RENDER must be voiced
    # too, not just the ack (it lands on the same stream one tick later).
    assert any(s and s.startswith("Summary unavailable.") for s in speaker.spoken)


# --- the third deliberate site, forced out by rule 3 (see the module docstring) ---
def test_whereami_on_a_dead_speaker_is_voiced(monkeypatch):
    """The readout about a dead VOICE session lands in that session's own stream,
    so rule 3 would hand the voice back and strand it — WB-C1's silence one
    branch over. ⌃⌘W is exactly when the user is asking about a fleet in this
    state, so the press sanctions the read; "playing, closed" is the answer.
    Task 5's pointer-mark tests pin the string; this pins that it is HEARD."""
    _liveness(monkeypatch, dead={"/dev/ttysA"})
    daemon, queue, speaker, sessions, config = make_daemon(foreground=None)
    sessions.register("api", cwd="/x/api")
    sessions.set_foreground("api")
    sessions.set_speaker("api")
    _ident(sessions, "api", "/dev/ttysA")
    daemon.handle_message(_msg("where_am_i", "api"))
    _drain(daemon, 3)
    assert "Voice and keyboard: api 1, playing, closed." in speaker.spoken


def _dying_fleet(monkeypatch):
    """A live session auto-adopted by keep-going, mid-drain, with a live third
    session holding its own backlog — the shape R-1 measured. Returns the set the
    test mutates to close B's terminal between two turns."""
    dead = set()
    _liveness(monkeypatch, dead)
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.register("B", cwd="/x/B"); _ident(sessions, "B", "/dev/ttysB")
    sessions.register("C", cwd="/x/C"); _ident(sessions, "C", "/dev/ttysC")
    for i in range(5):
        daemon._enqueue("B", "prose", "b-item-{0}".format(i), False)
    daemon._enqueue("C", "prose", "c-item", False)      # younger -> B is adopted first
    daemon._speak_loop_once()                            # A idle -> adopt B, speak b-item-0
    assert sessions.speaker() == "B"
    assert any(s and "b-item-0" in s for s in speaker.spoken)
    dead.add("/dev/ttysB")                               # the terminal closes MID-READ
    return daemon, speaker, sessions


# --- R-1 (probe H): §4d was selection-time only; a mid-drain death kept reading ---
def test_speaker_dying_mid_drain_is_silenced_and_its_queue_preserved(monkeypatch):
    """Spec §4d promises "a dead session's backlog is never auto-voiced", but the
    guard only ran at SELECTION: once adopted, the pop branch drained the whole
    pile with no liveness re-check — up to the full backlog_cap of 200 items read
    into an empty room. "I closed the terminal while it was still reading to me"
    is the likelier shape than "it was already closed"."""
    daemon, speaker, sessions = _dying_fleet(monkeypatch)
    _drain(daemon, 6)
    for i in range(1, 5):
        assert not any(s and "b-item-{0}".format(i) in s for s in speaker.spoken)
    # The pile is KEPT, not dropped: it stays discoverable via where-am-I (§4a)
    # and readable via catch-up (§4f) — the release silences, it never destroys.
    assert len(daemon._stream("B").queue) == 4
    assert sessions.speaker() == "C"                     # the live fleet keeps flowing
    assert any(s and "c-item" in s for s in speaker.spoken)


# --- R-1 (probe G): the naive "skip the pop" fix WEDGES the voice; release doesn't ---
def test_release_never_wedges_the_voice_on_a_dead_speaker(monkeypatch):
    """Skipping the pop leaves the dead stream non-empty, so _stream_quiescent stays
    False, so the keep-going gate never opens and the voice is stuck on a dead
    session forever while a live one waits. The voice must leave within one turn."""
    daemon, speaker, sessions = _dying_fleet(monkeypatch)
    daemon._speak_loop_once()                            # exactly ONE turn
    assert sessions.speaker() != "B"
    assert not any(s and "b-item-1" in s for s in speaker.spoken)


# --- composition: sanction beats release while it holds, and only while it holds ---
def test_sanctioned_read_survives_until_its_stream_empties_then_releases(monkeypatch):
    """The two halves meet here. A sanctioned dead read must NOT be cut off
    mid-way by rule 3 (it is the deliberate act rule 3 exists to protect), and
    the sanction must not outlive it — one press re-opens one stream once."""
    daemon, speaker, sessions = _dead_workspace(monkeypatch)
    daemon._enqueue("ws", "prose", "backlog one", False)
    daemon._enqueue("ws", "prose", "backlog two", False)
    daemon.handle_message(_msg("where_am_i", "ws"))
    assert sessions.speaker() == "ws"                    # the press took the idle voice
    for _ in range(3):                                   # backlog one, backlog two, readout
        daemon._speak_loop_once()
        assert sessions.speaker() == "ws", "a sanctioned read was cut off mid-pile"
    daemon._speak_loop_once()                            # the tick after it runs dry
    assert sessions.speaker() is None
    assert daemon._deliberate_dead_read is None          # one-shot: never a standing licence
