# tests/test_macos_raise.py
import sys

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS raise backend", allow_module_level=True)

from sonari.platform.macos.raiser import MacRaiseBackend
from sonari.sessions import Identity


class FakeProc:
    def __init__(self, rc):
        self.returncode = rc


def _backend(rc=0, exists=True, recorder=None):
    be = MacRaiseBackend()
    be._helper_exists = lambda: exists
    def run(argv, timeout=None):
        if recorder is not None:
            recorder.append(argv)
        return FakeProc(rc)
    be._run = run
    return be


def test_supports_terminal_needs_tty():
    be = MacRaiseBackend()
    assert be.supports(Identity("Apple_Terminal", tty="/dev/ttys1")) is True
    assert be.supports(Identity("Apple_Terminal", tty="")) is False


def test_supports_iterm_needs_session_id():
    be = MacRaiseBackend()
    assert be.supports(Identity("iTerm.app", iterm_session_id="w0:ID")) is True
    assert be.supports(Identity("iTerm.app", iterm_session_id="")) is False


def test_supports_unknown_terminal_false():
    assert MacRaiseBackend().supports(Identity("Ghostty", tty="/dev/ttys1")) is False


def test_raise_terminal_execs_helper_with_tty():
    rec = []
    be = _backend(rc=0, recorder=rec)
    assert be.raise_session(Identity("Apple_Terminal", tty="/dev/ttys5")) is True
    assert rec[0][0].endswith("sonari-raise")
    assert rec[0][1] == "/dev/ttys5"


def test_raise_terminal_nonzero_is_false():
    assert _backend(rc=1).raise_session(Identity("Apple_Terminal", tty="/dev/ttys5")) is False


def test_raise_missing_helper_is_false():
    assert _backend(exists=False).raise_session(
        Identity("Apple_Terminal", tty="/dev/ttys5")) is False


def test_raise_iterm_opens_reveal_url():
    rec = []
    be = _backend(rc=0, recorder=rec)
    assert be.raise_session(Identity("iTerm.app", iterm_session_id="w0t0p0:ID")) is True
    assert rec[0][0] == "open"
    assert rec[0][1] == "iterm2:///reveal?sessionid=w0t0p0:ID"


def test_check_grant_maps_exit_codes():
    assert _backend(rc=0).check_grant() == "granted"
    assert _backend(rc=3).check_grant() == "denied"
    assert _backend(rc=4).check_grant() == "unknown"
    assert _backend(exists=False).check_grant() == "unknown"


def test_doctor_rows_shape():
    rows = _backend(rc=0).doctor_rows()
    assert all(len(r) == 3 for r in rows)
