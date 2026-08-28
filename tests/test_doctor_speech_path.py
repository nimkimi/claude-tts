# tests/test_doctor_speech_path.py
import time

from unittest import mock

from sonari import cli, paths
from sonari.protocol import MsgType
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def _rows(status):
    pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.client.send", return_value=status):
        return {n: (ok, d) for n, ok, d in cli.doctor.doctor()}


def test_idle_daemon_is_healthy_however_long_it_has_been_quiet():
    ok, detail = _rows({"ok": True, "current_item": False,
                        "last_drain_age_s": 86400.0})["speech path"]
    assert ok is True
    assert "idle" in detail


def test_a_claimed_item_that_never_drains_is_a_wedge():
    ok, detail = _rows({"ok": True, "current_item": True,
                        "last_drain_age_s": 900.0})["speech path"]
    assert ok is False
    assert "wedged" in detail


def test_the_wedge_row_reports_the_quantity_it_actually_measured():
    """I2: `age` is last_drain_age_s — time since anything last DRAINED — but the
    row asserted "an utterance has been claimed for {age}s", a different
    quantity. After any quiet spell the drain age is already large the instant
    the next item is claimed, so the old wording overstated a fresh claim by the
    length of the preceding silence. STATUS carries no claim timestamp (adding
    one is a design change, booked for the next wave), so the honest row is the
    one that names the drain age and reports the claim as the condition it is."""
    ok, detail = _rows({"ok": True, "current_item": True,
                        "last_drain_age_s": 900.0})["speech path"]
    assert ok is False
    assert "claimed for" not in detail, detail
    assert "nothing has drained for 900s" in detail, detail


def test_a_claimed_item_draining_normally_is_healthy():
    ok, _ = _rows({"ok": True, "current_item": True,
                   "last_drain_age_s": 0.5})["speech path"]
    assert ok is True


def test_wedge_is_reported_even_though_the_socket_answers():
    """The exact lie D4 kills: the daemon replies, so 'daemon socket' is green,
    while the speech path is dead. If both rows agree, this test is worthless."""
    rows = _rows({"ok": True, "current_item": True, "last_drain_age_s": 900.0})
    assert rows["daemon socket"][0] is True
    assert rows["speech path"][0] is False


def test_unreachable_daemon_makes_the_row_fail_without_raising():
    with mock.patch("sonari.client.send", side_effect=OSError("down")):
        pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
        with mock.patch.object(cli, "_platform", lambda: pb):
            rows = {n: (ok, d) for n, ok, d in cli.doctor.doctor()}
    assert rows["speech path"][0] is False


# ---------------------------------------------------------------------------
# I3: a dead-audio incident (say/afplay exits nonzero, no reply this row's
# own STATUS check would otherwise catch) leaves a memo — SPEAK_FAIL_MEMO_PATH
# — that this row must surface. See src/sonari/daemon/host.py's
# _signal_speak_failure()/note_spoken() for the write/clear side and
# tests/test_speak_failure_memo.py for that half of the contract.
# ---------------------------------------------------------------------------


def _memo(tmp_path, age_s):
    """A speak-failure memo file aged to *age_s* seconds old."""
    import os
    memo = tmp_path / "speak.fail_memo"
    memo.write_text("")
    stale = time.time() - age_s
    os.utime(memo, (stale, stale))
    return memo


def test_a_fresh_speak_failure_memo_fails_the_row_with_what_and_when(tmp_path):
    memo = _memo(tmp_path, age_s=300)   # 5 minutes old, well inside the window
    with mock.patch.object(paths, "SPEAK_FAIL_MEMO_PATH", memo):
        ok, detail = _rows({"ok": True, "current_item": False,
                            "last_drain_age_s": 86400.0})["speech path"]
    assert ok is False
    assert "speech failure recorded" in detail
    assert "5m ago" in detail
    assert str(memo) in detail


def test_a_fresh_memo_dominates_even_a_normally_draining_claimed_item(tmp_path):
    """note_spoken() stamps last_drain on EVERY drain, completed or not — a
    daemon that fails every utterance instantly still looks like it is
    'draining normally' by that measure alone. The memo must win regardless,
    or this exact healthy-looking-but-broken shape survives undetected."""
    memo = _memo(tmp_path, age_s=10)
    with mock.patch.object(paths, "SPEAK_FAIL_MEMO_PATH", memo):
        ok, detail = _rows({"ok": True, "current_item": True,
                            "last_drain_age_s": 0.5})["speech path"]
    assert ok is False
    assert "speech failure recorded" in detail


