# tests/test_macos_uninstall_rc.py
"""'launchctl unload's exit code is not a signal on real macOS: probed directly
(throwaway labels, never Sonari's) it is 0 for BOTH a genuine unload and an
already-unloaded/failed one -- the only difference is text on stderr, which the
`launchctl()` helper's subprocess.call does not capture. So uninstall() must not
key its message off unload's rc; it must ask launchd directly, afterward, whether
the label is still registered -- the same signal daemon_is_launchd_job() already
trusts (`launchctl(["list", LABEL]) == 0`).

These tests never invoke real launchctl -- `launchctl` is mocked per-call by args,
keyed on whether the call is the "unload" or the "list" verification.
"""
from unittest import mock

import pytest

from sonari.platform.macos import supervisor as sup_mod

pytestmark = pytest.mark.skipif(
    not hasattr(sup_mod, "MacSupervisorBackend"),
    reason="macOS supervisor only")


def _launchctl_stub(unload_rc, list_rc):
    """A launchctl(args, timeout=None) stub keyed on the subcommand, not a single
    blanket return value -- the whole point being tested is that uninstall() must
    NOT infer removal from unload_rc alone."""
    def _stub(args, timeout=None):
        if args[0] == "unload":
            return unload_rc
        if args[0] == "list":
            return list_rc
        raise AssertionError("unexpected launchctl call: {0}".format(args))
    return _stub


def test_a_failed_unload_leaves_the_label_registered_and_warns(capsys, tmp_path):
    plist = tmp_path / "agent.plist"
    plist.write_text("<plist/>", encoding="utf-8")
    sup = sup_mod.MacSupervisorBackend()
    # unload rc=1 (a real failure) AND the post-unload `list` check confirms the
    # label is STILL registered with launchd.
    with mock.patch.object(sup_mod, "LAUNCH_AGENT_PATH", str(plist)), \
         mock.patch.object(sup, "launchctl", side_effect=_launchctl_stub(1, 0)):
        sup.uninstall()
    out = capsys.readouterr().out
    assert "warning" in out.lower()
    assert "Removed LaunchAgent" not in out
    # Failure must not abort uninstall: the plist file is still deleted.
    assert not plist.exists()


def test_unload_rc_zero_does_not_by_itself_prove_removal(capsys, tmp_path):
    """The decisive case: real launchctl returns rc=0 from unload EVEN WHEN the
    label never actually got unregistered. Trusting unload's own rc==0 (the
    brief's original approach) would print "Removed LaunchAgent" here -- a lie.
    Only the post-unload `list` check may decide the message."""
    plist = tmp_path / "agent.plist"
    plist.write_text("<plist/>", encoding="utf-8")
    sup = sup_mod.MacSupervisorBackend()
    with mock.patch.object(sup_mod, "LAUNCH_AGENT_PATH", str(plist)), \
         mock.patch.object(sup, "launchctl", side_effect=_launchctl_stub(0, 0)):
        sup.uninstall()
    out = capsys.readouterr().out
    assert "warning" in out.lower()
    assert "Removed LaunchAgent" not in out
    assert not plist.exists()


def test_a_confirmed_unload_reports_removal(capsys, tmp_path):
    plist = tmp_path / "agent.plist"
    plist.write_text("<plist/>", encoding="utf-8")
    sup = sup_mod.MacSupervisorBackend()
    # unload rc=0 AND `list` confirms the label is gone (non-zero == not found).
    with mock.patch.object(sup_mod, "LAUNCH_AGENT_PATH", str(plist)), \
         mock.patch.object(sup, "launchctl", side_effect=_launchctl_stub(0, 1)):
        sup.uninstall()
    out = capsys.readouterr().out
    assert "Removed LaunchAgent" in out
    assert "warning" not in out.lower()


def test_launchctl_missing_degrades_to_reporting_removal(capsys, tmp_path):
    """No launchctl binary at all (e.g. a dev box) must degrade to the previous
    unconditional-success behaviour, not newly warn on every uninstall."""
    plist = tmp_path / "agent.plist"
    plist.write_text("<plist/>", encoding="utf-8")
    sup = sup_mod.MacSupervisorBackend()
    with mock.patch.object(sup_mod, "LAUNCH_AGENT_PATH", str(plist)), \
         mock.patch.object(sup, "launchctl", return_value=1):  # FileNotFoundError path
        sup.uninstall()
    out = capsys.readouterr().out
    assert "Removed LaunchAgent" in out


def test_launchctl_timeout_is_bounded_and_never_raises():
    """The new post-unload verification call must not be able to hang uninstall
    forever; a TimeoutExpired is caught and treated as failure (rc=1), matching
    the existing FileNotFoundError contract of always returning an int."""
    import subprocess as sp
    sup = sup_mod.MacSupervisorBackend()
    with mock.patch.object(
            sup_mod.subprocess, "call",
            side_effect=sp.TimeoutExpired(cmd="launchctl", timeout=5.0)):
        rc = sup.launchctl(["list", "com.sonari.speechd"], timeout=5.0)
    assert rc == 1
