# tests/test_doctor_speech_path.py
from unittest import mock

from sonari import cli
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
