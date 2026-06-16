"""Sonari Phase 2 keymap: ALL hotkey logic lives here (the Swift binary is dumb).

Maps key names -> macOS virtual key codes, modifier names -> Carbon masks, and
actions -> speechd protocol messages. Produces the resolved JSON array that the
Swift hotkeyd reads, registers, and sends on fire.
"""
from __future__ import annotations

import json
import os

from sonari.paths import (
    KEYMAP_PATH,
    HOTKEYD_RESOLVED_PATH,
    SONARI_DIR,
    ensure_sonari_dir,
)

# Key/modifier tables and the default chord are platform-specific; the resolver
# pulls them from the active backend via get_platform() at call time (lazy — no
# import-time OS dispatch). The ONLY sys.platform branch stays in platform/__init__.

# action -> the speechd protocol message it sends.
ACTION_MESSAGES = {
    "stop": {"type": "stop"},
    "repeat": {"type": "repeat"},
    "skip": {"type": "skip"},
    # Message-cursor navigation over the current turn (next/prev/first/last item).
    "nav_next": {"type": "nav", "to": "next"},
    "nav_prev": {"type": "nav", "to": "prev"},
    "nav_first": {"type": "nav", "to": "first"},
    "nav_last": {"type": "nav", "to": "last"},
    "pause": {"type": "pause"},     # play/pause toggle
    "mute": {"type": "mute"},       # sticky per-session mute toggle
    "jump_decision": {"type": "jump_decision"},
    "catch_up": {"type": "catch_up"},
    "faster": {"type": "set_rate", "delta": 25},
    "slower": {"type": "set_rate", "delta": -25},
    "cycle_verbosity": {"type": "cycle_verbosity"},
    "reread_options": {"type": "reread_options"},
}

# Shared action -> key. The chord modifiers are platform-defaulted (macOS:
# Ctrl+Cmd; Windows: Ctrl+Shift+Alt) via the active backend's default_mods().
_DEFAULT_KEYS = {
    "stop": "s", "repeat": "r", "skip": ".", "jump_decision": "d",
    "catch_up": "l", "faster": "]", "slower": "[",
    "cycle_verbosity": "v", "reread_options": "o",
}


def _keytables():
    """(key_codes, mod_masks) for the active platform (lazy — no import-time dispatch)."""
    from sonari.platform import get_platform
    hk = get_platform().hotkey
    return hk.key_codes(), hk.mod_masks()


def default_keymap() -> dict:
    """The default action->binding map for the active platform (per-OS chord)."""
    from sonari.platform import get_platform
    mods = get_platform().hotkey.default_mods()
    return {action: {"key": key, "mods": list(mods)}
            for action, key in _DEFAULT_KEYS.items()}


def _copy_keymap(km: dict) -> dict:
    """Deep-ish copy: each action maps to a fresh {key, mods[...]} dict."""
    out = {}
    for action, binding in km.items():
        out[action] = {
            "key": binding.get("key"),
            "mods": list(binding.get("mods", [])),
        }
    return out


def resolve_keymap(keymap=None) -> list:
    """Resolve an action->binding map into the Swift-facing array.

    Each output entry: {action, keyCode, modifiers, message}. Raises ValueError
    on an unknown key name, unknown modifier name, or unknown action.
    """
    if keymap is None:
        keymap = default_keymap()
    key_codes, mod_masks = _keytables()
    resolved = []
    for action, binding in keymap.items():
        if action not in ACTION_MESSAGES:
            raise ValueError("unknown action: {0}".format(action))
        key = (binding.get("key") or "").lower()
        if key not in key_codes:
            raise ValueError("unknown key: {0}".format(binding.get("key")))
        mask = 0
        for mod in binding.get("mods", []):
            m = (mod or "").lower()
            if m not in mod_masks:
                raise ValueError("unknown modifier: {0}".format(mod))
            mask |= mod_masks[m]
        resolved.append({
            "action": action,
            "keyCode": key_codes[key],
            "modifiers": mask,
            "message": json.dumps(ACTION_MESSAGES[action]),
        })
    return resolved


def load_keymap() -> dict:
    """Merge the user's KEYMAP_PATH over a copy of DEFAULT_KEYMAP.

    Missing or corrupt files yield a fresh DEFAULT_KEYMAP copy. A user entry
    fully replaces the default binding for that action.
    """
    merged = _copy_keymap(default_keymap())
    try:
        with open(KEYMAP_PATH, "r", encoding="utf-8") as fh:
            user = json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        return merged
    if not isinstance(user, dict):
        return merged
    for action, binding in user.items():
        if isinstance(binding, dict):
            merged[action] = {
                "key": binding.get("key"),
                "mods": list(binding.get("mods", [])),
            }
    return merged


def write_default_keymap_if_absent() -> bool:
    """Write DEFAULT_KEYMAP to KEYMAP_PATH if it does not exist. Returns True
    iff it wrote the file."""
    if os.path.exists(KEYMAP_PATH):
        return False
    ensure_sonari_dir()
    with open(KEYMAP_PATH, "w", encoding="utf-8") as fh:
        json.dump(default_keymap(), fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    return True


def write_resolved(keymap=None) -> str:
    """Atomically write the resolved array to HOTKEYD_RESOLVED_PATH; return its
    path. Uses load_keymap() when no explicit keymap is given."""
    if keymap is None:
        keymap = load_keymap()
    data = json.dumps(resolve_keymap(keymap))
    ensure_sonari_dir()
    tmp_path = SONARI_DIR / (HOTKEYD_RESOLVED_PATH.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, HOTKEYD_RESOLVED_PATH)
    return str(HOTKEYD_RESOLVED_PATH)
