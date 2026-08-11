"""R1: the hotkeyd row must prove two facts, not one — the process is alive
AND its witness alarm is armed (alarmEnabled true, alarmAsset readable). A
hotkeyd that is running with a disabled or unplayable alarm is a watchdog
that cannot bark; presence alone was the old (insufficient) check.

Container shape: HOTKEYD_RESOLVED_PATH holds a JSON ARRAY — bindings plus a
witness entry — exactly as keymap.write_resolved() emits it
(`resolve_keymap(keymap) + [_witness_entry()]`, keymap.py:423-432). The
witness entry sits IN that array with `"action": "witness_config"`; it is
NOT a dict keyed by "witness_config". hotkeyd/sonari-hotkeyd.swift:180-191
reads the same array shape (`parsed as? [[String: Any]]`, matched by
`obj["action"]`), and the pre-existing "hotkeyd resolved keymap" doctor row
(supervisor.py:308-317) already asserts `isinstance(parsed, list)`.
"""
import json
from unittest import mock

import pytest

from sonari.platform.macos import supervisor as sup_mod

pytestmark = pytest.mark.skipif(not hasattr(sup_mod, "MacSupervisorBackend"),
                                reason="macOS supervisor only")

# A binding entry exactly as resolve_keymap() emits one (keymap.py:328-333) —
# used so the array-shape tests below mirror write_resolved()'s real output
# (bindings + witness entry) rather than an isolated single-entry file.
_BINDING_ENTRY = {"action": "nav_next", "keyCode": 125, "modifiers": 0,
                   "message": json.dumps({"type": "nav", "to": "next"})}


def _write_resolved(tmp_path, array):
    resolved = tmp_path / "hotkeyd.resolved.json"
    resolved.write_text(json.dumps(array), encoding="utf-8")
    return resolved


def _row(tmp_path, loaded=True, resolved_path=None):
    """resolved_path defaults to a path that does not exist (missing file)."""
    path = resolved_path if resolved_path is not None else tmp_path / "missing.json"
    sup = sup_mod.MacSupervisorBackend()
    with mock.patch.object(sup, "launchctl", return_value=0 if loaded else 1), \
         mock.patch("sonari.paths.HOTKEYD_RESOLVED_PATH", path):
        rows = {n: (ok, d) for n, ok, d in sup.doctor_rows()}
    return rows["hotkeyd"]


def test_not_loaded_is_a_failure(tmp_path):
    resolved = _write_resolved(tmp_path,
                               [{"action": "witness_config", "alarmEnabled": True}])
    ok, detail = _row(tmp_path, loaded=False, resolved_path=resolved)
    assert ok is False
    assert "not running" in detail


def test_running_with_the_alarm_disabled_is_a_failure(tmp_path):
    """A watchdog that cannot bark is worse than none — it looks like cover."""
    resolved = _write_resolved(tmp_path, [
        _BINDING_ENTRY,
        {"action": "witness_config", "alarmEnabled": False},
    ])
    ok, detail = _row(tmp_path, resolved_path=resolved)
    assert ok is False
    assert "alarm" in detail


def test_running_and_armed_is_healthy(tmp_path):
    asset = tmp_path / "Hero.aiff"
    asset.write_bytes(b"x")
    resolved = _write_resolved(tmp_path, [
        _BINDING_ENTRY,
        {"action": "witness_config", "alarmEnabled": True, "alarmAsset": str(asset)},
    ])
    ok, detail = _row(tmp_path, resolved_path=resolved)
    assert ok is True
    assert "armed" in detail


def test_missing_resolved_file_still_reports_armed_compiled_defaults(tmp_path):
    """sonari-hotkeyd.swift:174-177 compiles in defaults precisely so a stale or
    missing resolved file cannot silently disable the alarm."""
    ok, _ = _row(tmp_path, resolved_path=tmp_path / "does-not-exist.json")
    assert ok is True


def test_valid_array_without_a_witness_entry_uses_compiled_defaults(tmp_path):
    """Pins the container shape: the resolved file is a JSON ARRAY, not a dict
    keyed by "witness_config". A well-formed array that simply carries no
    witness_config entry (e.g. resolved before the witness entry existed)
    must fall through to the compiled-in defaults — same as a missing file —
    not crash and not read as unarmed."""
    resolved = _write_resolved(tmp_path, [_BINDING_ENTRY])
    ok, detail = _row(tmp_path, resolved_path=resolved)
    assert ok is True
    assert "armed" in detail
