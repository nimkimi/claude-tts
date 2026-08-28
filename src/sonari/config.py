"""Sonari persisted configuration: DEFAULTS plus load/save against CONFIG_PATH."""
from __future__ import annotations

import json

from sonari.atomicio import atomic_write_json
from sonari.paths import CONFIG_PATH, ensure_sonari_dir

DEFAULTS = {
    "voice": None,
    "rate": 200,
    "verbosity": "everything",
    "background_policy": "earcon_only",
    "history_cap": 200,
    "backlog_cap": 200,
    "minqueue": 1,
    "focus_follow": True,
    "spearcon_voice": "Samantha",
    "spearcon_rate": 525,
    "summarizer": "auto",        # SP5 host-LLM catch-up: auto|claude|off
    "summary_voice": "auto",     # LLM-body voice; auto=first curated say voice != main, else main
    "summary_model": "haiku",    # claude -p --model for the summary (owner override)
    "restore_max_age_hours": 24, # SP6: max age (h) of a restored pile before drop-on-load (§4.4)
    "submit_ack_enabled": False, # D2 §6.1: prompt-submit ack tone, dark pending the owner's ear
    "keepalive_enabled": True,   # Bluetooth keep-alive (holds the audio device open while live)
    # Every registered cue's default asset. In DEFAULTS (not bootstrap) so
    # load_config()'s per-key _deep_merge heals an existing install: a kind
    # added after a user's config.json was written still reaches them.
    # bootstrap's old whole-key guard could not -- it is why `repoint` was
    # silent for five weeks. Reverses edd0135 deliberately; see the plan.
    "earcons": {
        "permission": "/System/Library/Sounds/Funk.aiff",
        "choice":     "/System/Library/Sounds/Ping.aiff",
        "plan":       "/System/Library/Sounds/Submarine.aiff",
        "error":      "/System/Library/Sounds/Sosumi.aiff",
        "turn_done":  "/System/Library/Sounds/Tink.aiff",
        # W6/W7 failure taxonomy: distinct KINDS, one shared tone — the owner's
        # ear ruling (ear-batch-2 slot 1, 2026-08-01): every failure sounds Sosumi,
        # like plain `error`; the paired WORD carries the class. Kinds stay
        # distinct for words/config; a future re-split is config-level.
        "error_misdirected": "/System/Library/Sounds/Sosumi.aiff",
        "error_system":      "/System/Library/Sounds/Sosumi.aiff",
        "permission_expired": "/System/Library/Sounds/Sosumi.aiff",
        # D2 §6 silences (assets ratified by the ear-batch-2 audition 2026-08-01;
        # swaps stay config-level).
        "submit_ack": "/System/Library/Sounds/Morse.aiff",  # prompt-submit ack (dark by default)
        "repoint":    "/System/Library/Sounds/Bottle.aiff", # workspace repoint on click
        "crossing":   "/System/Library/Sounds/Frog.aiff",   # keep-going miss marker (prelude)
        # §7 witness alarms — played via raw spawn (hotkeyd / the daemon), never
        # the transient arbiter. Assets ratified (ear-batch-2, 2026-08-01).
        "alarm_daemon_down":  "/System/Library/Sounds/Hero.aiff",
        # Basso not Glass (ear-batch-2 slot 11): Glass doubles as the owner's
        # out-of-band chat attention chime, so the alarm gets its own timbre.
        "alarm_hotkeys_down": "/System/Library/Sounds/Basso.aiff",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Return a new dict: override applied onto base, recursing into nested dicts."""
    result = {
        k: _deep_merge(v, {}) if isinstance(v, dict) else v
        for k, v in base.items()
    }
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    """Deep-merge persisted CONFIG_PATH over a copy of DEFAULTS.

    Missing or corrupt (non-JSON / non-object) files yield a fresh DEFAULTS copy.
    """
    base = _deep_merge(DEFAULTS, {})
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            persisted = json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        return base
    if not isinstance(persisted, dict):
        return base
    return _deep_merge(base, persisted)


def save_config(cfg: dict) -> None:
    """Atomically persist cfg to CONFIG_PATH."""
    ensure_sonari_dir()
    atomic_write_json(CONFIG_PATH, cfg, indent=2)
