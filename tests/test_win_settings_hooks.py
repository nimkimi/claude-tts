"""~/.claude/settings.json hook merge/remove (Windows install glue).

Pure stdlib + tmp files — no winrt fakes needed. Runs identically on macOS and
Windows: the merge logic is OS-independent JSON manipulation.
"""
import json

import pytest

from sonari.platform.windows.supervisor import (
    merge_hooks_into_settings,
    remove_hooks_from_settings,
    settings_has_sonari_hooks,
)

PW = r"C:\Py\pythonw.exe"
HOOK = r"C:\plug\bin\sonari-hook"


def _read(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def test_merge_creates_file_with_sonari_hooks(tmp_path):
    sp = str(tmp_path / "settings.json")
    merge_hooks_into_settings(sp, PW, HOOK)
    data = _read(sp)
    md = data["hooks"]["MessageDisplay"][0]["hooks"][0]
    assert md["command"] == PW and md["args"] == [HOOK, "MessageDisplay"]
    assert settings_has_sonari_hooks(sp)


def test_merge_preserves_unrelated_keys_and_hooks(tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps({
        "theme": "dark",
        "hooks": {"Stop": [{"matcher": "", "hooks": [
            {"type": "command", "command": "other.exe", "args": ["x"]}]}]},
    }), encoding="utf-8")
    merge_hooks_into_settings(str(sp), PW, HOOK)
    data = _read(str(sp))
    assert data["theme"] == "dark"
    stop_cmds = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
    assert "other.exe" in stop_cmds and PW in stop_cmds   # unrelated kept, sonari added


def test_merge_is_idempotent(tmp_path):
    sp = str(tmp_path / "settings.json")
    merge_hooks_into_settings(sp, PW, HOOK)
    merge_hooks_into_settings(sp, PW, HOOK)
    data = _read(sp)
    sonari = [h for e in data["hooks"]["MessageDisplay"] for h in e["hooks"]
              if "sonari-hook" in (h.get("command", "") + " ".join(h.get("args", [])))]
    assert len(sonari) == 1   # not duplicated


def test_remove_drops_only_sonari_entries(tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps({"hooks": {"Stop": [{"matcher": "", "hooks": [
        {"type": "command", "command": "other.exe", "args": ["x"]}]}]}}), encoding="utf-8")
    merge_hooks_into_settings(str(sp), PW, HOOK)
    remove_hooks_from_settings(str(sp), HOOK)
    data = _read(str(sp))
    assert not settings_has_sonari_hooks(str(sp))
    stop_cmds = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
    assert stop_cmds == ["other.exe"]   # unrelated survived


def test_invalid_json_aborts_without_clobber(tmp_path):
    sp = tmp_path / "settings.json"
    sp.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        merge_hooks_into_settings(str(sp), PW, HOOK)
    assert sp.read_text(encoding="utf-8") == "{ not json"   # untouched
