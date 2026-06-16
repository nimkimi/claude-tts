"""Win32 virtual-key codes + RegisterHotKey fsModifiers, and the Windows default
chord. Pure data — no OS calls — so it imports on any host for the mock suite."""

# Virtual-Key codes. Letters == ASCII uppercase; OEM keys per WinUser.h.
KEY_CODES = {
    "s": 0x53, "r": 0x52, "d": 0x44, "l": 0x4C, "v": 0x56, "o": 0x4F,
    "period": 0xBE, ".": 0xBE,        # VK_OEM_PERIOD
    "rightbracket": 0xDD, "]": 0xDD,  # VK_OEM_6
    "leftbracket": 0xDB, "[": 0xDB,   # VK_OEM_4
}

# RegisterHotKey fsModifiers (WinUser.h). NOT the Carbon masks.
MOD_MASKS = {
    "alt": 0x0001, "ctrl": 0x0002, "control": 0x0002,
    "shift": 0x0004, "win": 0x0008, "cmd": 0x0008,  # 'cmd' -> Win key for portability
}

# MOD_NOREPEAT (0x4000) is OR-ed in at register time, not part of a chord.
MOD_NOREPEAT = 0x4000

# Default chord: Ctrl+Shift+Alt clears AltGr / Win-reserved / terminal / layout collisions.
DEFAULT_MODS = ["ctrl", "shift", "alt"]
