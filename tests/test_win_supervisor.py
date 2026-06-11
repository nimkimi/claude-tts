"""Mock-based tests for WinSupervisorBackend, Task XML, resolve_python.

WINDOWS-only code, exercised on macOS via the _winfakes harness (winreg fake is
installed by tests/conftest.py before this module imports the backend). "Green"
here means the MOCKED contract holds, NOT that it works on Windows. The real gate
is docs/superpowers/M2-WINDOWS-ACCEPTANCE.md.

Inject a fake winreg module before importing the Windows backend, then monkeypatch
instance methods for all external calls. XML structure is validated via
ElementTree.fromstring() with the full namespace string, which is more robust than
string-contains checks. The sys.modules.setdefault call is idempotent — running on
real Windows leaves the genuine winreg intact.
"""
import sys
import types
import xml.etree.ElementTree as ET

# --- winreg injection (must happen before any import of the windows backend) ---
if sys.platform != "win32":
    _fake_winreg = types.ModuleType("winreg")
    _fake_winreg.HKEY_LOCAL_MACHINE = 0x80000002
    _fake_winreg.OpenKey = lambda *a, **kw: None
    _fake_winreg.EnumKey = lambda *a, **kw: (_ for _ in ()).throw(OSError())
    _fake_winreg.QueryValueEx = lambda *a, **kw: (_ for _ in ()).throw(OSError())
    sys.modules.setdefault("winreg", _fake_winreg)

from sonari.platform.windows.supervisor import (
    WinSupervisorBackend, TASK_NAME, TASK_XML_TEMPLATE, _SPAWN_FLAGS,
)

_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def test_task_xml_logon_trigger_user_id():
    xml_str = TASK_XML_TEMPLATE.format(
        user_id="DESKTOP-ABC\\nima",
        pythonw=r"C:\Python311\pythonw.exe",
        supervisor_py=r"C:\sonari\supervisor_loop.py",
        work_dir=r"C:\sonari",
    )
    root = ET.fromstring(xml_str)
    uid_el = root.find(f".//{{{_NS}}}LogonTrigger/{{{_NS}}}UserId")
    assert uid_el is not None and uid_el.text == "DESKTOP-ABC\\nima"


def test_task_xml_restart_on_failure_present():
    xml_str = TASK_XML_TEMPLATE.format(
        user_id="DESKTOP\\u", pythonw="pw.exe",
        supervisor_py="s.py", work_dir=".",
    )
    root = ET.fromstring(xml_str)
    rof = root.find(f".//{{{_NS}}}RestartOnFailure")
    assert rof is not None
    interval = rof.find(f"{{{_NS}}}Interval")
    assert interval.text == "PT5M"


def test_task_xml_run_level_least_privilege():
    xml_str = TASK_XML_TEMPLATE.format(
        user_id="U", pythonw="pw.exe", supervisor_py="s.py", work_dir=".",
    )
    root = ET.fromstring(xml_str)
    rl = root.find(f".//{{{_NS}}}Principal/{{{_NS}}}RunLevel")
    assert rl.text == "LeastPrivilege"


def test_launch_spec_creationflags(monkeypatch):
    sup = WinSupervisorBackend()
    monkeypatch.setattr(sup, "resolve_python", lambda: r"C:\Python311\pythonw.exe")
    argv, kwargs = sup.launch_spec()
    assert argv[0].endswith("pythonw.exe")
    assert argv[-1] == "sonari.daemon"
    flags = kwargs["creationflags"]
    assert flags & 0x08000000, "CREATE_NO_WINDOW must be set"
    assert flags & 0x00000008, "DETACHED_PROCESS must be set"
    assert not kwargs.get("start_new_session", False), "must NOT combine with DETACHED_PROCESS"


def test_is_installed_calls_schtasks_query(monkeypatch):
    sup = WinSupervisorBackend()
    calls = []
    monkeypatch.setattr(sup, "_schtasks", lambda args: calls.append(args) or 0)
    assert sup.is_installed() is True
    assert calls[0] == ["/query", "/tn", TASK_NAME]


def test_doctor_rows_include_task_and_neural_voice(monkeypatch):
    sup = WinSupervisorBackend()
    monkeypatch.setattr(sup, "_schtasks", lambda args: 0)
    monkeypatch.setattr(sup, "resolve_python", lambda: r"C:\Python311\pythonw.exe")
    monkeypatch.setattr(sup, "_list_neural_voices", lambda: ["Microsoft Aria Online"])
    monkeypatch.setattr("sonari.paths.socket_connectable", lambda: True)
    names = [r[0] for r in sup.doctor_rows()]
    assert "Task Scheduler task" in names
    assert "pythonw.exe" in names
    assert "neural voice" in names
    assert "daemon running" in names


def test_resolve_python_skips_store_stub(monkeypatch, tmp_path):
    # Verify _is_store_stub fast-path (WindowsApps in path)
    from sonari.platform.windows.supervisor import _is_store_stub
    stub = str(tmp_path / "WindowsApps" / "python.exe")
    assert _is_store_stub(stub) is True


def test_spawn_flags_value():
    # Hex literal correctness — no subprocess import needed
    assert _SPAWN_FLAGS == 0x08000008
