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
    for k in ("s", "r", "d", "l", "v", "o", ".", "]", "[", "w", "tab", "equal", "minus"):
        assert k in kc
    assert kc["s"] == 1 and kc["."] == 47 and kc["]"] == 30 and kc["["] == 33
    assert kc["w"] == 13 and kc["tab"] == 48 and kc["equal"] == 24 and kc["minus"] == 27
    assert mm["cmd"] == 256 and mm["shift"] == 512 and mm["ctrl"] == 4096


def test_action_messages_faster_has_delta_25():
    assert keymap.ACTION_MESSAGES["faster"] == {"type": "set_rate", "delta": 25}
    assert keymap.ACTION_MESSAGES["slower"] == {"type": "set_rate", "delta": -25}


# --- default_keymap: per-OS chord -------------------------------------------

def test_default_keymap_macos_uses_ctrl_cmd(mac):
    d = keymap.default_keymap()
    assert set(d.keys()) == {
        "nav_prev", "nav_next",
        "stop_session", "stop_all", "jump_waiting",
        "jump_decision", "where_am_i", "faster", "slower",
        "nav_prev_response", "nav_next_response",
        "chooser_step_next", "chooser_step_prev",
        "approve", "deny", "repeat_last",
    }
    assert d["nav_next"]["key"] == "right" and d["nav_next"]["mods"] == ["ctrl", "cmd"]
    assert d["stop_session"]["key"] == "s" and d["stop_all"]["key"] == "m"
    assert d["jump_decision"]["key"] == "d" and d["where_am_i"]["key"] == "w"
    assert d["faster"]["key"] == "equal" and d["slower"]["key"] == "minus"
    # Sub-project B: nav_first/nav_last lose their default keys so ⌃⌘↑/↓ can own response-nav.
    assert "nav_first" not in d and "nav_last" not in d
    assert d["nav_prev_response"] == {"key": "up", "mods": ["ctrl", "cmd"]}
    assert d["nav_next_response"] == {"key": "down", "mods": ["ctrl", "cmd"]}
    assert d["chooser_step_next"] == {"key": "tab", "mods": ["ctrl", "cmd"]}
    assert d["chooser_step_prev"] == {"key": "tab", "mods": ["ctrl", "cmd", "shift"]}


# --- resolve_keymap ---------------------------------------------------------

def test_resolve_macos_carbon_codes(mac):
    resolved = keymap.resolve_keymap({"stop_session": {"key": "p", "mods": ["ctrl", "cmd"]}})
    assert resolved == [{
        "action": "stop_session", "keyCode": 35, "modifiers": 4352,  # 4096 | 256
        "message": '{"type": "stop_session"}'}]


def test_resolve_faster_message_is_json_with_delta(mac):
    resolved = keymap.resolve_keymap({"faster": {"key": "]", "mods": ["ctrl", "cmd"]}})
    entry = resolved[0]
    assert entry["keyCode"] == 30 and entry["modifiers"] == 4352
    assert json.loads(entry["message"]) == {"type": "set_rate", "delta": 25}


def test_default_keymap_binds_only_nav_stop_keys():
    km = keymap.default_keymap()
    assert {"nav_prev", "nav_next",
            "stop_session", "stop_all", "jump_waiting"} <= set(km.keys())
    assert set(km.keys()) <= set(keymap.ACTION_MESSAGES.keys())
    # nav_first/nav_last remain valid actions but ship UNBOUND after sub-project B.
    assert "nav_first" in keymap.ACTION_MESSAGES and "nav_first" not in km
    assert "nav_last" in keymap.ACTION_MESSAGES and "nav_last" not in km


def test_default_keymap_binds_nav_stop_keys():
    km = keymap.default_keymap()
    for action in ("nav_next", "nav_prev", "stop_session", "stop_all"):
        assert action in km, f"{action} has no default binding"
        assert km[action]["key"], f"{action} default binding has no key"


