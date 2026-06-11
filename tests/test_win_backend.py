from __future__ import annotations
from sonari.platform import base


def test_make_windows_backend_full_bundle():
    from sonari.platform.windows import make_backend
    pb = make_backend()
    assert isinstance(pb, base.PlatformBackend)
    for part, cls in [(pb.tts, base.TtsBackend), (pb.earcon, base.EarconBackend),
                      (pb.hotkey, base.HotkeyBackend), (pb.supervisor, base.SupervisorBackend)]:
        assert isinstance(part, cls)


def test_hotkey_stub_reports_deferred():
    from sonari.platform.windows.hotkeys import WinHotkeyBackend
    ok, detail = WinHotkeyBackend().install("log", "agent", lambda a: 0)
    assert ok is False and "M3" in detail


def test_display_combo_labels_ctrl_alt_o():
    from sonari.platform.windows.hotkeys import WinHotkeyBackend
    # MSDN RegisterHotKey modifier bits: 0x0001=Alt, 0x0002=Ctrl, 0x0004=Shift
    # 0x0003 == Ctrl | Alt; VK 0x4F == 'O'
    assert WinHotkeyBackend().display_combo(0x0003, 0x4F) == "Ctrl+Alt+O"


def test_get_platform_win32(monkeypatch):
    import sonari.platform as platform
    monkeypatch.setattr(platform.sys, "platform", "win32")
    platform._CACHE = None
    pb = platform.get_platform()
    assert isinstance(pb, base.PlatformBackend)
    platform._CACHE = None
