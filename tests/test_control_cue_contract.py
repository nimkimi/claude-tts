"""The completeness check that did not exist.

Every bug in this receipt is one shape: a per-site opt-in safety list with no
completeness check. Each individual session that added a call site was locally
correct; the defect lives in the seam. This file is the seam's test.
Spec: docs/superpowers/specs/2026-08-28-receipts-design.md 4.2.
"""
import pathlib
import re

from sonari.keymap import ACTIONS, ACTION_MESSAGES, CONTROL_GESTURES


SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "sonari"
SWIFT = (pathlib.Path(__file__).resolve().parent.parent
         / "hotkeyd" / "sonari-hotkeyd.swift")

# Message types hotkeyd sends that are MACHINERY, not operator gestures.
# witness_ping is hotkeyd's own liveness heartbeat.
MACHINERY = {"witness_ping"}


def test_every_action_declares_control_cue():
    """Mandatory, asserted with `in` and never with .get() -- an action added
    without it fails HERE, at the declaration, before anyone has to notice a
    silence."""
    for name, meta in sorted(ACTIONS.items()):
        assert "control_cue" in meta, (
            "action {0!r} does not declare control_cue. Every gesture answers; "
            "say so, or waive it with a reason.".format(name)
        )
    for name, meta in sorted(CONTROL_GESTURES.items()):
        assert "control_cue" in meta, name


def test_a_control_cue_waiver_carries_a_reason():
    for name, meta in sorted({**ACTIONS, **CONTROL_GESTURES}.items()):
        if meta["control_cue"] is False:
            reason = meta.get("control_cue_waiver", "")
            assert isinstance(reason, str) and reason.strip(), (
                "{0} waives control_cue with no reason".format(name)
            )


def test_the_legacy_exempt_flags_are_gone():
    """71 hits on the branch point. Two names for one idea is exactly the
    thing being deleted."""
    scanned = sorted(SRC.rglob("*.py"))
    # An empty corpus satisfies `hits == []` and reports success: a file move
    # or an SRC that stops resolving retires this guard silently. Same idiom as
    # test_cue_contract.py's `assert lits`. Asserted on the CORPUS, never on
    # `hits` -- a guard that required violations would be the guard inverted.
    assert scanned, "no python files under {0} -- the scan is broken".format(SRC)
    hits = [
        "{0}:{1}".format(p.relative_to(SRC), i)
        for p in scanned
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "mute_exempt" in line or "pause_exempt" in line
    ]
    assert hits == [], "legacy exempt flag still present at {0}".format(hits)


def test_every_hotkeyd_message_type_is_a_declared_gesture():
    """hotkeyd sends message types the resolved keymap does not carry. Four of
    them are operator gestures; they need a home in the registry too."""
    literals = set(re.findall(r'\\?"type\\?"\s*:\s*\\?"([a-z_]+)\\?"',
                              SWIFT.read_text(encoding="utf-8")))
    # The corpus, not the violations: `undeclared == set()` is satisfied by a
    # regex that stopped matching or a renamed hotkeyd source, and the guard
    # would report success having read nothing.
    assert literals, (
        "no message types parsed out of {0} -- the scan is broken".format(SWIFT))
    declared = {m["type"] for m in ACTION_MESSAGES.values()}
    declared |= {m["message"]["type"] for m in CONTROL_GESTURES.values()}
    undeclared = literals - declared - MACHINERY
    assert undeclared == set(), (
        "hotkeyd sends {0}, which no registry declares".format(sorted(undeclared))
    )
