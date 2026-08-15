"""Guard: every filesystem-path constant paths.py declares at module level
must be repointed by tests/conftest.py's isolation fixture -- or be
explicitly, justifiably allowlisted here. Mirrors test_no_os_branch_in_core.py's
convention: plain substring/regex text matching over source, not AST walking.

This is the guard that would have caught RAISE_BIN_PATH and KOKORO_VENV
directly: both are paths.py module constants bound at import time from
SONARI_DIR, and neither was in conftest.py's repoint list until this task
closed the gap. Full inventory:
/Users/Nima.Hakimi/projects/private/sonari/.superpowers/sdd/hermeticity-audit.md
(main checkout, read-only reference -- not part of this worktree's history).
"""
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PATHS_PY = REPO_ROOT / "src" / "sonari" / "paths.py"
CONFTEST_PY = REPO_ROOT / "tests" / "conftest.py"

# Module-level constants in paths.py that are legitimately NOT repointed by
# conftest's isolation fixture. Empty today -- every real path constant IS
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
_SETATTR_RE = re.compile(r'setattr\(\s*paths\s*,\s*["\']([A-Z][A-Z0-9_]*)["\']')


def _paths_py_constants():
    text = PATHS_PY.read_text(encoding="utf-8")
    return set(_ASSIGN_RE.findall(text))


def _conftest_repointed_names():
    text = CONFTEST_PY.read_text(encoding="utf-8")
    return set(_SETATTR_RE.findall(text))


def test_every_paths_constant_is_isolated_or_allowlisted():
    constants = _paths_py_constants()
    repointed = _conftest_repointed_names()
    uncovered = constants - repointed - set(ALLOWLIST)
    assert not uncovered, (
        "src/sonari/paths.py declares {0} but tests/conftest.py's "
        "_isolate_sonari_dir fixture does not repoint it (and it is not in "
        "this guard's ALLOWLIST in tests/test_paths_conftest_isolation.py). "
        "Either add monkeypatch.setattr(paths, \"<NAME>\", <tmp-path>, "
        "raising=False) to the fixture, or add the name to ALLOWLIST above "
        "with a one-line reason it must legitimately stay bound to the real "
        "path.".format(sorted(uncovered))
    )


def test_allowlist_entries_are_still_declared_in_paths_py():
    """Catches allowlist rot: an entry for a constant that no longer exists."""
    constants = _paths_py_constants()
    stale = set(ALLOWLIST) - constants
    assert not stale, (
        "ALLOWLIST references constants no longer declared in paths.py: "
        "{0} -- remove the stale entry.".format(sorted(stale))
    )