def test_a_stale_speak_failure_memo_falls_through_to_the_ordinary_rows(tmp_path):
    """Past the freshness window the memo is presumed a long-dead ghost (the
    audio path may since have been fixed with no utterance yet to clear it) —
    the row reads exactly as if no memo existed."""
    fresh_s = cli.doctor.SPEAK_FAIL_FRESH_S
    memo = _memo(tmp_path, age_s=fresh_s + 60)
    with mock.patch.object(paths, "SPEAK_FAIL_MEMO_PATH", memo):
        ok, detail = _rows({"ok": True, "current_item": False,
                            "last_drain_age_s": 86400.0})["speech path"]
    assert ok is True
    assert "idle" in detail


def test_a_memo_with_a_future_mtime_is_not_treated_as_fresh(tmp_path):
    """Clock-skew guard, matching client.py's _memo_is_fresh() precedent: a
    negative age must never read as fresh."""
    import os
    memo = tmp_path / "speak.fail_memo"
    memo.write_text("")
    future = time.time() + 3600
    os.utime(memo, (future, future))
    with mock.patch.object(paths, "SPEAK_FAIL_MEMO_PATH", memo):
        ok, detail = _rows({"ok": True, "current_item": False,
                            "last_drain_age_s": 86400.0})["speech path"]
    assert ok is True
    assert "idle" in detail


def test_no_memo_is_byte_identical_to_todays_row(tmp_path):
    """The healthy branches must stay byte-identical when no memo exists."""
    memo = tmp_path / "speak.fail_memo"     # never created
    with mock.patch.object(paths, "SPEAK_FAIL_MEMO_PATH", memo):
        ok, detail = _rows({"ok": True, "current_item": False,
                            "last_drain_age_s": 86400.0})["speech path"]
    assert ok is True
    assert detail == "idle (nothing claimed by the speak loop)"


def test_speech_path_fails_when_live_streams_hold_and_nothing_drains():
    """Today this exact state renders GREEN: 'idle (nothing claimed by the
    speak loop)'. It is the state the confirmed assembler wedge produces -- an
    unterminated streamed block leaves the keep-going gate shut and every other
    session silenced indefinitely. Doctor calls that healthy."""
    from sonari.cli.doctor import _speech_path_row

    st = {
        "current_item": False,
        "voice_state": "flowing",
        "last_drain_age_s": 900.0,
        "sessions": [
            {"session": "A", "queue_len": 3, "stopped": False, "live": True},
        ],
    }
    name, ok, detail = _speech_path_row(st, memo_row=None)
    assert (name, ok) == ("speech path", False)
    assert "stuck, not idle" in detail
    assert "sonari install" in detail


def test_speech_path_stays_green_for_a_deliberate_mute():
    from sonari.cli.doctor import _speech_path_row

    st = {"current_item": False, "voice_state": "flowing",
          "last_drain_age_s": 900.0,
          "sessions": [{"session": "A", "queue_len": 3, "stopped": True,
                        "live": True}]}
    assert _speech_path_row(st, memo_row=None)[1] is True


def test_speech_path_stays_green_for_a_dead_session_backlog():
    from sonari.cli.doctor import _speech_path_row

    st = {"current_item": False, "voice_state": "flowing",
          "last_drain_age_s": 900.0,
          "sessions": [{"session": "A", "queue_len": 3, "stopped": False,
                        "live": False}]}
    assert _speech_path_row(st, memo_row=None)[1] is True


def test_speech_path_stays_green_under_quiet_hold_with_a_backlog():
    """`voice_state == "flowing"` is the ONLY clause standing between this row
    and a confident RED every time he asks for quiet with work queued.

    ⌃⌘S sets voice_state to "quiet-hold" GLOBALLY while stopping only the
    session it was pressed on (playback.py on_stop_session). Every OTHER live
    session keeps its queue and stays `stopped: False` -- and the keep-going
    gate (host.py's `_voice_state == "flowing"` check) holds them all, so
    nothing drains and last_drain_age_s climbs without bound. That is the wedge
    shape in every observable respect. The difference is intent, and voice_state
    is the only place intent is legible. Drop the clause and the row fires on a
    perfectly healthy daemon at the exact moment he asked it to be silent.

    (stopped-all is the same story but doubly guarded -- on_stop_all sets every
    stream's `stopped`, and host.py's SESSION_START arm stops later arrivals
    too -- so quiet-hold is where this clause is load-bearing ALONE.)
    """
    from sonari.cli.doctor import _speech_path_row

    st = {"current_item": False, "voice_state": "quiet-hold",
          "last_drain_age_s": 900.0,
          "sessions": [{"session": "A", "queue_len": 3, "stopped": False,
                        "live": True}]}
    assert _speech_path_row(st, memo_row=None)[1] is True, _speech_path_row(
        st, memo_row=None)


