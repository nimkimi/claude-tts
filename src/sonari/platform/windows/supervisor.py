"""Windows supervisor backend — zero-admin Task Scheduler autostart, Python
resolution (py-launcher + Store-stub avoidance), exec-form hooks, and the
WinSupervisorBackend ABC implementation.

WINDOWS-only. Every Windows-only stdlib import (winreg, ctypes) is lazy (inside
a method/function) so this module imports cleanly on macOS/Linux for the mock
test suite. "Importable + mock-green" here does NOT mean Windows-verified — the
real gate is docs/superpowers/M2-WINDOWS-ACCEPTANCE.md.

Bodies copied verbatim from docs/superpowers/m2-windows-api-reference.md
(§Windows SupervisorBackend), adapting only: the file/import location to our
layout (src/sonari/platform/windows/...), subclassing the real ABC from
sonari.platform.base, and keeping Windows-only imports lazy.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from sonari.platform.base import SupervisorBackend

TASK_NAME = "Sonari.Speechd"

# Windows process-creation flags. Defined in subprocess only on win32, so use
# hex literals to keep this module importable on macOS/Linux.
_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_SPAWN_FLAGS = _CREATE_NO_WINDOW | _DETACHED_PROCESS  # 0x08000008


# ---------------------------------------------------------------------------
# Zero-admin Task Scheduler autostart via hand-authored XML
# ---------------------------------------------------------------------------

# UTF-16 LE with BOM is required by schtasks /xml on older Windows builds.
# Python's encoding='utf-16' produces exactly that.
TASK_XML_TEMPLATE = '''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2"
  xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>{user_id}</Author>
    <Description>Sonari speech daemon supervisor (autostart on logon)</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user_id}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <Hidden>true</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT5M</Interval>
      <Count>5</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{pythonw}</Command>
      <Arguments>"{supervisor_py}"</Arguments>
      <WorkingDirectory>{work_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'''


def _current_user_id() -> str:
    """Return DOMAIN\\user or COMPUTERNAME\\user for LogonTrigger/UserId."""
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    size = ctypes.c_ulong(256)
    ctypes.windll.secur32.GetUserNameExW(2, buf, ctypes.byref(size))  # 2 = NameSamCompatible
    return buf.value


def task_install(pythonw: str, supervisor_py: str) -> int:
    """Register the Task Scheduler task. Returns schtasks exit code (0 = success)."""
    user_id = _current_user_id()
    xml_content = TASK_XML_TEMPLATE.format(
        user_id=user_id,
        pythonw=pythonw,
        supervisor_py=supervisor_py,
        work_dir=str(Path(supervisor_py).parent),
    )
    # Write UTF-16 LE with BOM — required by schtasks /xml
    with tempfile.NamedTemporaryFile(
            mode='w', suffix='.xml', encoding='utf-16',
            delete=False) as fh:
        fh.write(xml_content)
        tmp = fh.name
    try:
        return subprocess.call(
            ["schtasks", "/create", "/tn", TASK_NAME, "/xml", tmp, "/f"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    finally:
        os.unlink(tmp)


def task_uninstall() -> int:
    """Delete the task. /f suppresses confirmation prompt."""
    return subprocess.call(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def task_is_installed() -> bool:
    """Return True if the task exists (schtasks /query exit 0 = found)."""
    return subprocess.call(
        ["schtasks", "/query", "/tn", TASK_NAME],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ) == 0

# KEY GOTCHA: RestartOnFailure is NOT expressible via schtasks CLI flags — XML only.
# The Task Scheduler's RestartOnFailure only restarts the *supervisor* process if
# it crashes (unlikely). The supervisor_loop is the real daemon restarter.


# ---------------------------------------------------------------------------
# Windows Python resolution — py -3 launcher, PATH probe, Store-stub detection
# ---------------------------------------------------------------------------

def _is_store_stub(path: str) -> bool:
    """Return True if *path* is the Windows Store Python stub.

    Fast path: WindowsApps in the normalised path.
    Slow path: run it and check for exit code 9009 (store stub sentinel) or
    empty stdout (the stub prints nothing and exits non-zero).
    """
    if "WindowsApps" in os.path.normcase(path):
        return True
    try:
        result = subprocess.run(
            [path, "-c", "import sys; print(sys.executable)"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 9009 or not result.stdout.strip()
    except Exception:
        return True   # treat anything broken as a stub


def _find_pythonw(python_real: str) -> "str | None":
    """Return the pythonw.exe sibling of *python_real*, or None."""
    d = os.path.dirname(python_real)
    for candidate in (
        os.path.join(d, "pythonw.exe"),
        os.path.join(d, "Scripts", "pythonw.exe"),   # venv layout
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _probe_python_version(candidate: str):
    """Return (major, minor) or None."""
    try:
        out = subprocess.check_output(
            [candidate, "-c",
             "import sys; print('%d.%d' % sys.version_info[:2])"],
            stderr=subprocess.DEVNULL, text=True, timeout=5,
        ).strip()
        major, minor = out.split(".")
        return (int(major), int(minor))
    except Exception:
        return None


def _probe_version_via_launcher(py_exe: str) -> "str | None":
    """Use `py -3 -c 'print(sys.executable)'` to resolve the real interpreter."""
    try:
        real = subprocess.check_output(
            [py_exe, "-3", "-c", "import sys; print(sys.executable)"],
            stderr=subprocess.DEVNULL, text=True, timeout=5,
        ).strip()
        return real if real else None
    except Exception:
        return None


def resolve_python_windows() -> "str | None":
    """Return pythonw.exe path for the best Python 3 >= 3.9, or None.

    Resolution order:
      1. py -3 launcher (works even when python.exe is not on PATH)
      2. 'python' on PATH (skip Microsoft Store stubs)
      3. 'python3' on PATH (skip Microsoft Store stubs)
    Deduped by realpath; prefers the py-launcher result.
    """
    seen_real = set()
    candidates = []   # list of (real_python_path, source_label)

    # 1. Windows Python Launcher
    py = shutil.which("py")
    if py:
        real = _probe_version_via_launcher(py)
        if real and not _is_store_stub(real):
            candidates.append((real, "py-launcher"))

    # 2 & 3. PATH-based names
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found and not _is_store_stub(found):
            try:
                real = subprocess.check_output(
                    [found, "-c", "import sys; print(sys.executable)"],
                    stderr=subprocess.DEVNULL, text=True, timeout=5,
                ).strip()
            except Exception:
                continue
            if real:
                candidates.append((real, name))

    for real, _src in candidates:
        norm = os.path.normcase(os.path.realpath(real))
        if norm in seen_real:
            continue
        seen_real.add(norm)
        ver = _probe_python_version(real)
        if ver and ver >= (3, 9):
            pw = _find_pythonw(real)
            if pw:
                return pw

    return None


# ---------------------------------------------------------------------------
# exec-form hooks.json for Windows (no bash shim) + .gitattributes LF line
# ---------------------------------------------------------------------------

# The resolved pythonw.exe path is baked in at install time by
# WinSupervisorBackend.install(). Claude Code supports separate 'command' +
# 'args' (exec-form) — no bash shim required.
HOOKS_JSON_TEMPLATE = '''{{
  "hooks": {{
    "MessageDisplay": [
      {{
        "matcher": "",
        "hooks": [
          {{
            "type": "command",
            "command": "{pythonw}",
            "args": [
              "{hook_py}",
              "MessageDisplay"
            ]
          }}
        ]
      }}
    ],
    "Stop": [
      {{
        "matcher": "",
        "hooks": [
          {{
            "type": "command",
            "command": "{pythonw}",
            "args": [
              "{hook_py}",
              "Stop"
            ]
          }}
        ]
      }}
    ]
  }}
}}'''


def build_hooks_json(pythonw: str, hook_py: str) -> str:
    """Return hooks.json content with backslashes doubled for JSON."""
    return HOOKS_JSON_TEMPLATE.format(
        pythonw=pythonw.replace("\\", "\\\\"),
        hook_py=hook_py.replace("\\", "\\\\"),
    )


# .gitattributes entry — prevents CRLF injection on Windows checkout.
# Created at repo root in Task 8; surfaced here for the install-time writer.
_GITATTRIBUTES_LINE = "hooks/*.py  text eol=lf\n"
GITATTRIBUTES_LINE = _GITATTRIBUTES_LINE  # public alias (reference name)


# ---------------------------------------------------------------------------
# WinSupervisorBackend — the SupervisorBackend ABC implementation
# ---------------------------------------------------------------------------

class WinSupervisorBackend(SupervisorBackend):

    # --- monkeypatchable thin wrappers ---

    def _schtasks(self, args: list) -> int:
        """Run 'schtasks <args>'. Monkeypatched in tests."""
        return subprocess.call(
            ["schtasks"] + args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _probe_python_version(self, candidate: str):
        """Return (major, minor) or None. Monkeypatched in tests."""
        try:
            out = subprocess.check_output(
                [candidate, "-c",
                 "import sys; print('%d.%d' % sys.version_info[:2])"],
                stderr=subprocess.DEVNULL, text=True, timeout=5,
            ).strip()
            major, minor = out.split(".")
            return (int(major), int(minor))
        except Exception:
            return None

    def _list_neural_voices(self) -> list:
        """Return list of neural voice token names. Monkeypatched in tests.

        Registry path: HKLM\\SOFTWARE\\Microsoft\\Speech_OneCore\\Voices\\Tokens
        NOT the legacy Speech\\Voices\\Tokens key (Narrator/OneCore voices only).
        winreg is Windows-only stdlib — imported lazily inside the method.
        """
        import winreg
        key_path = r"SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens"
        voices = []
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
            i = 0
            while True:
                try:
                    voices.append(winreg.EnumKey(key, i))
                    i += 1
                except OSError:
                    break
        except OSError:
            pass
        return voices

    # --- SupervisorBackend ABC ---

    def is_installed(self) -> bool:
        """Return True if the Task Scheduler task exists."""
        return self._schtasks(["/query", "/tn", TASK_NAME]) == 0

    def is_running(self) -> bool:
        """Return True if the daemon socket is accepting connections."""
        from sonari import paths
        return paths.socket_connectable()

    def resolve_python(self) -> Optional[str]:
        """Return pythonw.exe for the best Python >= 3.9, or None."""
        return resolve_python_windows()

    def launch_spec(self) -> tuple:
        """Return (argv, spawn_kwargs) for lazy daemon start."""
        pw = self.resolve_python() or "pythonw.exe"
        argv = [pw, "-m", "sonari.daemon"]
        kwargs = dict(
            creationflags=_SPAWN_FLAGS,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return argv, kwargs

    def doctor_rows(self) -> list:
        """Return Windows-specific [(name, ok, detail), ...] rows.

        Never raises — wrap every external call in try/except so 'sonari doctor'
        always renders (mirrors MacSupervisorBackend).
        """
        rows = []

        # schtasks availability
        schtasks = shutil.which("schtasks")
        rows.append(("schtasks", schtasks is not None,
                     schtasks or "not found (unexpected on Windows)"))

        # Task registered
        task_ok = self.is_installed()
        rows.append(("Task Scheduler task", task_ok,
                     TASK_NAME if task_ok
                     else "not registered (run 'sonari install')"))

        # pythonw.exe
        pw = self.resolve_python()
        rows.append(("pythonw.exe", pw is not None,
                     pw or "no Python >= 3.9 found; install from python.org"))

        # Neural voices (Speech_OneCore)
        try:
            voices = self._list_neural_voices()
            ok = bool(voices)
            detail = voices[0] if ok else (
                "none; install from Settings > Time & language > Speech")
            rows.append(("neural voice", ok, detail))
        except Exception as exc:
            rows.append(("neural voice", False, "error: {0}".format(exc)))

        # Daemon running
        running = self.is_running()
        rows.append(("daemon running", running,
                     "accepting connections" if running
                     else "not running (run 'sonari start')"))

        return rows

    def install(self, python: str, app_dir: str) -> None:
        supervisor_py = os.path.join(app_dir, "sonari", "platform",
                                     "windows", "supervisor_loop.py")
        task_install(python, supervisor_py)

    def uninstall(self) -> None:
        task_uninstall()
