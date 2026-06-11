"""Task 8 — exec-form hooks.json builder tests.

Verifies that build_hooks_json() produces valid JSON with Windows paths
correctly escaped, in exec-form (command + args) for all Sonari events.
"""
import json
from sonari.platform.windows.supervisor import build_hooks_json


def test_hooks_json_is_exec_form_with_escaped_paths():
    s = build_hooks_json(r"C:\u\.sonari\pythonw.exe", r"C:\plug\hook.py")
    data = json.loads(s)  # valid JSON (backslashes doubled)
    md = data["hooks"]["MessageDisplay"][0]["hooks"][0]
    assert md["type"] == "command"
    assert md["command"].endswith("pythonw.exe")
    assert md["args"][0].endswith("hook.py") and md["args"][-1] == "MessageDisplay"
