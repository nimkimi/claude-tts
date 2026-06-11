"""Windows hotkey backend — STUB (M3 implements real RegisterHotKey)."""
from __future__ import annotations

from sonari.platform.base import HotkeyBackend

# Minimal VK display map; M3 will expand this.
_VK_LABELS: dict[int, str] = {
    0x4F: "O",
    0x53: "S",
    0x51: "Q",
    0x5A: "Z",
    0x20: "Space",
    0x0D: "Enter",
    0x1B: "Escape",
}


class WinHotkeyBackend(HotkeyBackend):
    """Stub hotkey backend.  Hotkeys land in Milestone 3."""

    def install(self, log_path: str, agent_path: str, launchctl_fn) -> tuple:
        return (False, "Windows hotkeys land in M3 (Milestone 3).")

    def uninstall(self) -> None:
        pass

    def display_combo(self, modifiers: int, key_code: int) -> str:
        parts = []
        if modifiers & 0x0002:
            parts.append("Ctrl")
        if modifiers & 0x0004:
            parts.append("Shift")
        if modifiers & 0x0001:
            parts.append("Alt")
        parts.append(_VK_LABELS.get(key_code, "key{0}".format(key_code)))
        return "+".join(parts)
