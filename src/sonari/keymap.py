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

# Every user-facing hotkey verb, with the metadata every consumer derives from:
# the protocol message (hotkeyd), label + doc (generated README table, keymap
# CLI), teach (learn mode / first-encounter hints — each marked individually
# below). An action with no default key ships unbound; "proposed" is the chord
# the docs advertise for it.
ACTIONS = {
    "nav_next": {
        "message": {"type": "nav", "to": "next"},
        "label": "Next item",
        # wording provisional, pending owner ear-pass
        "teach": "Next item. Step forward one item in the current turn.",
        "doc": "Step forward one item in the current turn",
        "proposed": None,
    },
    "nav_prev": {
        "message": {"type": "nav", "to": "prev"},
        "label": "Previous item",
        # wording provisional, pending owner ear-pass
        "teach": "Previous item. Step back one item in the current turn.",
        "doc": "Step back one item in the current turn",
        "proposed": None,
    },
    "nav_prev_response": {
        "message": {"type": "nav", "to": "prev_response"},
        "label": "Previous response",
        # wording provisional, pending owner ear-pass
        "teach": "Previous response. Jump back a whole reply.",
        "doc": "Jump back one whole reply",
        "proposed": None,
    },
    "nav_next_response": {
        "message": {"type": "nav", "to": "next_response"},
        "label": "Next response",
        # wording provisional, pending owner ear-pass
        "teach": "Next response. Jump forward a whole reply.",
        "doc": "Jump forward one whole reply",
        "proposed": None,
    },
    "stop_session": {
        "message": {"type": "stop_session"},
        "label": "Stop or resume this session",
        # wording provisional, pending owner ear-pass
        "teach": "Stop or resume. Silences this session's voice; press again to resume.",
        "doc": "Stop/resume the current session's voice",
        "proposed": None,
    },
    "stop_all": {
        "message": {"type": "stop_all"},
        "label": "Stop everything",
        # wording provisional, pending owner ear-pass
        "teach": "Stop everything. Silences every session until resumed.",
        "doc": "Stop every session's voice",
        "proposed": None,
    },
    "jump_waiting": {
        "message": {"type": "jump_waiting"},
        "label": "Jump to a waiting session",
        # wording provisional, pending owner ear-pass
        "teach": "Jump to a waiting session. Moves the voice to a session that needs you.",
        "doc": "Move the voice to a background session that is waiting",
        "proposed": None,
    },
    "jump_decision": {
        "message": {"type": "jump_decision"},
        "label": "Jump to the decision",
        # wording provisional, pending owner ear-pass
        "teach": "Jump to the decision. Re-speaks the question that is waiting for an answer.",
        "doc": "Jump to the pending decision",
        "proposed": None,
    },
    "repeat_last": {
        "message": {"type": "repeat_last"},
        "label": "Repeat",
        # wording provisional, pending owner ear-pass
        "teach": "Repeat. Re-speaks the last thing Sonari said.",
        "doc": "Re-speak the last utterance",
        "proposed": None,
    },
    "chooser_step_next": {
        "message": {"type": "chooser_step", "direction": "next"},
        "label": "Session chooser, next",
        # wording provisional, pending owner ear-pass
        "teach": "Session chooser. Hold the chord and press Tab to browse sessions; release to switch.",
        "doc": "Browse sessions forward (hold chord, tap Tab)",
        "proposed": None,
    },
    "chooser_step_prev": {
        "message": {"type": "chooser_step", "direction": "prev"},
        "label": "Session chooser, previous",
        # wording provisional, pending owner ear-pass
        "teach": "Session chooser, backwards.",
        "doc": "Browse sessions backward",
        "proposed": None,
    },
    "where_am_i": {
        "message": {"type": "where_am_i"},
        "label": "Where am I?",
        # wording provisional, pending owner ear-pass
        "teach": "Where am I. Speaks a one-breath status of every session.",
        "doc": "Speak a terse status of all sessions",
        "proposed": None,
    },
    "approve": {
        "message": {"type": "answer_permission", "behavior": "allow"},
        "label": "Approve",
        # wording provisional, pending owner ear-pass
        "teach": "Approve. Answers yes to the pending permission request.",
        "doc": "Approve the pending permission request",
        "proposed": None,
    },
    "deny": {
        "message": {"type": "answer_permission", "behavior": "deny"},
        "label": "Deny",
        # wording provisional, pending owner ear-pass
        "teach": "Deny. Answers no to the pending permission request.",
        "doc": "Deny the pending permission request",
        "proposed": None,
    },
    "faster": {
        "message": {"type": "set_rate", "delta": 25},
        "label": "Faster",
        # wording provisional, pending owner ear-pass
        "teach": "Faster. Raises the speech rate.",
        "doc": "Speak faster",
        "proposed": None,
    },
    "slower": {
        "message": {"type": "set_rate", "delta": -25},
        "label": "Slower",
        # wording provisional, pending owner ear-pass
        "teach": "Slower. Lowers the speech rate.",
        "doc": "Speak slower",
        "proposed": None,
    },
    "reread_options": {
        "message": {"type": "reread_options"},
        "label": "Re-read the options",
        # wording provisional, pending owner ear-pass
        "teach": "Re-read the options. Speaks the pending question's choices again.",
        "doc": "Re-speak the pending question's options",
        "proposed": None,
    },
    "cycle_verbosity": {
        "message": {"type": "cycle_verbosity"},
        "label": "Cycle verbosity",
        # wording provisional, pending owner ear-pass
        "teach": "Cycle verbosity. Steps between everything, medium, and quiet.",
        "doc": "Cycle verbosity: everything / medium / quiet",
        "proposed": None,
    },
    "skip_pile": {
        "message": {"type": "skip_pile"},
        "label": "Skip the pile",
        # wording provisional, pending owner ear-pass
        "teach": "Skip the pile. Settles this session's unheard backlog without reading it.",
        "doc": "Settle the unheard backlog without hearing it",
        "proposed": {"key": "down", "mods": ["ctrl", "cmd", "shift"]},
    },
    "catch_up": {
        "message": {"type": "catch_up"},
        "label": "Catch up",
        # wording provisional, pending owner ear-pass
        "teach": "Catch up. Summarizes what you have not heard, then marks it heard.",
        "doc": "Hear a summary of the unheard backlog",
        "proposed": {"key": "l", "mods": ["ctrl", "cmd"]},
    },
    "learn_mode": {
        "message": {"type": "learn_mode"},
        "label": "Learn mode",
        # wording provisional, pending owner ear-pass
        "teach": "Learn mode. Keys describe themselves instead of acting.",
        "doc": "Toggle learn mode: keys speak what they do instead of doing it",
        "proposed": None,   # chord chosen at ear-batch #1
    },
    "query_actions": {
        "message": {"type": "query_actions"},
        "label": "What can I do?",
        # wording provisional, pending owner ear-pass
        "teach": "What can I do. Speaks the keys that matter right now.",
        "doc": "Speak the actions available right now",
        "proposed": None,   # chord chosen at ear-batch #1
    },
}

