"""Rows -> one spoken sentence. Pure and total: no I/O, no clock, no config.

Failing checks are NAMED, not merely counted: the shipped rule forbids a
relaying session from glossing doctor, and a count-only verdict would gloss it
by ear instead. Enumeration is self-bounding — names appear only on failure.
"""
from __future__ import annotations

from sonari.cli import checkmeta

# PROVISIONAL (ear-batch-4) — every literal in this module.
_NONE = "Sonari ran no checks."
_HEALTHY = "Sonari is healthy. {n} check{s} passed."
_UNHEALTHY = "Sonari is unhealthy. {n} check{s} failed: {names}."


def verdict(rows) -> str:
    """Fold doctor rows into one sayable sentence."""
    rows = list(rows or [])
    if not rows:
        return _NONE
    failed = [c for c, ok, _ in rows if not ok and not checkmeta.is_warn(c)]
    if not failed:
        # Count what actually PASSED, not len(rows). A warn-class row is
        # excluded from the spoken failure list by design, but it did not pass
        # — counting it said "21 checks passed" when 20 did, which is a spoken
        # statement of fact that is false.
        n = sum(1 for _, ok, _ in rows if ok)
        return _HEALTHY.format(n=n, s="" if n == 1 else "s")
    n = len(failed)
    return _UNHEALTHY.format(
        n=n, s="" if n == 1 else "s",
        names=", ".join(checkmeta.spoken_name(c) for c in failed))
