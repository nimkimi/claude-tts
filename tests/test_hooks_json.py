import json
import os


def test_hooks_json_registers_permission_request():
    """Verify hooks.json registers the PermissionRequest event with sonari-hook."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = json.load(open(os.path.join(root, "hooks", "hooks.json")))
    entries = cfg["hooks"]["PermissionRequest"]
    cmds = [h["command"] for e in entries for h in e["hooks"]]
    assert any("sonari-hook PermissionRequest" in c for c in cmds)
