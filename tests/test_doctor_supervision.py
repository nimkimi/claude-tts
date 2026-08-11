# tests/test_doctor_supervision.py
from unittest import mock

from sonari import cli
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey


def _detail(job_loaded):
    sup = FakeSupervisor()
    sup.daemon_is_launchd_job = lambda: job_loaded
    pb = fake_platform(supervisor=sup, hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.client.send", return_value={"ok": True}):
        rows = {n: (ok, d) for n, ok, d in cli.doctor.doctor()}
    return rows["daemon socket"]


def test_supervised_daemon_says_so():
    ok, detail = _detail(True)
    assert ok is True
    assert "launchd" in detail


def test_orphan_is_named_but_does_not_fail_the_row():
    ok, detail = _detail(False)
    assert ok is True                      # it works; it just cannot be stopped
    assert "orphan" in detail