def test_speech_path_stays_green_when_the_backlog_drained_a_moment_ago():
    """The LOWER pin on WEDGE_HOLD_S. Live sessions holding a pile is the
    NORMAL state of a busy daemon between drains -- it is only a wedge once
    nothing has drained for WEDGE_HOLD_S. Without this, the threshold has no
    floor: WEDGE_HOLD_S could fall to 0.0 and every ordinary queued moment
    would read RED, with the rest of the suite still green."""
    from sonari.cli.doctor import _speech_path_row

    st = {"current_item": False, "voice_state": "flowing",
          "last_drain_age_s": 1.0,
          "sessions": [{"session": "A", "queue_len": 3, "stopped": False,
                        "live": True}]}
    assert _speech_path_row(st, memo_row=None)[1] is True, _speech_path_row(
        st, memo_row=None)


def test_a_never_drained_daemon_is_a_wedge_named_since_the_daemon_started():
    """`age is None` is the loop jamming on its FIRST item -- nothing has EVER
    drained, so there is no measured age to name. It is a wedge and must read
    RED (an `age is not None and ...` condition makes it green, which is the
    worst arm to lose: a daemon that never spoke once looks healthy).

    The sentence matters as much as the verdict. Rendering None as a number
    would say "nothing has been spoken for 0 minutes - the speak loop is stuck"
    in one breath, and he hears this row rather than reading it."""
    from sonari.cli.doctor import _speech_path_row

    st = {"current_item": False, "voice_state": "flowing",
          "last_drain_age_s": None,
          "sessions": [{"session": "A", "queue_len": 3, "stopped": False,
                        "live": True}]}
    name, ok, detail = _speech_path_row(st, memo_row=None)
    assert (name, ok) == ("speech path", False), detail
    assert "since the daemon started" in detail, detail
    assert "0 minutes" not in detail, detail


def test_speech_path_stays_green_for_a_restored_pile_not_yet_reconfirmed():
    """WHY `live` must fail CLOSED on a pending session -- recorded here
    because this row is where a widening would do its damage.

    `live` is `sessions.is_live(sid)`, which reports False for an SP6-RESTORED
    session: one recovered from disk and not yet reconfirmed this lifetime. A
    restored pile is a backlog on a session that may no longer have a terminal
    behind it, and it drains for nobody. That is precisely the false positive
    this field excludes.

    If a future editor "simplifies" the producer to `liveness(sid) != "dead"`
    -- which fails OPEN on pending -- restored piles would start reading
    `live: True` and this row would fire a confident RED on every daemon
    restart with saved state. The producer side is pinned by
    tests/test_status_diagnostics.py::test_live_tracks_liveness_itself_not_a_correlate;
    this is the consumer side of the same contract.
    """
    from sonari.cli.doctor import _speech_path_row

    st = {"current_item": False, "voice_state": "flowing",
          "last_drain_age_s": 900.0,
          "sessions": [{"session": "P", "queue_len": 7, "stopped": False,
                        "live": False}]}
    assert _speech_path_row(st, memo_row=None)[1] is True, _speech_path_row(
        st, memo_row=None)


def test_the_voice_row_is_wired_into_doctor():
    """Pins the WIRE-IN, not the logic: every other _voice_row test calls the
    function directly, so a forgotten `results.append(_voice_row(st))` leaves a
    correct, fully tested, completely dead function -- and a doctor with FEWER
    rows than before, because the enhanced-voice row is deleted in the same
    step. An empty voice short-circuits to the system-default arm before any
    platform touch, so this stays hermetic with no new mocks."""
    rows = _rows({"ok": True, "current_item": False,
                  "last_drain_age_s": 1.0, "voice": ""})
    assert "voice" in rows
    assert rows["voice"] == (True, "system default")


