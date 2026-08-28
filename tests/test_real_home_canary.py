"""The receipt the canary never had.

tests/conftest.py's `_real_home_canary` is the detective half of the D0.1
refusal: after every session it re-stats the paths the suite has DESTROYED
TWICE (~/.sonari, ~/.sonari/venv, ~/.sonari/app, ~/.local/bin/sonari and the
two LaunchAgent plists) and fails the run if any of them moved.

Until this file existed, neutering its assertion -- `assert not changed` ->
`assert True` -- left the whole suite green. It was the only guard on this
branch never proved to bite, and it is the one guard whose failure is not
recoverable: every other defect here costs a re-run, this one costs his
install and, the last time, nineteen hours of silence.

Proving it BEHAVIOURALLY would mean writing into the real ~/.sonari, which is
the accident itself. So conftest lifts the verdict into
`_assert_real_home_untouched(before, after)` -- the same shape it already uses
for `_assert_no_silent_cues` -- and these tests hand it fabricated snapshots.
The mechanism under test is the real one; only the stats are invented.
"""
import pathlib
import re

import pytest

import conftest


CONFTEST_PY = pathlib.Path(conftest.__file__).resolve()

# The identity tuple _canary_stat returns for a path the daemon writes into.
# Its exact contents do not matter here -- only that two snapshots can differ.
_STAT_A = (111,)
_STAT_B = (222,)


def _snapshot(value=_STAT_A):
    return {p: value for p in conftest._CANARY_PATHS}


def test_the_canary_fires_when_a_watched_path_moves():
    """A replaced file: same path, different inode. This is what an errant
    write to ~/.sonari/config.json or a re-created LaunchAgent looks like."""
    before = _snapshot()
    after = dict(before)
    victim = conftest._REAL_HOME / ".local" / "bin" / "sonari"
    after[victim] = _STAT_B
    with pytest.raises(AssertionError) as excinfo:
        conftest._assert_real_home_untouched(before, after)
    assert "TOUCHED THE REAL INSTALL" in str(excinfo.value)
    # It must NAME the path. The canary's whole reason for existing over the
    # refusal alone is that it is the only thing that would have identified
    # the culprit either of the two times this happened.
    assert str(victim) in str(excinfo.value), str(excinfo.value)


def test_the_canary_fires_when_a_watched_path_is_deleted():
    """The shape of BOTH recorded destructions -- uninstall_kokoro() rmtree'ing
    ~/.sonari/venv, and the probe that removed ~/.sonari/daemon.lock. A deleted
    path stats as None, and None must not read as 'unchanged'."""
    before = _snapshot()
    after = dict(before)
    victim = conftest._REAL_HOME / ".sonari" / "venv"
    after[victim] = None
    with pytest.raises(AssertionError, match="TOUCHED THE REAL INSTALL"):
        conftest._assert_real_home_untouched(before, after)


def test_the_canary_fires_when_a_watched_path_appears():
    """The reverse of a delete, and just as much a write into his install: a
    path that did not exist before the run and does after it."""
    before = _snapshot(value=None)
    after = dict(before)
    after[conftest._REAL_HOME / ".sonari" / "app"] = _STAT_A
    with pytest.raises(AssertionError, match="TOUCHED THE REAL INSTALL"):
        conftest._assert_real_home_untouched(before, after)


def test_the_canary_stays_quiet_when_nothing_moved():
    """The other direction. A canary that always fires gets switched off, and
    then it is not there the time it matters -- which is why the watched set is
    split into identity-only and full-stat in the first place."""
    before = _snapshot()
    conftest._assert_real_home_untouched(before, dict(before))   # no raise


def test_the_canary_watches_the_paths_the_two_outages_destroyed():
    """Corpus pin, in the shape of G0b's four: an EMPTY _CANARY_PATHS makes
    `changed` unconditionally empty, so the verdict above would pass while
    watching nothing. Names the two specific paths rather than only counting,
    because a set that quietly lost ~/.sonari/venv is exactly the 2026-08-15
    outage going unwatched again."""
    watched = set(conftest._CANARY_PATHS)
    assert watched, "the canary watches nothing -- the guard is broken"
    for required in (conftest._REAL_HOME / ".sonari" / "venv",
                     conftest._REAL_HOME / ".local" / "bin" / "sonari"):
        assert required in watched, (
            "{0} was destroyed by this suite and is no longer "
            "watched".format(required))


def test_the_canary_is_wired_as_a_session_scoped_autouse_fixture():
    """Nobody requests this fixture by name, so autouse is the whole wiring;
    and session scope is what makes it a post-RUN check rather than a
    per-test one it would be far too slow to be."""
    fixture = conftest._real_home_canary
    # pytest 9 renamed the marker attribute from _pytestfixturefunction to
    # _fixture_function_marker (installed venv is 9.0.3).
    marker = fixture._fixture_function_marker
    assert marker.autouse is True
    assert marker.scope == "session"


def test_the_canary_fixture_still_calls_the_extracted_verdict():
    """Trip-wire for what lifting the assertion out of the fixture cost, in the
    idiom test_paths_conftest_isolation.py already uses for the same seam.

    While the comparison WAS the fixture's body, proving the comparison bites
    and proving the suite runs it were one assertion. They are not anymore: a
    fixture that stopped calling _assert_real_home_untouched would leave every
    test above green while nothing watched his install at all.
    """
    text = CONFTEST_PY.read_text(encoding="utf-8")
    assert re.search(r"^\s*_assert_real_home_untouched\(", text, re.MULTILINE), (
        "tests/conftest.py defines _assert_real_home_untouched but never calls "
        "it -- the real-home canary is not armed")
