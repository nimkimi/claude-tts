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


def test_doctor_rows_report_collisions(monkeypatch):
    hk, _ = _backend_with_fake_user32(monkeypatch, fail_ids={1})
    hk._register_all([{"action": "stop", "keyCode": 0x53, "modifiers": 0x2,
                       "message": '{"type": "stop"}'}])
    rows = hk.doctor_rows()
    chord = [r for r in rows if r[0] == "hotkey chords"][0]
    assert chord[1] is False and "stop" in chord[2]


def test_doctor_rows_clean_when_no_collisions(monkeypatch):
    hk = WinHotkeyBackend()
    monkeypatch.setattr(hk, "_process_is_elevated", lambda: False)
    rows = hk.doctor_rows()
    assert rows == [("hotkey chords", True, "no collisions")]


def test_uipi_row_when_elevated(monkeypatch):
    hk = WinHotkeyBackend()
    monkeypatch.setattr(hk, "_process_is_elevated", lambda: True)
    rows = hk.doctor_rows()
    assert any("Administrator" in r[2] for r in rows)


def test_display_combo_labels():
    hk = WinHotkeyBackend()
    assert hk.display_combo(0x0002 | 0x0004 | 0x0001, 0x4F) == "Ctrl+Shift+Alt+O"
    assert hk.display_combo(0x0002, 0x53) == "Ctrl+S"


def test_display_combo_arrow_labels():
    hk = WinHotkeyBackend()
    assert hk.display_combo(0x0001, 0x27) == "Alt+Right"
    assert hk.display_combo(0x0001, 0x25) == "Alt+Left"
    assert hk.display_combo(0x0001, 0x26) == "Alt+Up"
    assert hk.display_combo(0x0001, 0x28) == "Alt+Down"
