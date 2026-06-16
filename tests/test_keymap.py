import json

import pytest

from sonari import keymap
import sonari.platform as platform


def _force(monkeypatch, plat):
    monkeypatch.setattr(platform.sys, "platform", plat)
    platform._CACHE = None


@pytest.fixture
def mac(monkeypatch):
    _force(monkeypatch, "darwin")
    yield
    platform._CACHE = None


@pytest.fixture
def win(monkeypatch):
    _force(monkeypatch, "win32")
    yield
    platform._CACHE = None


def _patch_keymap_paths(monkeypatch, tmp_path):
    km = tmp_path / "keymap.json"
    resolved = tmp_path / "hotkeyd.resolved.json"
    monkeypatch.setattr(keymap, "KEYMAP_PATH", km)
    monkeypatch.setattr(keymap, "HOTKEYD_RESOLVED_PATH", resolved)
    monkeypatch.setattr(keymap, "SONARI_DIR", tmp_path)
    monkeypatch.setattr(keymap, "ensure_sonari_dir",
                        lambda: tmp_path.mkdir(parents=True, exist_ok=True))
    return km, resolved


# --- keytables come from the active platform backend ------------------------

def test_macos_keytables_via_backend(mac):
    kc, mm = keymap._keytables()
    for k in ("s", "r", "d", "l", "v", "o", ".", "]", "["):
        assert k in kc
    assert kc["s"] == 1 and kc["."] == 47 and kc["]"] == 30 and kc["["] == 33
    assert mm["cmd"] == 256 and mm["shift"] == 512 and mm["ctrl"] == 4096


def test_windows_keytables_via_backend(win):
    kc, mm = keymap._keytables()
    assert kc["s"] == 0x53 and kc["."] == 0xBE
    assert mm["ctrl"] == 0x0002 and mm["shift"] == 0x0004 and mm["alt"] == 0x0001


def test_action_messages_faster_has_delta_25():
    assert keymap.ACTION_MESSAGES["faster"] == {"type": "set_rate", "delta": 25}
    assert keymap.ACTION_MESSAGES["slower"] == {"type": "set_rate", "delta": -25}


# --- default_keymap: per-OS chord -------------------------------------------

def test_default_keymap_macos_uses_ctrl_cmd(mac):
    d = keymap.default_keymap()
    assert set(d.keys()) == {
        "stop", "repeat", "skip", "jump_decision", "catch_up",
        "faster", "slower", "cycle_verbosity", "reread_options"}
    assert d["stop"]["key"] == "s" and d["stop"]["mods"] == ["ctrl", "cmd"]
    assert d["skip"]["key"] == "." and d["faster"]["key"] == "]"


def test_default_keymap_windows_uses_ctrl_shift_alt(win):
    d = keymap.default_keymap()
    assert d["stop"]["mods"] == ["ctrl", "shift", "alt"]
    assert d["reread_options"]["key"] == "o"


# --- resolve_keymap ---------------------------------------------------------

def test_resolve_macos_carbon_codes(mac):
    resolved = keymap.resolve_keymap({"stop": {"key": "s", "mods": ["ctrl", "cmd"]}})
    assert resolved == [{
        "action": "stop", "keyCode": 1, "modifiers": 4352,  # 4096 | 256
        "message": '{"type": "stop"}'}]


def test_resolve_windows_vk_codes(win):
    resolved = keymap.resolve_keymap(
        {"stop": {"key": "s", "mods": ["ctrl", "shift", "alt"]}})
    row = resolved[0]
    assert row["keyCode"] == 0x53                            # VK 'S'
    assert row["modifiers"] == (0x0002 | 0x0004 | 0x0001)    # ctrl|shift|alt
    assert row["action"] == "stop"


def test_resolve_faster_message_is_json_with_delta(mac):
    resolved = keymap.resolve_keymap({"faster": {"key": "]", "mods": ["ctrl", "cmd"]}})
    entry = resolved[0]
    assert entry["keyCode"] == 30 and entry["modifiers"] == 4352
    assert json.loads(entry["message"]) == {"type": "set_rate", "delta": 25}


def test_resolve_default_keymap_covers_all_actions():
    resolved = keymap.resolve_keymap(keymap.default_keymap())
    assert len(resolved) == len(keymap._DEFAULT_KEYS)
    assert {e["action"] for e in resolved} == set(keymap._DEFAULT_KEYS.keys())


def test_resolve_unknown_key_raises():
    with pytest.raises(ValueError):
        keymap.resolve_keymap({"stop": {"key": "zzz", "mods": ["ctrl"]}})


def test_resolve_unknown_mod_raises():
    with pytest.raises(ValueError):
        keymap.resolve_keymap({"stop": {"key": "s", "mods": ["hyper"]}})


def test_resolve_unknown_action_raises():
    with pytest.raises(ValueError):
        keymap.resolve_keymap({"frobnicate": {"key": "s", "mods": ["ctrl"]}})


# --- load_keymap ------------------------------------------------------------

def test_load_keymap_returns_defaults_when_missing(monkeypatch, tmp_path):
    _patch_keymap_paths(monkeypatch, tmp_path)
    loaded = keymap.load_keymap()
    assert loaded == keymap.default_keymap()
    loaded["stop"]["key"] = "x"  # independent copy
    assert keymap.default_keymap()["stop"]["key"] == "s"


def test_load_keymap_merges_user_override(monkeypatch, tmp_path):
    km, _ = _patch_keymap_paths(monkeypatch, tmp_path)
    km.write_text(json.dumps({"stop": {"key": "x", "mods": ["cmd"]}}), encoding="utf-8")
    loaded = keymap.load_keymap()
    assert loaded["stop"] == {"key": "x", "mods": ["cmd"]}
    assert loaded["repeat"] == keymap.default_keymap()["repeat"]


def test_load_keymap_tolerates_corrupt_file(monkeypatch, tmp_path):
    km, _ = _patch_keymap_paths(monkeypatch, tmp_path)
    km.write_text("{ not json", encoding="utf-8")
    assert keymap.load_keymap() == keymap.default_keymap()


def test_write_default_keymap_if_absent_writes_once(monkeypatch, tmp_path):
    km, _ = _patch_keymap_paths(monkeypatch, tmp_path)
    assert not km.exists()
    assert keymap.write_default_keymap_if_absent() is True
    assert km.exists()
    assert json.loads(km.read_text(encoding="utf-8")) == keymap.default_keymap()
    assert keymap.write_default_keymap_if_absent() is False


# --- write_resolved ---------------------------------------------------------

def test_write_resolved_emits_array_of_nine(monkeypatch, tmp_path):
    _patch_keymap_paths(monkeypatch, tmp_path)
    out_path = keymap.write_resolved()
    data = json.loads((tmp_path / "hotkeyd.resolved.json").read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == len(keymap._DEFAULT_KEYS)
    for entry in data:
        assert isinstance(entry["keyCode"], int)
        assert isinstance(entry["modifiers"], int)
        assert isinstance(entry["message"], str)


def test_write_resolved_no_tmp_leftover(monkeypatch, tmp_path):
    _patch_keymap_paths(monkeypatch, tmp_path)
    keymap.write_resolved()
    assert list(tmp_path.glob("*.tmp")) == []


def test_resolve_nav_action_message(win):
    resolved = keymap.resolve_keymap({"nav_next": {"key": "right", "mods": ["alt"]}})
    assert resolved[0]["action"] == "nav_next"
    assert json.loads(resolved[0]["message"]) == {"type": "nav", "to": "next"}
