# tests/test_doctor_fault_log.py
from unittest import mock

from sonari import cli
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey


def _row(tmp_path, contents=None):
    log = tmp_path / "faulthandler.log"
    if contents is not None:
        log.write_text(contents, encoding="utf-8")
    pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.paths.FAULTLOG_PATH", log), \
         mock.patch("sonari.client.send", return_value={"ok": True}):
        return {n: (ok, d) for n, ok, d in cli.doctor.doctor()}["fault log"]


def test_no_log_is_clean(tmp_path):
    ok, detail = _row(tmp_path)
    assert ok is True and "no crash" in detail


def test_armed_line_only_is_clean(tmp_path):
    ok, _ = _row(tmp_path, "=== faulthandler armed: pid 42 ===\n")
    assert ok is True


def test_a_dump_after_the_armed_line_is_reported(tmp_path):
    ok, detail = _row(tmp_path,
                      "=== faulthandler armed: pid 42 ===\n"
                      "Current thread 0x00007ff8 (most recent call first):\n"
                      '  File "tts.py", line 194 in speak\n')
    assert ok is False
    assert "crash" in detail
