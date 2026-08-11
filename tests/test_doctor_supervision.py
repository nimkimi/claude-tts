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


def test_a_failing_supervision_probe_never_reports_a_live_daemon_as_dead():
    """The PING already proved the daemon is REACHABLE. If the supervision probe
    then raises (launchctl unreadable, PermissionError — anything but the
    FileNotFoundError the helper handles), the row must not undo that proof: it
    said "not reachable: ... (run 'sonari install')", sending an eyes-free user
    to reinstall a working system. Supervision is advisory; an unknown answer
    must degrade to "reachable, supervision unknown", never to a hard fail."""
    def _boom():
        raise PermissionError("launchctl: Operation not permitted")

    sup = FakeSupervisor()
    sup.daemon_is_launchd_job = _boom
    pb = fake_platform(supervisor=sup, hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.client.send", return_value={"ok": True}):
        rows = {n: (ok, d) for n, ok, d in cli.doctor.doctor()}
    ok, detail = rows["daemon socket"]
    assert ok is True, "a reachable daemon must not be reported as unreachable"
    assert "reachable" in detail
    assert "sonari install" not in detail
