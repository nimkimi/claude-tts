from sonari.platform.windows.hotkeys import WinHotkeyBackend
from sonari.platform.base import HotkeyBackend


def _backend_with_fake_user32(monkeypatch, *, fail_ids=()):
    hk = WinHotkeyBackend()
    registered = []

    def _register(hid, mods, vk):
        if hid in fail_ids:
            return 0           # RegisterHotKey FALSE
        registered.append((hid, mods, vk))
        return 1
    monkeypatch.setattr(hk, "_register", _register)
    monkeypatch.setattr(hk, "_unregister", lambda hid: 1)
    monkeypatch.setattr(hk, "_last_error", lambda: 1409)  # ERROR_HOTKEY_ALREADY_REGISTERED
    monkeypatch.setattr(hk, "_process_is_elevated", lambda: False)
    return hk, registered


def test_keytables_and_default_mods():
    hk = WinHotkeyBackend()
    assert hk.key_codes()["s"] == 0x53 and hk.mod_masks()["ctrl"] == 0x0002
    assert hk.default_mods() == ["ctrl", "shift", "alt"]
    assert isinstance(hk, HotkeyBackend)


def test_register_bindings_maps_ids_to_messages(monkeypatch):
    hk, registered = _backend_with_fake_user32(monkeypatch)
    resolved = [{"action": "stop", "keyCode": 0x53,
                 "modifiers": 0x0002 | 0x0004 | 0x0001,
                 "message": '{"type": "stop"}'}]
    id_to_msg = hk._register_all(resolved)
    assert len(registered) == 1
    hid, mods, vk = registered[0]
    assert vk == 0x53 and mods == (0x0002 | 0x0004 | 0x0001 | 0x4000)  # +MOD_NOREPEAT
    assert id_to_msg[hid] == {"type": "stop"}
    assert hk.collisions == []


def test_register_records_collision_on_1409(monkeypatch):
    hk, _ = _backend_with_fake_user32(monkeypatch, fail_ids={1})  # first id collides
    resolved = [{"action": "stop", "keyCode": 0x53, "modifiers": 0x2,
                 "message": '{"type": "stop"}'}]
    hk._register_all(resolved)
    assert hk.collisions and hk.collisions[0]["action"] == "stop"
    assert hk.collisions[0]["already_owned"] is True


def test_dispatch_on_hotkey_id_calls_back():
    hk = WinHotkeyBackend()
    got = []
    id_to_msg = {5: {"type": "skip"}}
    hk._on_hotkey(5, id_to_msg, got.append)
    hk._on_hotkey(99, id_to_msg, got.append)   # unknown id -> no-op
    assert got == [{"type": "skip"}]


def test_doctor_rows_unknown_when_daemon_not_running():
    # cli.doctor() builds a FRESH backend that never start()ed; it must NOT assert
    # a green "no collisions" it cannot see — the chords are registered in the
    # daemon process. With no daemon-side state file, report unknown. (#9)
    rows = WinHotkeyBackend().doctor_rows()
    chord = [r for r in rows if r[0] == "hotkey chords"][0]
    assert "no collisions" not in chord[2].lower()
    assert "daemon" in chord[2].lower()


def test_doctor_rows_report_collisions(monkeypatch):
    # The daemon-side backend registers + persists its diagnostics; doctor (a
    # different, never-started backend) reads that state. (#9)
    daemon_side, _ = _backend_with_fake_user32(monkeypatch, fail_ids={1})
    daemon_side._register_all([{"action": "stop", "keyCode": 0x53, "modifiers": 0x2,
                                "message": '{"type": "stop"}'}])
    daemon_side._write_state()
    rows = WinHotkeyBackend().doctor_rows()
    chord = [r for r in rows if r[0] == "hotkey chords"][0]
    assert chord[1] is False and "stop" in chord[2]


def test_doctor_rows_clean_when_no_collisions(monkeypatch):
    daemon_side, _ = _backend_with_fake_user32(monkeypatch)
    daemon_side._register_all([{"action": "stop", "keyCode": 0x53, "modifiers": 0x2,
                                "message": '{"type": "stop"}'}])
    daemon_side._write_state()
    rows = WinHotkeyBackend().doctor_rows()
    chord = [r for r in rows if r[0] == "hotkey chords"][0]
    assert chord == ("hotkey chords", True, "no collisions (daemon-reported)")


def test_uipi_row_when_elevated(monkeypatch):
    daemon_side, _ = _backend_with_fake_user32(monkeypatch)
    monkeypatch.setattr(daemon_side, "_process_is_elevated", lambda: True)
    daemon_side._register_all([])
    daemon_side._write_state()
    rows = WinHotkeyBackend().doctor_rows()
    assert any("Administrator" in r[2] for r in rows)


def test_display_combo_labels():
    hk = WinHotkeyBackend()
    assert hk.display_combo(0x0002 | 0x0004 | 0x0001, 0x4F) == "Ctrl+Shift+Alt+O"
    assert hk.display_combo(0x0002, 0x53) == "Ctrl+S"
