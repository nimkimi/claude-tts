# tests/test_macos_hotkeys.py
from sonari.platform.macos.hotkeys import MacHotkeyBackend


def test_display_combo_labels_ctrl_cmd_o():
    hk = MacHotkeyBackend()
    # 4096 (ctrl) | 256 (cmd) == 4352 ; key 31 == 'O'
    assert hk.display_combo(4352, 31) == "Ctrl+Cmd+O"


def test_display_tables_cover_every_keycode_and_modifier():
    from sonari.platform.macos import keytables
    hk = MacHotkeyBackend()
    for code in keytables.KEY_CODES.values():
        assert code in hk._keycode_display
    for mask in keytables.MOD_MASKS.values():
        assert mask in {m for m, _ in hk._mod_display}


def test_hotkey_install_defaults_agent_path_and_loads(tmp_path, monkeypatch):
    import sonari.platform.macos.hotkeys as mh
    agent = tmp_path / "com.sonari.hotkeyd.plist"
    monkeypatch.setattr(mh, "LAUNCH_AGENT_PATH", str(agent))
    monkeypatch.setattr(mh.paths, "HOTKEYD_BIN_PATH", tmp_path / "sonari-hotkeyd")
    monkeypatch.setattr(mh.MacHotkeyBackend, "build", lambda self: (True, "built"))
    calls = []
    ok, _ = mh.MacHotkeyBackend().install(
        log_path=str(tmp_path / "hk.log"), agent_path=None,
        launchctl_fn=lambda a: calls.append(a) or 0)
    assert ok is True and agent.exists()
    assert ["load", str(agent)] in calls


def test_hotkey_uninstall_removes_agent_and_binary(tmp_path, monkeypatch):
    import sonari.platform.macos.hotkeys as mh
    agent = tmp_path / "com.sonari.hotkeyd.plist"; agent.write_text("<plist/>")
    binp = tmp_path / "sonari-hotkeyd"; binp.write_text("bin")
    monkeypatch.setattr(mh, "LAUNCH_AGENT_PATH", str(agent))
    monkeypatch.setattr(mh.paths, "HOTKEYD_BIN_PATH", binp)
    monkeypatch.setattr("sonari.platform.macos.supervisor.MacSupervisorBackend.launchctl",
                        lambda self, a: 0)
    mh.MacHotkeyBackend().uninstall()
    assert not agent.exists() and not binp.exists()
