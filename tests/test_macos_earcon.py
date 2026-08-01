from sonari.platform.macos import earcon as mod
from sonari.platform.macos.earcon import MacEarconBackend


def test_play_invokes_afplay_with_path(monkeypatch):
    seen = {}
    monkeypatch.setattr(mod.os.path, "exists", lambda p: True)
    monkeypatch.setattr(mod.subprocess, "Popen", lambda args: seen.setdefault("args", args))
    MacEarconBackend().play("/x/Funk.aiff")
    assert seen["args"] == ["afplay", "/x/Funk.aiff"]


def test_play_missing_file_is_none(monkeypatch):
    monkeypatch.setattr(mod.os.path, "exists", lambda p: False)
    assert MacEarconBackend().play("/nope.aiff") is None


def test_default_earcons_are_macos_system_sounds():
    d = MacEarconBackend().default_earcons()
    assert d["permission"] == "/System/Library/Sounds/Funk.aiff"
    assert set(d) == {"permission", "choice", "plan", "error", "turn_done",
                      "error_misdirected", "error_system",   # W6 taxonomy
                      "permission_expired",                  # W7 expiry
                      "submit_ack",                          # D2 §6.1 prompt-submit ack
                      "repoint",                             # D2 §6.2 workspace repoint
                      "crossing",                            # D2 §6.6 keep-going miss marker
                      "alarm_daemon_down", "alarm_hotkeys_down"}  # §7 witness alarms
    assert d["submit_ack"] == "/System/Library/Sounds/Morse.aiff"
    assert d["repoint"] == "/System/Library/Sounds/Bottle.aiff"
    assert d["crossing"] == "/System/Library/Sounds/Frog.aiff"
    assert d["alarm_daemon_down"] == "/System/Library/Sounds/Hero.aiff"
    assert d["alarm_hotkeys_down"] == "/System/Library/Sounds/Basso.aiff"