def test_resolve_unknown_key_raises():
    with pytest.raises(ValueError):
        keymap.resolve_keymap({"stop_session": {"key": "zzz", "mods": ["ctrl"]}})


def test_resolve_unknown_mod_raises():
    with pytest.raises(ValueError):
        keymap.resolve_keymap({"stop_session": {"key": "p", "mods": ["hyper"]}})


def test_resolve_unknown_action_raises():
    with pytest.raises(ValueError):
        keymap.resolve_keymap({"frobnicate": {"key": "s", "mods": ["ctrl"]}})


def test_resolve_skips_unbound_entries():
    # An entry with no key is UNBOUND -> skipped (not an error), so an action with
    # a default binding can be explicitly cleared in keymap.json.
    # 'ctrl' is valid on both macOS and Windows keytables (the modifier is
    # incidental here — the point is that the keyless 'stop_session' entry is skipped).
    resolved = keymap.resolve_keymap({"stop_session": {"key": None, "mods": ["ctrl"]},
                                      "stop_all": {"key": "m", "mods": ["ctrl"]}})
    actions = {e["action"] for e in resolved}
    assert "stop_session" not in actions and "stop_all" in actions


def test_unbind_action_default_writes_unbound_override(monkeypatch, tmp_path):
    km, _ = _patch_keymap_paths(monkeypatch, tmp_path)
    keymap.unbind_action("nav_prev")             # nav_prev HAS a default binding
    user = json.loads(km.read_text(encoding="utf-8"))
    assert user["nav_prev"]["key"] is None       # explicit unbound override
    resolved = keymap.resolve_keymap(keymap.load_keymap())
    assert "nav_prev" not in {e["action"] for e in resolved}


def test_unbind_action_non_default_just_drops(monkeypatch, tmp_path):
    # nav_first is a valid action that ships UNBOUND after sub-project B, so
    # unbinding it just drops any user binding (no explicit null override needed).
    km, _ = _patch_keymap_paths(monkeypatch, tmp_path)
    km.write_text(json.dumps({"nav_first": {"key": "]", "mods": ["alt"]}}), encoding="utf-8")
    keymap.unbind_action("nav_first")            # no default -> remove the binding
    assert "nav_first" not in json.loads(km.read_text(encoding="utf-8"))


def test_unbind_unknown_action_raises():
    with pytest.raises(ValueError):
        keymap.unbind_action("bogus")


# --- load_keymap ------------------------------------------------------------

def test_load_keymap_returns_defaults_when_missing(monkeypatch, tmp_path):
    _patch_keymap_paths(monkeypatch, tmp_path)
    loaded = keymap.load_keymap()
    assert loaded == keymap.default_keymap()
    loaded["nav_prev"]["key"] = "x"  # independent copy
    assert keymap.default_keymap()["nav_prev"]["key"] == "left"


def test_load_keymap_merges_user_override(monkeypatch, tmp_path):
    km, _ = _patch_keymap_paths(monkeypatch, tmp_path)
    km.write_text(json.dumps({"stop_session": {"key": "x", "mods": ["cmd"]}}), encoding="utf-8")
    loaded = keymap.load_keymap()
    assert loaded["stop_session"] == {"key": "x", "mods": ["cmd"]}
    assert loaded["nav_next"] == keymap.default_keymap()["nav_next"]


def test_load_keymap_drops_unknown_actions(monkeypatch, tmp_path):
    # A stale keymap.json binding a since-removed action must be ignored, not break
    # the whole keymap (resolve_keymap would otherwise raise on the unknown action).
    km, _ = _patch_keymap_paths(monkeypatch, tmp_path)
    km.write_text(json.dumps({"stop": {"key": "s", "mods": ["ctrl"]},
                              "stop_session": {"key": "p", "mods": ["ctrl"]}}), encoding="utf-8")
    loaded = keymap.load_keymap()
    assert "stop" not in loaded
    assert loaded["stop_session"] == {"key": "p", "mods": ["ctrl"]}
    keymap.resolve_keymap(loaded)   # must not raise


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

