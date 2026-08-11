# tests/test_doctor_reachability.py
from unittest import mock

from sonari import cli
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey


def _row(on_path):
    sup = FakeSupervisor()
    sup.reachability_row = lambda: (
        ("reachability", True, "sonari is on your PATH") if on_path
        else ("reachability", False,
              "~/.local/bin is not on your PATH — 'sonari' will not run"))
    pb = fake_platform(supervisor=sup, hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.client.send", return_value={"ok": True}):
        return {n: (ok, d) for n, ok, d in cli.doctor.doctor()}["reachability"]


def test_on_path_is_healthy():
    assert _row(True)[0] is True


def test_off_path_fails_and_names_the_directory():
    ok, detail = _row(False)
    assert ok is False
    assert ".local/bin" in detail
