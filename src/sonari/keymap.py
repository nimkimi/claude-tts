"""Sonari Phase 2 keymap: ALL hotkey logic lives here (the Swift binary is dumb).

Maps key names -> macOS virtual key codes, modifier names -> Carbon masks, and
actions -> speechd protocol messages. Produces the resolved JSON array that the
Swift hotkeyd reads, registers, and sends on fire.
"""
from __future__ import annotations

import json
import os

from sonari.atomicio import atomic_write_json
from sonari.paths import (
    KEYMAP_PATH,
    HOTKEYD_RESOLVED_PATH,
    ensure_sonari_dir,
)

# Key/modifier tables and the default chord are platform-specific; the resolver
# pulls them from the active backend via get_platform() at call time (lazy — no
# import-time OS dispatch). The ONLY sys.platform branch stays in platform/__init__.

# action -> the speechd protocol message it sends. The hotkey-bindable action set
# is deliberately small: navigation, stop/stop-all, pin, jump, and speech-rate. (stop /
# skip stay reachable via the CLI; they are just not hotkey actions.)
ACTION_MESSAGES = {
    # Message-cursor navigation over the current turn (next/prev/first/last item).
    "nav_next": {"type": "nav", "to": "next"},
    "nav_prev": {"type": "nav", "to": "prev"},
    "nav_first": {"type": "nav", "to": "first"},
    "nav_last": {"type": "nav", "to": "last"},
    # Response-to-response navigation (Stage 5): jump a whole turn at a time. Two new
    # `to` values on the existing NAV message (no new protocol type).
    "nav_prev_response": {"type": "nav", "to": "prev_response"},
    "nav_next_response": {"type": "nav", "to": "next_response"},
    "stop_session": {"type": "stop_session"},   # ⌃⌘S: per-session stop/start
    "stop_all": {"type": "stop_all"},   # ⌃⌘M: stop every session
    "pin_toggle": {"type": "pin_toggle"},   # pin/unpin the voice to the current session (#31)
    "jump_waiting": {"type": "jump_waiting"},  # switch voice to a waiting background session
    "faster": {"type": "set_rate", "delta": 25},
    "slower": {"type": "set_rate", "delta": -25},
}

# Shared action -> default key. The chord modifiers are platform-defaulted (macOS:
# Ctrl+Cmd; Windows: Ctrl+Shift+Alt) via the active backend's default_mods().
# Only navigation + stop/stop-all + pin + jump are bound out of the box; faster/slower are
# valid actions but ship UNBOUND (blank by default) so the default keymap stays
# minimal — users add a key for them in keymap.json if they want one.
_DEFAULT_KEYS = {
    "nav_prev": "left", "nav_next": "right", "nav_first": "up", "nav_last": "down",
    "stop_session": "s", "stop_all": "m", "pin_toggle": "p", "jump_waiting": "j",
}


def _keytables():
    """(key_codes, mod_masks) for the active platform (lazy — no import-time dispatch)."""
    from sonari.platform import get_platform
    hk = get_platform().hotkey
    return hk.key_codes(), hk.mod_masks()


def default_keymap() -> dict:
    """The default action->binding map for the active platform (per-OS chord).

    The `_DEFAULT_KEYS` actions all share the platform's `default_mods()` chord.
    `extra_default_bindings()` adds any per-platform binding that the uniform chord
    can't express (response-nav = arrows +Shift on macOS's Ctrl+Cmd; on Windows the base
    chord already spends Shift, so response-nav gets distinct keys [ / ] under it)."""
    from sonari.platform import get_platform
    hk = get_platform().hotkey
    mods = hk.default_mods()
    out = {action: {"key": key, "mods": list(mods)}
           for action, key in _DEFAULT_KEYS.items()}
    out.update(hk.extra_default_bindings())
    return out


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

    Each output entry: {action, keyCode, modifiers, message}. An entry whose key
    is empty/None is treated as UNBOUND and skipped (no hotkey registered) — this
    lets keymap.json explicitly clear an action that has a default binding. Raises
    ValueError on an unknown key name, unknown modifier name, or unknown action.
    """
    if keymap is None:
        keymap = default_keymap()
    key_codes, mod_masks = _keytables()
    resolved = []
    for action, binding in keymap.items():
        if action not in ACTION_MESSAGES:
            raise ValueError("unknown action: {0}".format(action))
        key = (binding.get("key") or "").lower()
        if not key:
            continue                    # explicitly unbound -> no hotkey
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
    fully replaces the default binding for that action. Entries for actions Sonari
    no longer defines are ignored, so a stale keymap.json (e.g. one binding an
    action that was since removed) does not break the whole keymap.
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
        if action not in ACTION_MESSAGES:
            continue                       # drop bindings for removed/unknown actions
        if isinstance(binding, dict):
            merged[action] = {
                "key": binding.get("key"),
                "mods": list(binding.get("mods", [])),
            }
    return merged


def _read_user_keymap() -> dict:
    """The user's raw keymap.json overrides as a dict, or {} if missing/corrupt."""
    try:
        with open(KEYMAP_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_user_keymap(user: dict) -> None:
    """Atomically persist the user's keymap.json overrides."""
    ensure_sonari_dir()
    atomic_write_json(KEYMAP_PATH, user, indent=2)


def unbind_action(action: str) -> None:
    """Persist 'no hotkey' for *action* in the user's keymap.json. If the action has a
    default binding ON THIS PLATFORM, write an explicit unbound override ({"key": null})
    so it overrides that default; if it has no default, just drop any user binding.
    Raises ValueError for an unknown action."""
    if action not in ACTION_MESSAGES:
        raise ValueError("unknown action: {0}".format(action))
    user = _read_user_keymap()
    if action in default_keymap():
        user[action] = {"key": None, "mods": []}
    else:
        user.pop(action, None)
    _write_user_keymap(user)


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
    ensure_sonari_dir()
    atomic_write_json(HOTKEYD_RESOLVED_PATH, resolve_keymap(keymap), indent=None)
    return str(HOTKEYD_RESOLVED_PATH)