def test_write_resolved_emits_array_of_bindings(monkeypatch, tmp_path):
    _patch_keymap_paths(monkeypatch, tmp_path)
    keymap.write_resolved()
    data = json.loads((tmp_path / "hotkeyd.resolved.json").read_text(encoding="utf-8"))
    # len must match the platform's full default_keymap (not just _DEFAULT_KEYS) because
    # extra_default_bindings() adds the macOS response-nav (⌃⌘↑/↓) and chooser (⌃⌘Tab/⌃⌘⇧Tab) bindings.
    assert isinstance(data, list) and len(data) == len(keymap.default_keymap())
    for entry in data:
        assert isinstance(entry["keyCode"], int)
        assert isinstance(entry["modifiers"], int)
        assert isinstance(entry["message"], str)


def test_write_resolved_no_tmp_leftover(monkeypatch, tmp_path):
    _patch_keymap_paths(monkeypatch, tmp_path)
    keymap.write_resolved()
    assert list(tmp_path.glob("*.tmp")) == []


def test_no_two_default_actions_share_a_key():
    # Default bindings may share a key only when the modifier chord differs
    # (sub-project B: chooser_step_next/chooser_step_prev both use "tab" but differ by +Shift).
    # The invariant is no two actions resolve to the same *hotkey* (key + mods pair).
    from sonari.keymap import default_keymap
    chords = [(b["key"], tuple(b["mods"])) for b in default_keymap().values()]
    assert len(chords) == len(set(chords))


def test_jump_waiting_action_message():
    assert keymap.ACTION_MESSAGES["jump_waiting"] == {"type": "jump_waiting"}


def test_default_keymap_binds_jump_waiting_to_j():
    km = keymap.default_keymap()
    assert km["jump_waiting"]["key"] == "j"


# --- response-nav actions + macOS Ctrl+Cmd+arrow defaults (sub-project B) ----

def test_response_nav_action_messages():
    assert keymap.ACTION_MESSAGES["nav_prev_response"] == {"type": "nav", "to": "prev_response"}
    assert keymap.ACTION_MESSAGES["nav_next_response"] == {"type": "nav", "to": "next_response"}


def test_response_nav_resolves_with_shift_on_macos(mac):
    resolved = keymap.resolve_keymap(
        {"nav_prev_response": {"key": "left", "mods": ["ctrl", "cmd", "shift"]}})
    row = resolved[0]
    assert row["action"] == "nav_prev_response"
    assert row["keyCode"] == 123                                  # left arrow (Carbon)
    assert row["modifiers"] == (4096 | 256 | 512)                 # ctrl | cmd | shift
    assert json.loads(row["message"]) == {"type": "nav", "to": "prev_response"}


def test_unbind_response_nav_on_macos_writes_unbound_override(mac, monkeypatch, tmp_path):
    km, _ = _patch_keymap_paths(monkeypatch, tmp_path)
    keymap.unbind_action("nav_prev_response")    # mac-defaulted -> explicit null override
    user = json.loads(km.read_text(encoding="utf-8"))
    assert user["nav_prev_response"]["key"] is None


# --- Sub-project B: cockpit grammar (chooser/where-am-i/jump/rate) ----------

def test_response_nav_default_is_ctrl_cmd_arrows_no_shift(mac):
    d = keymap.default_keymap()
    assert d["nav_prev_response"] == {"key": "up", "mods": ["ctrl", "cmd"]}
    assert d["nav_next_response"] == {"key": "down", "mods": ["ctrl", "cmd"]}


def test_chooser_step_default_bindings_on_macos(mac):
    d = keymap.default_keymap()
    assert d["chooser_step_next"] == {"key": "tab", "mods": ["ctrl", "cmd"]}
    assert d["chooser_step_prev"] == {"key": "tab", "mods": ["ctrl", "cmd", "shift"]}


