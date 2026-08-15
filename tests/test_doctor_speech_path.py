# tests/test_doctor_speech_path.py
import time

from unittest import mock

from sonari import cli, paths
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey


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
