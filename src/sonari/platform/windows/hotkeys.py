"""Windows hotkey backend — in-process RegisterHotKey + GetMessage pump.

WINDOWS-only ctypes is reached ONLY through the _register/_unregister/
_get_message/_post_quit/_last_error wrappers (monkeypatched in tests), so this
module imports on any host. Whether a chord actually fires system-wide is
on-hardware-only (M3-WINDOWS-ACCEPTANCE.md)."""
from __future__ import annotations

import json
import threading
from typing import Optional

from sonari.platform.base import HotkeyBackend
from sonari.platform.windows import keytables

_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_VK_LABELS = {
    0x53: "S", 0x52: "R", 0x44: "D", 0x4C: "L", 0x56: "V", 0x4F: "O",
    0x50: "P", 0x4D: "M",
    0xBE: ".", 0xDD: "]", 0xDB: "[", 0x20: "Space", 0x0D: "Enter", 0x1B: "Escape",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
}
_MOD_LABELS = [(0x0002, "Ctrl"), (0x0004, "Shift"), (0x0001, "Alt"), (0x0008, "Win")]


class WinHotkeyBackend(HotkeyBackend):
    """In-process global hotkeys via Win32 RegisterHotKey on a daemon thread."""

    def __init__(self) -> None:
        self.collisions: list = []
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._stop = threading.Event()

    # --- keytables ---
    def key_codes(self) -> dict:
        return dict(keytables.KEY_CODES)

    def mod_masks(self) -> dict:
        return dict(keytables.MOD_MASKS)

    def default_mods(self) -> list:
        return list(keytables.DEFAULT_MODS)

    # --- monkeypatchable user32/kernel32 wrappers (lazy ctypes) ---
    def _register(self, hid: int, mods: int, vk: int) -> int:
        import ctypes
        return ctypes.windll.user32.RegisterHotKey(None, hid, mods, vk)

    def _unregister(self, hid: int) -> int:
        import ctypes
        return ctypes.windll.user32.UnregisterHotKey(None, hid)

    def _last_error(self) -> int:
        import ctypes
        return ctypes.windll.kernel32.GetLastError()

    def _get_message(self):
        """Block for the next thread message; return (wm, wparam, lparam) or None
        on WM_QUIT/error."""
        import ctypes
        from ctypes import wintypes
        msg = wintypes.MSG()
        r = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r in (0, -1):            # 0 == WM_QUIT, -1 == error
            return None
        return (msg.message, int(msg.wParam), int(msg.lParam))

    def _post_quit(self) -> None:
        import ctypes
        if self._thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)

    def _process_is_elevated(self) -> bool:
        """True if THIS (daemon) process runs elevated. Lazy ctypes; never raises."""
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    # --- registration + dispatch logic (pure, unit-tested) ---
    def _register_all(self, resolved: list) -> dict:
        """RegisterHotKey each resolved binding (MOD_NOREPEAT added). Returns
        {hotkey_id: message_dict}. Records collisions instead of raising."""
        self.collisions = []
        id_to_msg = {}
        for i, b in enumerate(resolved, start=1):
            mods = b["modifiers"] | keytables.MOD_NOREPEAT
            if self._register(i, mods, b["keyCode"]):
                id_to_msg[i] = json.loads(b["message"])
            else:
                err = self._last_error()
                self.collisions.append({
                    "action": b["action"], "error": err,
                    "already_owned": err == _ERROR_HOTKEY_ALREADY_REGISTERED,
                })
        return id_to_msg

    def _on_hotkey(self, hid: int, id_to_msg: dict, dispatch) -> None:
        msg = id_to_msg.get(hid)
        if msg is not None:
            dispatch(msg)

    # --- lifecycle ---
    def start(self, dispatch) -> None:
        """Start a daemon thread that registers the current keymap and pumps
        WM_HOTKEY into *dispatch*. RegisterHotKey + GetMessage MUST share the same
        thread, so registration happens inside the thread."""
        from sonari import keymap
        resolved = keymap.resolve_keymap(keymap.load_keymap())
        self._stop.clear()

        def _run():
            import ctypes
            self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            id_to_msg = self._register_all(resolved)
            try:
                while not self._stop.is_set():
                    got = self._get_message()
                    if got is None:
                        break
                    wm, wparam, _ = got
                    if wm == _WM_HOTKEY:
                        self._on_hotkey(wparam, id_to_msg, dispatch)
            finally:
                for hid in id_to_msg:
                    self._unregister(hid)

        self._thread = threading.Thread(target=_run, name="sonari-hotkeys", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        # JOIN the pump thread before returning. The thread's finally clause
        # unregisters every chord; without the join, a reload's immediate start()
        # re-registers the SAME chords while the old thread still owns them
        # (RegisterHotKey -> 1409), they get dropped, then the old thread's finally
        # unregisters them — leaving ALL hotkeys dark until a daemon restart (H2).
        self._stop.set()
        self._post_quit()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None

    # --- diagnostics ---
    def doctor_rows(self) -> list:
        rows = []
        if self._process_is_elevated():
            rows.append(("hotkey integrity", False,
                         "daemon is elevated; hotkeys won't reach a non-elevated "
                         "Claude window. Don't run as Administrator (UIPI)."))
        if self.collisions:
            owned = ", ".join(c["action"] for c in self.collisions)
            rows.append(("hotkey chords", False,
                         "chord already owned by another app for: {0} "
                         "(rebind in ~/.sonari/keymap.json)".format(owned)))
        else:
            rows.append(("hotkey chords", True, "no collisions"))
        return rows

    def display_combo(self, modifiers: int, key_code: int) -> str:
        parts = [name for mask, name in _MOD_LABELS if modifiers & mask]
        parts.append(_VK_LABELS.get(key_code, "key{0}".format(key_code)))
        return "+".join(parts)

    def uninstall(self) -> None:
        self.stop()

    def install(self, log_path: str, agent_path, launchctl_fn) -> tuple:
        # Windows hotkeys are started by the daemon (start()), not `sonari install`.
        return (True, "Windows hotkeys run in-process; started by the daemon.")