def test_b_action_messages_present():
    assert keymap.ACTION_MESSAGES["jump_decision"] == {"type": "jump_decision"}
    assert keymap.ACTION_MESSAGES["where_am_i"] == {"type": "where_am_i"}
    assert keymap.ACTION_MESSAGES["chooser_step_next"] == {
        "type": "chooser_step", "direction": "next"}
    assert keymap.ACTION_MESSAGES["chooser_step_prev"] == {
        "type": "chooser_step", "direction": "prev"}


def test_full_default_keymap_resolves_without_duplicate_hotkeys(mac):
    resolved = keymap.resolve_keymap(keymap.default_keymap())
    pairs = [(e["keyCode"], e["modifiers"]) for e in resolved]
    assert len(pairs) == len(set(pairs)), "duplicate (keyCode, modifiers) in default keymap"
    actions = {e["action"] for e in resolved}
    assert {"jump_decision", "where_am_i", "faster", "slower",
            "chooser_step_next", "chooser_step_prev",
            "nav_prev_response", "nav_next_response"} <= actions


# --- Sub-project C: answer_permission (approve/deny hotkeys) -------------------

def test_approve_deny_action_messages():
    assert keymap.ACTION_MESSAGES["approve"] == {"type": "answer_permission", "behavior": "allow"}
    assert keymap.ACTION_MESSAGES["deny"] == {"type": "answer_permission", "behavior": "deny"}


def test_approve_deny_default_bindings(mac):
    km = keymap.default_keymap()
    assert "approve" in km, "approve has no default binding"
    assert "deny" in km, "deny has no default binding"
    assert km["approve"]["key"] == "return"
    assert km["deny"]["key"] == "escape"
    assert km["approve"]["mods"] == ["ctrl", "cmd"]
    assert km["deny"]["mods"] == ["ctrl", "cmd"]


def test_approve_deny_resolve_to_correct_keycodes(mac):
    resolved = keymap.resolve_keymap(keymap.default_keymap())
    approve_entry = next((e for e in resolved if e["action"] == "approve"), None)
    deny_entry = next((e for e in resolved if e["action"] == "deny"), None)
    assert approve_entry is not None, "approve not in resolved keymap"
    assert deny_entry is not None, "deny not in resolved keymap"
    # Return key = keyCode 36, Escape key = keyCode 53, Ctrl+Cmd = 4096 | 256 = 4352
    assert approve_entry["keyCode"] == 36
    assert approve_entry["modifiers"] == 4352  # ctrl | cmd
    assert deny_entry["keyCode"] == 53
    assert deny_entry["modifiers"] == 4352  # ctrl | cmd
    assert json.loads(approve_entry["message"]) == {"type": "answer_permission", "behavior": "allow"}
    assert json.loads(deny_entry["message"]) == {"type": "answer_permission", "behavior": "deny"}


def test_repeat_last_action_message_and_default_key(mac):
    assert keymap.ACTION_MESSAGES["repeat_last"] == {"type": "repeat_last"}
    d = keymap.default_keymap()
    assert d["repeat_last"]["key"] == "r"              # ⌃⌘R (owner-locked)


def test_resolved_default_keymap_has_chooser_and_no_cycle(mac):
    # Spec §10: the resolved keymap hotkeyd reads contains the chooser actions
    # and none of the deleted cycle ones.
    resolved = keymap.resolve_keymap(keymap.default_keymap())
    actions = {e["action"] for e in resolved}
    assert {"chooser_step_next", "chooser_step_prev"} <= actions
    assert not any(a.startswith("cycle_session") for a in actions)
    msgs = [json.loads(e["message"]) for e in resolved]
    assert {"type": "chooser_step", "direction": "next"} in msgs
    assert not any(m.get("type") == "cycle_session" for m in msgs)
