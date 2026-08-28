"""R5's two new STATUS fields. Read-only diagnostics; no behaviour change.

`keepalive` on the wire is a STRING (disabled|degraded|running|hold|idle). The
age exists -- _players holds (proc, spawned_at) against a monotonic clock --
but nothing exposed it, so an orphaned player reads as "running" and the row
is green.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md 6.0.
"""
from tests.daemon_helpers import make_daemon
from tests.test_keepalive_manager import FakeProc
from sonari.protocol import MsgType
from sonari.sessions import Identity


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _pin_clock(ka, monkeypatch, now):
    monkeypatch.setattr(ka, "_clock", lambda: now)


def _live():
    return FakeProc(["afplay", "-"])


def _exited(rc=0):
    p = FakeProc(["afplay", "-"])
    p.die(rc)
    return p


def test_oldest_player_age_is_none_with_no_players():
    daemon, _, _, _, _ = make_daemon()
    assert daemon.keepalive.oldest_player_age() is None


def test_oldest_player_age_reads_the_oldest_spawn(monkeypatch):
    """The 950 entry is appended FIRST so list order disagrees with spawn
    order: `_players[0][1]` would read 50.0 here, so this pins the `min` and
    not the frontmost element."""
    daemon, _, _, _, _ = make_daemon()
    ka = daemon.keepalive
    _pin_clock(ka, monkeypatch, 1000.0)
    with ka._lock:
        ka._players.append((_live(), 950.0))
        ka._players.append((_live(), 700.0))
    assert ka.oldest_player_age() == 300.0


def test_oldest_player_age_ignores_players_that_have_already_EXITED(monkeypatch):
    """The whole point of the field, and the doctor row that consumes it.

    Exited players are pruned only by tick(), whose only caller sits behind a
    BLOCKING speak() -- so during every utterance `_players` accumulates
    corpses. A reduction over them makes the HEALTHY overlap chain (a 295s-old
    player A that just exited, its successor B spawned moments ago) read as a
    305s+ stall, turning the keepalive row RED on a working system and telling
    him to tear the working chain down. Only the LIVE player holds the device,
    so only the live player's age is the answer.
    """
    daemon, _, _, _, _ = make_daemon()
    ka = daemon.keepalive
    _pin_clock(ka, monkeypatch, 1000.0)
    with ka._lock:
        ka._players.append((_exited(), 400.0))     # A: spawned 600s ago, dead
        ka._players.append((_live(), 995.0))       # B: the live overlap, 5s old
    assert ka.oldest_player_age() == 5.0


def test_oldest_player_age_is_none_when_every_player_has_EXITED(monkeypatch):
    """Reducing over an empty filtered list must not raise. The state is real:
    pruning halts for the duration of an utterance, so a whole chain can be
    dead-but-unpruned. Nothing is holding the audio device, which is exactly
    what None means to _keepalive_row -- it falls through to the state string.
    """
    daemon, _, _, _, _ = make_daemon()
    ka = daemon.keepalive
    _pin_clock(ka, monkeypatch, 1000.0)
    with ka._lock:
        ka._players.append((_exited(), 400.0))
        ka._players.append((_exited(), 600.0))
    assert ka.oldest_player_age() is None


def test_status_carries_the_keepalive_age_and_per_session_liveness():
    """`live` must TRACK is_live, not merely be a bool. Presence-and-type
    assertions are satisfied by a hard-coded True, and a literal here turns
    Task 13's wedge row RED on a dead session's backlog -- the exact case the
    field exists to exclude. So drive one genuinely dead session through the
    wire: a captured tty whose device node does not exist is `dead` by
    ttyutil.tty_alive, while a session with no identity at all stays fail-open
    `live`.
    """
    daemon, _, _, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("D", cwd="/x/dead")
    sessions.set_identity("D", Identity(term_program="Apple_Terminal",
                                        tty="/dev/ttys-does-not-exist"))
    daemon._stream("D")
    st = daemon.handle_message(_msg(MsgType.STATUS, "A"))
    assert "keepalive_oldest_player_age_s" in st
    assert st["sessions"], st
    live = {s["session"]: s["live"] for s in st["sessions"]}
    assert live == {"A": True, "D": False}, st["sessions"]


