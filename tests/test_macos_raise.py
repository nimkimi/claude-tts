# tests/test_macos_raise.py
import os
import sys
from unittest import mock

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS raise backend", allow_module_level=True)

from sonari import paths
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


def test_raise_iterm_execs_helper_with_iterm_flag():
    # iTerm2's reveal-URL path lands on the wrong session on macOS Tahoe; the
    # iTerm branch now execs sonari-raise --iterm <full session id> (the helper
    # strips to the bare GUID itself), mirroring the Terminal branch.
    rec = []
    be = _backend(rc=0, recorder=rec)
    assert be.raise_session(Identity("iTerm.app", iterm_session_id="w0t0p0:GUID")) is True
    assert rec[0][0].endswith("sonari-raise")
    assert rec[0][1] == "--iterm"
    assert rec[0][2] == "w0t0p0:GUID"  # full id passed; Swift does the bare-GUID strip


def test_raise_iterm_nonzero_is_false():
    assert _backend(rc=1).raise_session(
        Identity("iTerm.app", iterm_session_id="w0t0p0:GUID")) is False


def test_raise_iterm_missing_helper_is_false():
    assert _backend(exists=False).raise_session(
        Identity("iTerm.app", iterm_session_id="w0t0p0:GUID")) is False


def test_check_grant_maps_exit_codes():
    assert _backend(rc=0).check_grant() == "granted"
    assert _backend(rc=3).check_grant() == "denied"
    assert _backend(rc=4).check_grant() == "unknown"
    assert _backend(exists=False).check_grant() == "unknown"


def test_doctor_rows_shape():
    rows = _backend(rc=0).doctor_rows()
    assert all(len(r) == 3 for r in rows)


# --- build() grant-preserving hash-skip ----------------------------------
# build() mirrors MacHotkeyBackend.build(): it must NOT recompile when the swift
# source is unchanged, because a rebuild changes the binary's cdhash and silently
# drops the Automation grant. These tests mock swiftc + the filesystem (tmp paths)
# so no real compile runs and the real ~/.sonari is never touched.

def test_build_raise_missing_swiftc_returns_false():
    with mock.patch("shutil.which", return_value=None):
        ok, detail = MacRaiseBackend().build()
    assert ok is False and "swiftc" in detail.lower()


def test_build_raise_first_build_writes_srchash_and_returns_bin_path(tmp_path, monkeypatch):
    binp = tmp_path / "sonari-raise"
    monkeypatch.setattr(paths, "RAISE_BIN_PATH", binp)
    monkeypatch.setattr(paths, "SONARI_DIR", tmp_path)
    with mock.patch("shutil.which", return_value="/usr/bin/swiftc"), \
         mock.patch("subprocess.call", return_value=0) as call:
        ok, detail = MacRaiseBackend().build()
    assert ok is True and detail == str(binp)
    assert call.call_count == 1
    args = call.call_args.args[0]
    assert args[0] == "swiftc"
    assert args[1].endswith(os.path.join("hotkeyd", "sonari-raise.swift"))
    assert args[-1] == str(binp)
    # the source hash is recorded so the next build can skip-rebuild
    assert (tmp_path / ".raise.srchash").exists()


def test_build_raise_nonzero_returncode_is_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RAISE_BIN_PATH", tmp_path / "sonari-raise")
    monkeypatch.setattr(paths, "SONARI_DIR", tmp_path)
    with mock.patch("shutil.which", return_value="/usr/bin/swiftc"), \
         mock.patch("subprocess.call", return_value=1):
        ok, _ = MacRaiseBackend().build()
    assert ok is False


def test_build_raise_skips_recompile_when_source_unchanged(tmp_path, monkeypatch):
    binp = tmp_path / "sonari-raise"
    binp.write_text("pretend-built binary")        # binary already present
    monkeypatch.setattr(paths, "RAISE_BIN_PATH", binp)
    monkeypatch.setattr(paths, "SONARI_DIR", tmp_path)
    with mock.patch("shutil.which", return_value="/usr/bin/swiftc"), \
         mock.patch("subprocess.call", return_value=0) as call1:
        ok1, _ = MacRaiseBackend().build()         # first build records the hash
    assert ok1 is True and call1.call_count == 1
    with mock.patch("shutil.which", return_value="/usr/bin/swiftc"), \
         mock.patch("subprocess.call", return_value=0) as call2:
        ok2, detail2 = MacRaiseBackend().build()   # unchanged source -> skip swiftc
    assert ok2 is True and call2.call_count == 0
    assert "unchanged" in detail2.lower()
    assert "automation grant" in detail2.lower()   # the load-bearing reason


def test_build_raise_recompiles_when_source_changes(tmp_path, monkeypatch):
    binp = tmp_path / "sonari-raise"
    binp.write_text("pretend-built binary")
    # a stale hash from old source -> the current source no longer matches -> rebuild
    (tmp_path / ".raise.srchash").write_text("a-stale-hash-from-old-source")
    monkeypatch.setattr(paths, "RAISE_BIN_PATH", binp)
    monkeypatch.setattr(paths, "SONARI_DIR", tmp_path)
    with mock.patch("shutil.which", return_value="/usr/bin/swiftc"), \
         mock.patch("subprocess.call", return_value=0) as call:
        ok, _ = MacRaiseBackend().build()
    assert ok is True and call.call_count == 1
