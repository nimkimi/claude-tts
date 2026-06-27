"""macOS Carbon key-code + modifier-mask tables (used to resolve the keymap
into the form the Swift hotkeyd reads)."""

KEY_CODES = {
    "s": 1, "r": 15, "d": 2, "l": 37, "v": 9, "o": 31,   # 's' = stop_session
    "f": 3, "p": 35, "m": 46, "j": 38,  # 'p' free, 'm' = stop_all, 'j' = jump_waiting (kVK_ANSI_J)
    "w": 13,                            # 'w' = where_am_i (kVK_ANSI_W)
    "period": 47, ".": 47,
    "rightbracket": 30, "]": 30,
    "leftbracket": 33, "[": 33,
    "equal": 24, "+": 24,               # rate faster (kVK_ANSI_Equal; '+' alias, same physical key)
    "minus": 27, "-": 27,               # rate slower (kVK_ANSI_Minus; '-' alias)
    "tab": 48,                          # cycle sessions (kVK_Tab)
    "return": 36, "enter": 36,          # approve answer_permission (kVK_Return; 'enter' alias)
    "escape": 53, "esc": 53,            # deny answer_permission (kVK_Escape; 'esc' alias)
    # Arrow keys (Carbon virtual key codes), with aliases.
    "left": 123, "leftarrow": 123,
    "right": 124, "rightarrow": 124,
    "down": 125, "downarrow": 125,
    "up": 126, "uparrow": 126,
}

MOD_MASKS = {
    "cmd": 256, "shift": 512,
    "opt": 2048, "option": 2048,
    "ctrl": 4096, "control": 4096,
}

# Default chord on macOS (Ctrl+Cmd, avoids VoiceOver's Ctrl+Opt).
DEFAULT_MODS = ["ctrl", "cmd"]