def test_live_tracks_liveness_itself_not_a_correlate():
    """`live` must be is_live(sid) itself, not something that agrees with it on
    one fixture. B is LIVE but NOT foreground (a foreground()-keyed producer
    reads it dead), and P is a restored/pending session that is_live() fails
    CLOSED on (a `liveness() != "dead"` producer reads it live)."""
    daemon, _, _, sessions, _ = make_daemon(foreground="A")
    sessions.load_state({"P": {"folder": "p", "number": 1}})   # -> provisional
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/beta")
    daemon._stream("B")
    daemon._stream("P")
    st = daemon.handle_message(_msg(MsgType.STATUS, "A"))
    live = {s["session"]: s["live"] for s in st["sessions"]}
    assert live == {"A": True, "B": True, "P": False}, st["sessions"]


# ---------------------------------------------------------------------------
# `speaker_held`: the field the doctor's speech-path row needs to tell a
# ratified re-engage-onto-a-mute from a dead speak loop. See
# tests/test_doctor_speech_path.py for the consumer half.
# ---------------------------------------------------------------------------


def test_speaker_held_is_true_when_the_voice_is_parked_on_a_muted_stream():
    daemon, _, _, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.set_speaker("A")
    daemon._stream("A").stopped = True
    st = daemon.handle_message(_msg(MsgType.STATUS, "A"))
    assert st["speaker_held"] is True, st


def test_speaker_held_reads_the_speaker_not_any_stopped_session():
    """The discriminator. B is muted and A — the voice owner — is not, so a
    producer written as "does any session read stopped" agrees with a correct
    one on the fixture above and only diverges here. It matters because that
    weaker producer would blind the wedge row on a genuinely stuck loop
    whenever one unrelated session happened to be muted, which is a common
    state for him."""
    daemon, _, _, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    sessions.set_speaker("A")
    daemon._stream("A")
    daemon._stream("B").stopped = True
    st = daemon.handle_message(_msg(MsgType.STATUS, "A"))
    assert st["speaker_held"] is False, st


def test_speaker_held_is_false_with_no_speaker_at_all():
    """Fork-2 releases the voice (set_speaker(None)) rather than parking it, and
    the assembler wedge itself can be speakerless. Both must stay detectable, so
    "no speaker" is never "held"."""
    daemon, _, _, sessions, _ = make_daemon(foreground=None)
    sessions.register("A", cwd="/x/alpha")
    daemon._stream("A").stopped = True
    assert sessions.speaker() is None, sessions.speaker()
    st = daemon.handle_message(_msg(MsgType.STATUS, "A"))
    assert st["speaker_held"] is False, st


def test_speaker_held_reads_the_speaker_not_the_foreground():
    """The second discriminator, and it was NOT free: a producer keyed on
    `foreground()` survived the whole suite until this pair existed, because
    every other fixture here leaves the two coincident.

    SP2 diverges them by design -- keep-going advances the VOICE (speaker)
    while the workspace stays where he last acted -- and `foreground` is
    already on the wire, so reaching for it is the natural wrong turn. Both
    directions are pinned: a muted speaker behind a live workspace must read
    True, and a live speaker behind a muted workspace must read False (that
    second one is the dangerous direction -- it would blind the wedge row on a
    genuinely stuck loop).
    """
    daemon, _, _, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    sessions.set_speaker("B")                  # voice on B, workspace still A
    daemon._stream("A")
    daemon._stream("B").stopped = True
    st = daemon.handle_message(_msg(MsgType.STATUS, "A"))
    assert sessions.foreground() == "A" and sessions.speaker() == "B", st
    assert st["speaker_held"] is True, st

    daemon2, _, _, sessions2, _ = make_daemon(foreground="A")
    sessions2.register("A", cwd="/x/alpha")
    sessions2.register("B", cwd="/x/bravo")
    sessions2.set_speaker("B")
    daemon2._stream("A").stopped = True         # the WORKSPACE is the muted one
    daemon2._stream("B")
    st2 = daemon2.handle_message(_msg(MsgType.STATUS, "A"))
    assert st2["speaker_held"] is False, st2
