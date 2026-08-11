# tests/test_doctor_restore_health.py
import json
from unittest import mock

from sonari import cli
from sonari.daemon import persistence
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey


def _rows(tmp_path, payload=None, write=True):
    state = tmp_path / "state.json"
    if write:
        state.write_text(json.dumps(payload), encoding="utf-8")
    pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sonari.client.send", return_value={"ok": True}):
        return {n: (ok, d) for n, ok, d in cli.doctor.doctor()}


def test_absent_state_is_reported_but_not_a_failure(tmp_path):
    ok, detail = _rows(tmp_path, write=False)["restore health"]
    assert ok is True
    assert "no saved state" in detail


def test_unparseable_state_fails_loudly(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{not json", encoding="utf-8")
    pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sonari.client.send", return_value={"ok": True}):
        rows = {n: (ok, d) for n, ok, d in cli.doctor.doctor()}
    assert rows["restore health"][0] is False


def test_version_mismatch_warns_that_the_pile_will_be_dropped(tmp_path):
    bad = {"version": persistence.STATE_VERSION + 1, "sessions": {}}
    ok, detail = _rows(tmp_path, bad)["restore health"]
    assert ok is False
    assert "dropped" in detail


def test_healthy_state_reports_the_session_count(tmp_path):
    good = {"version": persistence.STATE_VERSION,
            "sessions": {"a": {}, "b": {}}}
    ok, detail = _rows(tmp_path, good)["restore health"]
    assert ok is True
    assert "2" in detail
