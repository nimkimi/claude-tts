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
                      "your_turn",                           # D2 §6.1 solo boundary
                      "submit_ack",                          # D2 §6.1 prompt-submit ack
                      "repoint"}                             # D2 §6.2 workspace repoint
    assert d["your_turn"] == "/System/Library/Sounds/Pop.aiff"
    assert d["submit_ack"] == "/System/Library/Sounds/Morse.aiff"
    assert d["repoint"] == "/System/Library/Sounds/Bottle.aiff"
