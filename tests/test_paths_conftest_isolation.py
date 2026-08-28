"""Guard: every filesystem-path constant paths.py declares at module level
must be repointed by the shared isolation list in tests/_isolation.py -- or be
explicitly, justifiably allowlisted here. Mirrors test_no_os_branch_in_core.py's
convention: plain substring/regex text matching over source, not AST walking.

The list used to live inline in tests/conftest.py's autouse fixture; it moved to
tests/_isolation.py so an ad-hoc script can apply the identical list without
pytest (partial isolation by exactly such a script caused the 2026-08-15
outage). The last test below is the trip-wire for what that move cost: conftest
must still CALL the list, or this guard would pass while nothing applied it.

This is the guard that would have caught RAISE_BIN_PATH and KOKORO_VENV
directly: both are paths.py module constants bound at import time from
SONARI_DIR, and neither was in the repoint list until this task
closed the gap. Full inventory:
/Users/Nima.Hakimi/projects/private/sonari/.superpowers/sdd/hermeticity-audit.md
(main checkout, read-only reference -- not part of this worktree's history).
"""
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PATHS_PY = REPO_ROOT / "src" / "sonari" / "paths.py"
ISOLATION_PY = REPO_ROOT / "tests" / "_isolation.py"
CONFTEST_PY = REPO_ROOT / "tests" / "conftest.py"

# Module-level constants in paths.py that are legitimately NOT repointed by
# the isolation list. Empty today -- every real path constant IS
# isolated (that's the point of this guard existing). Add an entry here ONLY
# with a one-line reason; anything else belongs in the fixture, not here.
ALLOWLIST = {
    # "NAME": "one-line reason it must legitimately stay bound to the real path",
}

# Deliberately matches EVERY uppercase module-level assignment, not just
# names ending _PATH/_DIR/_VENV -- a suffix filter would be a silent escape
# hatch (a future SONARI_HOME or APP_ROOT constant could skip the guard
# without anyone choosing that). Costs nothing today: every current
# constant in paths.py happens to be a path anyway.
_ASSIGN_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)
# Matches both spellings by design: the fixture's old `monkeypatch.setattr(
# paths, "NAME", ...)` and the isolation list's `_setattr(paths, "NAME", ...)`
# shim, which dispatches to monkeypatch or plain setattr depending on the caller.
_SETATTR_RE = re.compile(r'setattr\(\s*paths\s*,\s*["\']([A-Z][A-Z0-9_]*)["\']')


def _paths_py_constants():
    text = PATHS_PY.read_text(encoding="utf-8")
    return set(_ASSIGN_RE.findall(text))


def _isolation_repointed_names():
    text = ISOLATION_PY.read_text(encoding="utf-8")
    return set(_SETATTR_RE.findall(text))


def test_every_paths_constant_is_isolated_or_allowlisted():
    constants = _paths_py_constants()
    repointed = _isolation_repointed_names()
    uncovered = constants - repointed - set(ALLOWLIST)
    assert not uncovered, (
        "src/sonari/paths.py declares {0} but tests/_isolation.py's "
        "isolate_paths() does not repoint it (and it is not in "
        "this guard's ALLOWLIST in tests/test_paths_conftest_isolation.py). "
        "Either add _setattr(paths, \"<NAME>\", <root-relative path>) to "
        "isolate_paths(), or add the name to ALLOWLIST above "
        "with a one-line reason it must legitimately stay bound to the real "
        "path.".format(sorted(uncovered))
    )


def test_conftest_still_applies_the_isolation_list():
    """Trip-wire for what moving the list out of conftest.py cost.

    While the repoints WERE the autouse fixture's body, guarding the list and
    guarding that the suite applies it were the same assertion. They are not
    anymore: a conftest that stopped calling isolate_paths() would leave the
    test above passing while every test in the suite ran against the
    developer's real ~/.sonari. tests/test_isolation_helper.py proves the same
    thing behaviourally (that one also catches a dropped autouse=True); this
    one names the mechanism, so the failure says what to restore.
    """
    text = CONFTEST_PY.read_text(encoding="utf-8")
    assert "from _isolation import isolate_paths" in text, (
        "tests/conftest.py no longer imports the shared isolation list -- "
        "restore it, or this guard is checking a list nobody applies")
    assert re.search(r"^\s*isolate_paths\(", text, re.MULTILINE), (
        "tests/conftest.py imports isolate_paths but never calls it -- the "
        "suite is running unisolated against the real ~/.sonari")


def test_allowlist_entries_are_still_declared_in_paths_py():
    """Catches allowlist rot: an entry for a constant that no longer exists."""
    constants = _paths_py_constants()
    stale = set(ALLOWLIST) - constants
    assert not stale, (
        "ALLOWLIST references constants no longer declared in paths.py: "
        "{0} -- remove the stale entry.".format(sorted(stale))
    )
