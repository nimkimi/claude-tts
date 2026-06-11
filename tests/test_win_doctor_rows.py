"""Task 9: Doctor wiring — Windows rows reachable via the seam.

Tests that WinSupervisorBackend.doctor_rows() returns the expected
Windows-specific rows under monkeypatching on macOS.
"""
from __future__ import annotations


def test_windows_supervisor_doctor_rows(monkeypatch):
    from sonari.platform.windows.supervisor import WinSupervisorBackend
    sup = WinSupervisorBackend()
    monkeypatch.setattr(sup, "_schtasks", lambda a: 0)
    monkeypatch.setattr(sup, "resolve_python", lambda: r"C:\Py\pythonw.exe")
    monkeypatch.setattr(sup, "_list_neural_voices", lambda: ["Microsoft Aria"])
    monkeypatch.setattr("sonari.paths.socket_connectable", lambda: True)
    names = [r[0] for r in sup.doctor_rows()]
    assert {"Task Scheduler task", "pythonw.exe", "neural voice", "daemon running"} <= set(names)
