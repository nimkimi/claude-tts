"""R3: a kind added after a user's config.json was written must still reach them.

bootstrap's guard was `if "earcons" not in cfg` -- all-or-nothing on the whole
key. The owner's block has existed since June, so no earcon added after that
date ever reached him, and `repoint` -- the one cue whose entire job is "the
voice just moved because of something you did" -- has been dead for five weeks.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md 4.3.
"""
import json

from sonari import config
from sonari.config import DEFAULTS


# The owner's real block, read from ~/.sonari/config.json on 2026-08-28.
# `ready` is an orphan kind dropped from the defaults by 1c0f5fb on
# 2026-06-27 -- which is what dates this block to before that day.
LEGACY_SIX = {
    "choice": "/System/Library/Sounds/Ping.aiff",
    "error": "/System/Library/Sounds/Sosumi.aiff",
    "permission": "/System/Library/Sounds/Funk.aiff",
    "plan": "/System/Library/Sounds/Submarine.aiff",
    "ready": "/System/Library/Sounds/Glass.aiff",
    "turn_done": "/System/Library/Sounds/Tink.aiff",
}


def test_defaults_carries_the_earcon_table():
    assert "earcons" in DEFAULTS
    assert DEFAULTS["earcons"]["repoint"] == "/System/Library/Sounds/Bottle.aiff"
    assert len(DEFAULTS["earcons"]) == 13


def test_load_config_merges_new_earcon_kinds_into_a_legacy_config(tmp_path):
    """THE receipt. His config predates `repoint`; the merge must heal it."""
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text(
        json.dumps({"voice": "Voice 1", "rate": 225, "earcons": LEGACY_SIX}),
        encoding="utf-8",
    )
    loaded = config.load_config()
    assert loaded["earcons"]["repoint"] == "/System/Library/Sounds/Bottle.aiff"


def test_a_legacy_override_survives_the_merge(tmp_path):
    """His five real overrides and his `ready` orphan are not rewritten."""
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text(
        json.dumps({"earcons": LEGACY_SIX}), encoding="utf-8")
    loaded = config.load_config()
    assert loaded["earcons"]["choice"] == "/System/Library/Sounds/Ping.aiff"
    assert loaded["earcons"]["ready"] == "/System/Library/Sounds/Glass.aiff"
    assert len(loaded["earcons"]) == 14   # 13 defaults + the `ready` orphan


def test_an_explicit_null_still_mutes_a_cue(tmp_path):
    """Regression pin. Muting is done with a flag, not by deleting a key --
    but an explicit null must keep working, and _deep_merge writes an
    override value even when it is None."""
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text(
        json.dumps({"earcons": {"repoint": None}}), encoding="utf-8")
    loaded = config.load_config()
    assert loaded["earcons"]["repoint"] is None


import pathlib
import re


SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "sonari"


def test_only_one_resolver_reads_the_earcon_table():
    """Three sources of truth for one table is the drift this receipt closes."""
    scanned = sorted(SRC.rglob("*.py"))
    # The corpus, not the violations. This literal grep is also the SOLE guard
    # on two of the three collapsed resolvers (host._asset_path and
    # keymap._witness_entry), so a scan that silently stops scanning takes
    # those with it. Idiom from test_cue_contract.py's `assert lits`.
    assert scanned, "no python files under {0} -- the scan is broken".format(SRC)
    hits = [
        "{0}:{1}".format(p.relative_to(SRC), i)
        for p in scanned
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "_FALLBACK_EARCONS" in line
    ]
    assert hits == [], "second earcon table still alive at {0}".format(hits)


def test_the_platform_backend_no_longer_owns_a_default_table():
    from sonari.platform.macos import earcon
    assert not hasattr(earcon, "_DEFAULTS")
    assert not hasattr(earcon.MacEarconBackend, "default_earcons")


def test_every_registered_cue_has_a_default_asset():
    """Forward guard, green from here on: a future cue cannot ship DOA.

    The five exempt kinds are not tone assets: speech and summary_voice are
    spoken, pitch_up/pitch_down resolve through Speaker.pitch_asset, and
    callsign is a rendered spearcon.
    """
    from sonari.config import DEFAULTS
    from sonari.cues import CUES

    ASSET_EXEMPT = {"speech", "summary_voice", "pitch_up", "pitch_down",
                    "callsign"}
    missing = set(CUES) - ASSET_EXEMPT - set(DEFAULTS["earcons"])
    assert missing == set(), (
        "registered cue(s) {0} have no default asset and would ship "
        "silent".format(sorted(missing))
    )