# ---------------------------------------------------------------------------
# The row's own false-positive analysis was WRONG about voice_state. Three
# ratified "deliberate re-engage" lifts (navigation.py's crossed nav,
# playback.py's ctrl-cmd-D, both crossed and within-session) set
# voice_state="flowing" and then park the voice ON a stopped stream via
# sessions.focus(). The enum says flowing; the loop holds every tick; every
# other live session starves. Past WEDGE_HOLD_S the row fired a confident RED
# and named a DESTRUCTIVE remedy ("Restart it: sonari install") after two of
# the branch's own acceptance rows. The producers are ratified
# (tests/test_sp3_lifts.py::test_jump_decision_lifts_hold pins the R5 lift),
# so the fix is here: STATUS carries `speaker_held` and the row honours it.
# ---------------------------------------------------------------------------


def test_speech_path_stays_green_when_the_speakers_own_stream_is_held():
    """The consumer half: `flowing` plus a held SPEAKER is a mute, not a wedge.

    Byte-identical to test_speech_path_fails_when_live_streams_hold_and_nothing
    _drains except for `speaker_held` -- that one field is the whole difference
    between "he muted the session the voice is on" and "the speak loop died"."""
    from sonari.cli.doctor import _speech_path_row

    st = {
        "current_item": False,
        "voice_state": "flowing",
        "last_drain_age_s": 900.0,
        "speaker_held": True,
        "sessions": [
            {"session": "A", "queue_len": 3, "stopped": False, "live": True},
        ],
    }
    name, ok, detail = _speech_path_row(st, memo_row=None)
    assert (name, ok) == ("speech path", True), detail
    # Pin the HELD arm, not merely a green verdict: the idle arm two lines
    # below is also green, so `ok is True` alone would pass on a row that
    # never consulted speaker_held at all.
    assert "held" in detail, detail
    assert "sonari install" not in detail, detail


def test_speech_path_still_reds_when_the_speaker_is_not_held():
    """The genuine assembler wedge keeps its RED. There the speak loop is stuck
    on a stream that is NOT stopped, so speaker_held is False and every clause
    of the wedge condition still holds. Without this the fix could be `return
    green` and the row would be worth nothing."""
    from sonari.cli.doctor import _speech_path_row

    st = {
        "current_item": False,
        "voice_state": "flowing",
        "last_drain_age_s": 900.0,
        "speaker_held": False,
        "sessions": [
            {"session": "A", "queue_len": 3, "stopped": False, "live": True},
        ],
    }
    name, ok, detail = _speech_path_row(st, memo_row=None)
    assert (name, ok) == ("speech path", False), detail
    assert "stuck, not idle" in detail, detail


def test_the_doctor_is_green_after_ctrl_cmd_D_re_engages_onto_a_muted_speaker():
    """End to end, through the real daemon and the real STATUS reply.

    ctrl-cmd-S on A, then ctrl-cmd-D on A: the physical state is identical
    across those two presses -- same speaker, same stopped flag, same starved
    backlog on B -- and only the enum moves (quiet-hold -> flowing). Before
    `speaker_held` the first press rendered GREEN and the second RED, which is
    the proof the row was reporting the enum rather than the daemon's health.

    last_drain_age_s is forced past WEDGE_HOLD_S because the wedge shape is
    defined by elapsed time and the test must not sleep for five minutes.
    """
    from sonari.cli.doctor import _speech_path_row

    daemon, _, speaker, sessions, _ = make_daemon(foreground="A")
    sessions.register("A", cwd="/x/alpha")
    sessions.register("B", cwd="/x/bravo")
    sessions.set_speaker("A")
    daemon._enqueue("A", "permission", "A question needs your answer.", True)
    daemon._enqueue("B", "prose", "bravo keeps talking", False)

    daemon.handle_message(_msg(MsgType.STOP_SESSION, "A"))
    for _ in range(20):
        daemon._speak_loop_once()
    st = daemon.handle_message(_msg(MsgType.STATUS, "A"))
    assert st["voice_state"] == "quiet-hold", st
    assert _speech_path_row({**st, "last_drain_age_s": 400.0},
                            memo_row=None)[1] is True

    daemon.handle_message(_msg(MsgType.JUMP_DECISION, "A"))
    for _ in range(20):
        daemon._speak_loop_once()
    st = daemon.handle_message(_msg(MsgType.STATUS, "A"))
    # The state that used to false-RED: the lift landed, the speaker is muted.
    assert st["voice_state"] == "flowing", st
    assert sessions.speaker() == "A", sessions.speaker()
    assert daemon._streams["A"].stopped is True, st["sessions"]
    assert st["speaker_held"] is True, st
    name, ok, detail = _speech_path_row({**st, "last_drain_age_s": 400.0},
                                        memo_row=None)
    assert (name, ok) == ("speech path", True), detail
    assert "sonari install" not in detail, detail
