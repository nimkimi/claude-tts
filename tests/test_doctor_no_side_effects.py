# tests/test_doctor_no_side_effects.py
"""doctor() observes; it never repairs.

A diagnostic that restarts what it measures cannot measure it, and it would
resurrect a daemon the user just uninstalled. This is an INVARIANT PIN: the
property already holds (spec 2.1), and this test keeps it holding.
"""
from unittest import mock

from sonari import cli
from tests._fakeplatform import fake_platform, FakeSupervisor, FakeHotkey

# paths.KOKORO_VENV is bound from the real Path.home() at import time and is NOT
# among the constants conftest's autouse _isolate_sonari_dir fixture repoints (see
# tests/conftest.py). On a machine with neural voices actually installed, leaving
# it unmocked makes the "neural voices" doctor row exec the real ~/.sonari/venv
# python via subprocess.check_output -- a genuine, machine-dependent subprocess
# spawn that has nothing to do with the invariant these tests pin. Force the "not
# installed" branch, same as test_cli_doctor.py's neural-row tests do, so the
# result is deterministic on every machine.


def test_doctor_spawns_no_processes():
    pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.client.send", return_value={"ok": True}), \
         mock.patch("sonari.kokoro_provision.neural_enabled", return_value=False), \
         mock.patch("subprocess.Popen") as popen:
        cli.doctor.doctor()
    assert popen.call_count == 0, (
        f"doctor spawned {popen.call_count} process(es); it must only observe")


def test_doctor_never_calls_ensure_running():
    # client.py does `from sonari.daemon import ensure_running`, a direct import
    # that binds its own name in sonari.client's namespace at import time.
    # Patching sonari.daemon.ensure_running (the origin) does NOT intercept calls
    # made through that local binding -- the codebase's own client tests patch
    # sonari.client.ensure_running for the same reason (see test_client_ensure.py).
    pb = fake_platform(supervisor=FakeSupervisor(), hotkey=FakeHotkey())
    with mock.patch.object(cli, "_platform", lambda: pb), \
         mock.patch("sonari.client.send", return_value={"ok": True}), \
         mock.patch("sonari.kokoro_provision.neural_enabled", return_value=False), \
         mock.patch("sonari.client.ensure_running") as ensure:
        cli.doctor.doctor()
    ensure.assert_not_called()