ACTION_MESSAGES = {name: meta["message"] for name, meta in ACTIONS.items()}

_MOD_DISPLAY = {"ctrl": "Ctrl", "cmd": "Cmd", "shift": "Shift", "alt": "Alt"}
_KEY_DISPLAY = {"left": "←", "right": "→", "up": "↑", "down": "↓",
                "return": "Return", "escape": "Esc", "tab": "Tab",
                "equal": "=", "minus": "-"}


def combo_display(binding) -> str:
    """'Ctrl+Cmd+W'-style rendering of a {key, mods} binding."""
    parts = [_MOD_DISPLAY.get(m, m.title()) for m in binding.get("mods", [])]
    key = (binding.get("key") or "")
    parts.append(_KEY_DISPLAY.get(key, key.upper()))
    return "+".join(parts)


def hotkey_rows(bindings=None) -> list:
    """Doc/CLI rows for every action: bound rows first (registry order), then
    unbound. Defaults to the PLATFORM DEFAULT keymap (what generated docs like
    the README and scripts/gen_docs.py describe) when *bindings* is omitted.
    Callers that need to reflect what is actually bound right now — e.g. the
    `sonari keymap` listing — pass load_keymap() explicitly."""
    if bindings is None:
        bindings = default_keymap()
    bound, unbound = [], []
    for name, meta in ACTIONS.items():
        binding = bindings.get(name)
        row = {
            "action": name,
            "label": meta["label"],
            "doc": meta["doc"],
            "combo": combo_display(binding) if binding and binding.get("key") else None,
            "proposed": (combo_display(meta["proposed"]) if meta.get("proposed") else None),
        }
        (bound if row["combo"] else unbound).append(row)
    return bound + unbound


def action_for_message(msg) -> "str | None":
    """Reverse lookup: the action whose protocol message equals *msg* exactly.
    Hotkeyd sends the registered message verbatim, so equality is safe."""
    for name, meta in ACTIONS.items():
        if meta["message"] == msg:
            return name
    return None


# Shared action -> default key. The chord modifiers are platform-defaulted (macOS:
# Ctrl+Cmd; Windows: Ctrl+Shift+Alt) via the active backend's default_mods().
# Per-platform extras (chooser, response-nav) are NOT listed here — they live in
# extra_default_bindings(), which is what frees ⌃⌘↑/↓ for response-nav.
_DEFAULT_KEYS = {
    "nav_prev": "left", "nav_next": "right",
    "stop_session": "s", "stop_all": "m", "jump_waiting": "j",
    "jump_decision": "d", "where_am_i": "w", "repeat_last": "r",
    "approve": "return", "deny": "escape",
    "faster": "equal", "slower": "minus",
    "reread_options": "o", "cycle_verbosity": "v",
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
    can't express (on macOS's Ctrl+Cmd: response-nav = ↑/↓ and the chooser = Tab/⇧Tab; on
    Windows the base chord already spends Shift, so response-nav gets distinct keys)."""
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


def _witness_entry() -> dict:
    """The §7 witness-config entry appended to the resolved array. keyCode-less:
    an old hotkeyd binary's loadEntries guard requires keyCode and skips it; a
    new binary reads it by action name. The asset resolves config-first;
    load_config()'s merge of config.DEFAULTS keeps it from ever being silently
    unconfigured; hotkeyd's compiled-in defaults are the last resort, so a
    STALE resolved file cannot disable the alarm either. Words ratified
    (ear-batch-2, 2026-08-01)."""
    from sonari.config import load_config
    # One resolver. This site is why the table must live in load_config and
    # not in bootstrap: keymap runs in the hotkeyd/CLI process, which never
    # executes bootstrap.main().
    asset = (load_config().get("earcons") or {}).get("alarm_daemon_down")
    return {"action": "witness_config", "alarmAsset": asset,
            "alarmWords": "Sonari is down.", "alarmEnabled": True}


def write_resolved(keymap=None) -> str:
    """Atomically write the resolved array (bindings + the witness-config entry)
    to HOTKEYD_RESOLVED_PATH; return its path. Uses load_keymap() when no
    explicit keymap is given."""
    if keymap is None:
        keymap = load_keymap()
    ensure_sonari_dir()
    atomic_write_json(HOTKEYD_RESOLVED_PATH,
                      resolve_keymap(keymap) + [_witness_entry()], indent=None)
    return str(HOTKEYD_RESOLVED_PATH)
